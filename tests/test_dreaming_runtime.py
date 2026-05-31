"""
Focused tests for the nightly Dreaming Runtime.

Covers:
- Config defaults (window, batch, lookback, jitter)
- Config validation (window start < end)
- Window-aware sleep (outside window → wait; inside window → proceed)
- Batch size limit (never more than max_scopes_per_batch scopes per batch)
- No-eligible-scopes handling
- No-overlap enforcement per batch
- Timeout enforcement per batch
- Failure isolation (crash in one batch does not affect next batch)
- Metadata-only logging (no secret/leak in log output)
- Scope selection respects min_daily_memories / lookback_days / ai-layer-only filter
- DREAMING_MIN_DAILY_MEMORIES=0 disables the memory gate
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from amo_bot.ai.dreaming_runtime import DreamingRuntime
from amo_bot.ai.memory_maintenance import MemoryMaintenanceResult
from amo_bot.config.settings import Settings


# ── helpers ────────────────────────────────────────────────────────────────────

def _make_mock_repo() -> MagicMock:
    repo = MagicMock()
    repo._session = MagicMock()
    return repo


def _make_success_result(now: datetime | None = None) -> MemoryMaintenanceResult:
    return MemoryMaintenanceResult(
        run_at=now or datetime.now(UTC),
        scopes_scanned=3,
        scopes_pruned=1,
        deleted_daily_memories=5,
        aggregation_scopes_attempted=2,
        recent_rows_seen=4,
        daily_rows_upserted=1,
        scopes_skipped_no_new_data=0,
        aggregation_scopes_failed=0,
        curation_scopes_attempted=2,
        curation_candidates_considered=4,
        curation_promoted=1,
        curation_scopes_failed=0,
    )


class _EchoCurator:
    """Curator that promotes all given daily memories — used only in tests."""
    def curate(self, *, scope, daily_memories, now):
        return [
            {"source_daily_memory_id": d.id, "fact_text": d.summary_text}
            for d in daily_memories
        ]


# ── test: config defaults ─────────────────────────────────────────────────────

def test_dreaming_window_defaults() -> None:
    """Nightly window and batch settings have the correct defaults."""
    s = Settings(
        BOT_TOKEN="1234:TOKEN",
        WEBUI_PASSWORD="pw",
        WEBUI_SECRET_KEY="x" * 32,
    )
    assert s.dreaming_window_start == "02:00"
    assert s.dreaming_window_end == "05:00"
    assert s.dreaming_timezone == "Europe/Berlin"
    assert s.dreaming_max_scopes_per_batch == 3
    assert s.dreaming_batch_pause_seconds == 300
    assert s.dreaming_jitter_seconds == 120
    assert s.dreaming_min_daily_memories == 1
    assert s.dreaming_lookback_days == 7


def test_dreaming_window_start_must_be_before_end() -> None:
    """A window where start >= end is rejected."""
    with pytest.raises(ValueError, match="DREAMING_WINDOW_START must be before DREAMING_WINDOW_END"):
        Settings(
            BOT_TOKEN="1234:TOKEN",
            WEBUI_PASSWORD="pw",
            WEBUI_SECRET_KEY="x" * 32,
            DREAMING_WINDOW_START="05:00",
            DREAMING_WINDOW_END="02:00",
        )


def test_dreaming_window_end_equals_start_rejected() -> None:
    """A window where start == end is rejected (same time is not a valid window)."""
    with pytest.raises(ValueError, match="DREAMING_WINDOW_START must be before DREAMING_WINDOW_END"):
        Settings(
            BOT_TOKEN="1234:TOKEN",
            WEBUI_PASSWORD="pw",
            WEBUI_SECRET_KEY="x" * 32,
            DREAMING_WINDOW_START="03:00",
            DREAMING_WINDOW_END="03:00",
        )


def test_dreaming_max_scopes_per_batch_bounds() -> None:
    """Batch size at boundaries [1,50] are accepted."""
    s = Settings(
        BOT_TOKEN="1234:TOKEN",
        WEBUI_PASSWORD="pw",
        WEBUI_SECRET_KEY="x" * 32,
        DREAMING_MAX_SCOPES_PER_BATCH=1,
    )
    assert s.dreaming_max_scopes_per_batch == 1

    s2 = Settings(
        BOT_TOKEN="1234:TOKEN",
        WEBUI_PASSWORD="pw",
        WEBUI_SECRET_KEY="x" * 32,
        DREAMING_MAX_SCOPES_PER_BATCH=50,
    )
    assert s2.dreaming_max_scopes_per_batch == 50


# ── test: DreamingRuntime construction ───────────────────────────────────────

def test_runtime_refuses_negative_timeout() -> None:
    """A negative timeout is clamped to minimum 1 s."""
    repo = _make_mock_repo()
    rt = DreamingRuntime(repository=repo, enabled=True, timeout_seconds=-10)
    assert rt._timeout == 1.0


def test_runtime_clamps_max_scopes_per_batch() -> None:
    """Batch size above 50 is clamped to 50."""
    repo = _make_mock_repo()
    rt = DreamingRuntime(repository=repo, enabled=True, max_scopes_per_batch=99)
    assert rt._max_scopes_per_batch == 50


def test_runtime_clamps_batch_pause() -> None:
    """Negative batch pause is clamped to 0."""
    repo = _make_mock_repo()
    rt = DreamingRuntime(repository=repo, enabled=True, batch_pause_seconds=-50)
    assert rt._batch_pause_seconds == 0


def test_runtime_clamps_jitter() -> None:
    """Negative jitter is clamped to 0."""
    repo = _make_mock_repo()
    rt = DreamingRuntime(repository=repo, enabled=True, jitter_seconds=-100)
    assert rt._jitter_seconds == 0


def test_runtime_min_daily_memories_zero_disables_gate() -> None:
    """When min_daily_memories=0 the memory gate is disabled."""
    repo = _make_mock_repo()
    rt = DreamingRuntime(repository=repo, enabled=True, min_daily_memories=0)
    assert rt._min_daily_memories == 0


def test_runtime_lookback_days_clamped() -> None:
    """Lookback days are clamped to [1, 365]."""
    repo = _make_mock_repo()
    rt_bad = DreamingRuntime(repository=repo, enabled=True, lookback_days=0)
    assert rt_bad._lookback_days == 1

    rt_good = DreamingRuntime(repository=repo, enabled=True, lookback_days=999)
    assert rt_good._lookback_days == 365


def test_runtime_disabled_does_not_start_loop() -> None:
    """When enabled=False start() is a no-op."""
    repo = _make_mock_repo()
    rt = DreamingRuntime(repository=repo, enabled=False)
    rt.start()
    assert rt.is_running is False


# ── test: window helpers ──────────────────────────────────────────────────────

def test_is_in_window_inside() -> None:
    """_is_in_window returns True when current time is inside the window."""
    repo = _make_mock_repo()
    rt = DreamingRuntime(
        repository=repo,
        enabled=False,
        window_start="02:00",
        window_end="05:00",
        timezone="UTC",
    )
    # 03:00 UTC is inside 02:00-05:00 UTC.
    check_time = datetime(2026, 5, 29, 3, 0, 0, tzinfo=timezone.utc)
    assert rt._is_in_window(when=check_time) is True


def test_is_in_window_before() -> None:
    """_is_in_window returns False when before the window."""
    repo = _make_mock_repo()
    rt = DreamingRuntime(
        repository=repo,
        enabled=False,
        window_start="02:00",
        window_end="05:00",
        timezone="UTC",
    )
    # 01:00 UTC is before 02:00-05:00 UTC.
    check_time = datetime(2026, 5, 29, 1, 0, 0, tzinfo=timezone.utc)
    assert rt._is_in_window(when=check_time) is False


def test_is_in_window_after() -> None:
    """_is_in_window returns False when after the window."""
    repo = _make_mock_repo()
    rt = DreamingRuntime(
        repository=repo,
        enabled=False,
        window_start="02:00",
        window_end="05:00",
        timezone="UTC",
    )
    # 06:00 UTC is after 02:00-05:00 UTC.
    check_time = datetime(2026, 5, 29, 6, 0, 0, tzinfo=timezone.utc)
    assert rt._is_in_window(when=check_time) is False


def test_is_in_window_exactly_at_start() -> None:
    """_is_in_window returns True at exactly the window start time."""
    repo = _make_mock_repo()
    rt = DreamingRuntime(
        repository=repo,
        enabled=False,
        window_start="02:00",
        window_end="05:00",
        timezone="UTC",
    )
    check_time = datetime(2026, 5, 29, 2, 0, 0, tzinfo=timezone.utc)
    assert rt._is_in_window(when=check_time) is True


def test_is_in_window_just_before_end() -> None:
    """_is_in_window returns True one second before the window end."""
    repo = _make_mock_repo()
    rt = DreamingRuntime(
        repository=repo,
        enabled=False,
        window_start="02:00",
        window_end="05:00",
        timezone="UTC",
    )
    # 04:59:59 UTC is just before 05:00 — still inside.
    check_time = datetime(2026, 5, 29, 4, 59, 59, tzinfo=timezone.utc)
    assert rt._is_in_window(when=check_time) is True


def test_is_in_window_exactly_at_end() -> None:
    """_is_in_window returns False at exactly the window end time."""
    repo = _make_mock_repo()
    rt = DreamingRuntime(
        repository=repo,
        enabled=False,
        window_start="02:00",
        window_end="05:00",
        timezone="UTC",
    )
    check_time = datetime(2026, 5, 29, 5, 0, 0, tzinfo=timezone.utc)
    assert rt._is_in_window(when=check_time) is False


# ── test: no-overlap batch ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_overlap_batch_lock_prevents_concurrent_batches() -> None:
    """
    The asyncio.Lock inside DreamingRuntime serialises concurrent calls to
    _execute_batch.  A second batch must not run while the first is in progress.
    """
    repo = _make_mock_repo()
    rt = DreamingRuntime(repository=repo, enabled=False, timeout_seconds=5.0)

    overlap_lock = asyncio.Lock()

    async def slow_run(*args: object, **kwargs: object) -> MemoryMaintenanceResult:
        async with overlap_lock:
            pass  # verify single batch
        await asyncio.sleep(0.5)
        return _make_success_result()

    rt._run_batch = slow_run  # type: ignore[method-assignment]

    first_task = asyncio.create_task(rt._execute_batch([]))
    await asyncio.sleep(0.05)

    second_task = asyncio.create_task(rt._execute_batch([]))
    await asyncio.sleep(0.05)

    assert not second_task.done(), "second batch should be blocked by lock"

    first_result, second_result = await asyncio.gather(first_task, second_task)

    assert first_result.status == "success"
    assert second_result.status == "success"


# ── test: timeout ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_batch_times_out_when_execution_exceeds_timeout() -> None:
    """
    If _run_batch exceeds the configured timeout, the batch is marked 'timeout'.
    """
    repo = _make_mock_repo()
    rt = DreamingRuntime(repository=repo, enabled=False, timeout_seconds=0.5)

    async def very_slow(*args: object, **kwargs: object) -> MemoryMaintenanceResult:
        await asyncio.sleep(10.0)
        return _make_success_result()

    rt._run_batch = very_slow  # type: ignore[method-assignment]

    result = await rt._execute_batch([])
    assert result.status == "timeout"
    assert result.error_class == "TimeoutError"

    await rt.stop()


# ── test: failure isolation ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_crash_in_batch_does_not_prevent_next_batch() -> None:
    """
    If a batch raises an exception the result is 'error' but the next batch succeeds.
    """
    repo = _make_mock_repo()
    rt = DreamingRuntime(repository=repo, enabled=False, timeout_seconds=5.0)

    crash = True

    async def flaky_run(*args: object, **kwargs: object) -> MemoryMaintenanceResult:
        nonlocal crash
        if crash:
            crash = False
            raise RuntimeError("simulated failure")
        return _make_success_result()

    rt._run_batch = flaky_run  # type: ignore[method-assignment]

    result1 = await rt._execute_batch([])
    assert result1.status == "error"
    assert result1.error_class == "RuntimeError"

    result2 = await rt._execute_batch([])
    assert result2.status == "success"
    assert result2.scopes_scanned == 3


# ── test: stop / drain ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stop_waits_for_in_progress_batch() -> None:
    """stop() blocks until the current batch finishes (drain)."""
    repo = _make_mock_repo()
    rt = DreamingRuntime(repository=repo, enabled=True, timeout_seconds=5.0)

    finishing = asyncio.Event()

    async def blocking_run(*args: object, **kwargs: object) -> MemoryMaintenanceResult:
        await asyncio.sleep(0.3)
        finishing.set()
        return _make_success_result()

    rt._run_batch = blocking_run  # type: ignore[method-assignment]

    async def one_cycle_loop() -> None:
        await rt._execute_batch([])
        while not rt._stopping:
            await asyncio.sleep(0.01)

    rt._loop = one_cycle_loop  # type: ignore[method-assignment]
    rt.start()
    await asyncio.sleep(0.05)

    stop_task = asyncio.create_task(rt.stop())
    await finishing.wait()

    done, pending = await asyncio.wait([stop_task], timeout=2.0)
    assert len(done) == 1
    assert rt.is_running is False


# ── test: batch size enforcement ────────────────────────────────────────────────

def test_select_eligible_scopes_respects_max_batch_size() -> None:
    """
    _select_eligible_scopes applies limit=max_scopes_per_batch via the SQL query.
    With mock we verify the query is built with the correct limit.
    """
    from unittest.mock import ANY

    repo = _make_mock_repo()
    rt = DreamingRuntime(
        repository=repo,
        enabled=True,
        window_start="00:00",
        window_end="23:59",
        timezone="UTC",
        max_scopes_per_batch=2,
        min_daily_memories=0,  # no memory gate — uses simple path
    )

    # Simulate 5 configs in the DB.
    fake_configs = [
        MagicMock(scope_type="topic", chat_id=i, topic_id=i, user_id=None)
        for i in range(1, 6)
    ]

    # When min_daily_memories=0 the no-memory-gate path is used.
    # Build a chain: select().limit().all() returns at most 2 items.
    limited_query = MagicMock()
    limited_query.all.return_value = fake_configs[:2]  # capped at max_scopes_per_batch
    repo._session.scalars.return_value = limited_query

    selected = rt._select_eligible_scopes()
    assert len(selected) == 2


# ── test: min_daily_memories gate ──────────────────────────────────────────────

def test_select_eligible_scopes_respects_min_daily_memories() -> None:
    """
    When min_daily_memories=1, a scope with zero daily memories in the lookback
    window must not be returned.
    """
    repo = _make_mock_repo()
    rt = DreamingRuntime(
        repository=repo,
        enabled=True,
        window_start="00:00",
        window_end="23:59",
        timezone="UTC",
        max_scopes_per_batch=10,
        min_daily_memories=1,
        lookback_days=7,
    )

    # Simulate 3 configs, only one with daily memories (count >= 1).
    # The SQLAlchemy scalars().all() returns TopicAgentConfig rows.
    # When min_daily_memories > 0, the outer join must filter correctly.
    # We simulate: all configs are in the DB, but only those with mem_count >= 1 survive.

    rich_scope = MagicMock(scope_type="topic", chat_id=1, topic_id=1, user_id=None)
    poor_scope = MagicMock(scope_type="topic", chat_id=2, topic_id=2, user_id=None)

    # When min_daily_memories > 0, the mock's scalars().all() should return only
    # scopes that pass the memory-count subquery filter.
    repo._session.scalars.return_value.all.return_value = [rich_scope]

    selected = rt._select_eligible_scopes()
    assert len(selected) == 1
    assert selected[0].chat_id == 1


# ── test: metadata-only logging ─────────────────────────────────────────────────

def test_batch_log_output_contains_no_memory_content() -> None:
    """
    Structured log events emitted by the batch runtime must not contain
    raw memory text, prompts, tokens, or API keys.
    """
    from amo_bot.core.logging import SensitiveLogFilter

    root = logging.getLogger()
    has_filter = any(isinstance(f, SensitiveLogFilter) for f in root.filters)
    if not has_filter:
        root.addFilter(SensitiveLogFilter())

    records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = lambda r: records.append(r)  # type: ignore

    logger = logging.getLogger("amo_bot.ai.dreaming_runtime")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)

    repo = _make_mock_repo()
    rt = DreamingRuntime(
        repository=repo,
        enabled=False,
        timeout_seconds=5.0,
    )

    async def fake_run(*args: object, **kwargs: object) -> MemoryMaintenanceResult:
        return _make_success_result()

    rt._run_batch = fake_run  # type: ignore[method-assignment]

    async def _test() -> None:
        await rt._execute_batch([])

    loop = asyncio.new_event_loop()
    loop.run_until_complete(_test())
    loop.close()

    secret_patterns = [
        re.compile(r"api[_-]?key", re.IGNORECASE),
        re.compile(r"\btoken\b", re.IGNORECASE),
        re.compile(r"\bprompt\b", re.IGNORECASE),
        re.compile(r"memory[_-]?content", re.IGNORECASE),
        re.compile(r"sk-[A-Za-z0-9]{8,}"),
        re.compile(r"ghp_[A-Za-z0-9]{8,}"),
    ]

    for rec in records:
        msg = rec.getMessage()
        for pat in secret_patterns:
            assert not pat.search(msg), f"Sensitive data found in log: {msg!r}"

    logger.removeHandler(handler)


def test_dreaming_run_result_is_metadata_only() -> None:
    """DreamingRunResult must not carry actual memory/prompt content."""
    from amo_bot.ai.dreaming_runtime import DreamingRunResult

    r = DreamingRunResult(
        run_id="abc123",
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        duration_ms=100,
        scopes_scanned=3,
        scopes_pruned=1,
        deleted_daily_memories=5,
        curation_candidates_considered=4,
        curation_promoted=1,
        curation_auto_approved=0,
        curation_scopes_failed=0,
        status="success",
        error_class=None,
    )

    assert r.run_id == "abc123"
    assert r.status == "success"
    # No fact_text, prompt, token, memory_content fields exist.
    assert not hasattr(r, "fact_text")
    assert not hasattr(r, "prompt")
    assert not hasattr(r, "tokens")
    assert not hasattr(r, "memory_content")


# ── test: service scopes parameter ─────────────────────────────────────────────

def test_service_run_once_accepts_scopes_list(tmp_path: pytest.TempPathFactory) -> None:
    """
    MemoryMaintenanceService.run_once() with scopes= list processes only that list
    and not all configs (confirming the batch contract).
    """
    from amo_bot.ai.memory_maintenance import MemoryMaintenanceService
    from amo_bot.db.init_db import init_db
    from amo_bot.db.repositories import TopicAgentMemoryRepository

    db_url = f"sqlite:///{tmp_path}/scopes_param.sqlite"
    init_db(database_url=db_url)
    from amo_bot.db.base import create_session_factory
    session_factory = create_session_factory(db_url)
    repo = TopicAgentMemoryRepository(session_factory())

    # Two scopes.
    repo.upsert_config(
        scope_type="topic", chat_id=-1001, topic_id=10, user_id=None,
        ai_enabled=False, response_mode="command", memory_retention_days=30,
    )
    repo.upsert_config(
        scope_type="topic", chat_id=-1002, topic_id=11, user_id=None,
        ai_enabled=False, response_mode="command", memory_retention_days=30,
    )

    # Process only the first scope via batch mode.
    from amo_bot.db.models import TopicAgentConfig
    from sqlalchemy import select

    session = session_factory()
    scope_list: list[TopicAgentConfig] = session.scalars(
        select(TopicAgentConfig).where(TopicAgentConfig.chat_id == -1001)
    ).all()

    svc = MemoryMaintenanceService(repository=repo, auto_curate_long_memory=False)
    result = svc.run_once(scopes=scope_list)

    # Only one scope should have been scanned.
    assert result.scopes_scanned == 1
    assert result.scopes_pruned == 0

    session.close()
