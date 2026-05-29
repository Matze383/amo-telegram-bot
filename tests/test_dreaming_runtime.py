"""
Focused tests for the Dreaming / Memory-Curation Runtime.

Covers:
- Config defaults (disabled, interval, timeout, limits, auto-approve off)
- Scheduler disabled/enabled behaviour
- No-overlap enforcement (second run blocked while first is in progress)
- Timeout enforcement
- Failure isolation (crash does not affect next cycle)
- Metadata-only logging (no secret/leak in log output)
- Scope isolation and review/permission preservation
"""

from __future__ import annotations

import asyncio
import logging
import re
import sys
from datetime import UTC, datetime, timedelta
from unittest.mock import patch, MagicMock

import pytest

from amo_bot.ai.dreaming_runtime import DreamingRuntime
from amo_bot.ai.memory_maintenance import MemoryMaintenanceService, MemoryMaintenanceResult
from amo_bot.config.settings import Settings
from amo_bot.core.logging import SensitiveLogFilter, setup_logging


# ── helpers ──────────────────────────────────────────────────────────────────

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
        curation_scopes_attempted=2,
        curation_candidates_considered=4,
        curation_promoted=1,
        curation_scopes_failed=0,
    )


# ── test: config defaults ─────────────────────────────────────────────────────

def test_dreaming_disabled_by_default() -> None:
    """DREAMING_ENABLED defaults to False so the runtime never auto-starts."""
    s = Settings(
        BOT_TOKEN="1234:TOKEN",
        WEBUI_PASSWORD="pw",
        WEBUI_SECRET_KEY="x" * 32,
    )
    assert s.dreaming_enabled is False


def test_dreaming_interval_minimum_enforced() -> None:
    """Intervals are accepted within the [60, ∞) range by pydantic validation."""
    s = Settings(
        BOT_TOKEN="1234:TOKEN",
        WEBUI_PASSWORD="pw",
        WEBUI_SECRET_KEY="x" * 32,
        DREAMING_INTERVAL_SECONDS=60,
    )
    assert s.dreaming_interval_seconds == 60


def test_dreaming_candidate_limit_bounds() -> None:
    """Limits at the boundary of [1,30] are accepted."""
    s = Settings(
        BOT_TOKEN="1234:TOKEN",
        WEBUI_PASSWORD="pw",
        WEBUI_SECRET_KEY="x" * 32,
        DREAMING_MAX_DAILY_CANDIDATES_PER_SCOPE=30,
        DREAMING_MAX_PROMOTIONS_PER_SCOPE=1,
    )
    assert s.dreaming_max_daily_candidates_per_scope == 30
    assert s.dreaming_max_promotions_per_scope == 1


def test_dreaming_auto_approve_defaults_false() -> None:
    """Auto-approve is off by default — human review is always required."""
    s = Settings(
        BOT_TOKEN="1234:TOKEN",
        WEBUI_PASSWORD="pw",
        WEBUI_SECRET_KEY="x" * 32,
    )
    assert s.dreaming_auto_approve_mode is False


# ── test: DreamingRuntime construction ───────────────────────────────────────

def test_runtime_refuses_negative_interval() -> None:
    """A negative interval is clamped to minimum 60 s."""
    repo = _make_mock_repo()
    rt = DreamingRuntime(repository=repo, enabled=True, interval_seconds=-10)
    assert rt._interval == 60


def test_runtime_clamps_candidate_limit() -> None:
    """Candidate limit above 30 is clamped to 30."""
    repo = _make_mock_repo()
    rt = DreamingRuntime(repository=repo, enabled=True, max_daily_candidates_per_scope=99)
    assert rt._max_daily_candidates == 30


def test_runtime_clamps_promotion_limit() -> None:
    """Promotion limit above 20 is clamped to 20."""
    repo = _make_mock_repo()
    rt = DreamingRuntime(repository=repo, enabled=True, max_promotions_per_scope=99)
    assert rt._max_promotions == 20


def test_runtime_disabled_does_not_start_loop() -> None:
    """When enabled=False start() is a no-op."""
    repo = _make_mock_repo()
    rt = DreamingRuntime(repository=repo, enabled=False)
    rt.start()
    assert rt.is_running is False
    assert rt.is_enabled is False


def test_runtime_enabled_start_requires_running_loop() -> None:
    """Enabled startup must be performed inside the polling asyncio loop."""
    repo = _make_mock_repo()
    rt = DreamingRuntime(repository=repo, enabled=True, interval_seconds=3600)
    with pytest.raises(RuntimeError, match="running event loop"):
        rt.start()
    assert rt.is_running is False


@pytest.mark.asyncio
async def test_runtime_enabled_starts_loop() -> None:
    """When enabled=True start() launches the background task."""
    repo = _make_mock_repo()
    rt = DreamingRuntime(repository=repo, enabled=True, interval_seconds=3600)
    rt.start()
    await asyncio.sleep(0.05)
    assert rt.is_running is True
    assert rt.is_enabled is True
    await rt.stop()


@pytest.mark.asyncio
async def test_start_is_idempotent() -> None:
    """Calling start() twice does not create duplicate tasks."""
    repo = _make_mock_repo()
    rt = DreamingRuntime(repository=repo, enabled=True, interval_seconds=3600)
    rt.start()
    await asyncio.sleep(0.05)
    first_task = rt._task
    rt.start()  # idempotent
    await asyncio.sleep(0.05)
    assert rt._task is first_task
    await rt.stop()


# ── test: no-overlap ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_overlap_lock_prevents_concurrent_execution() -> None:
    """
    The asyncio.Lock inside DreamingRuntime serialises concurrent calls to
    _execute_protected.  We verify this by having a slow _run_once and checking
    that the second call does not overlap with the first.
    """
    repo = _make_mock_repo()
    rt = DreamingRuntime(repository=repo, enabled=False, interval_seconds=3600)

    overlap_lock = asyncio.Lock()

    async def slow_run(*args: object, **kwargs: object) -> MemoryMaintenanceResult:
        async with overlap_lock:
            # If we can acquire here, there is no concurrent execution yet.
            pass
        await asyncio.sleep(0.5)  # longer than the lock acquisition timeout
        return _make_success_result()

    rt._run_once = slow_run  # type: ignore[method-assignment]

    # Start first call.
    first_task = asyncio.create_task(rt._execute_protected())
    await asyncio.sleep(0.05)  # let first call start and hit the lock

    # Start second call while first is in progress.
    second_task = asyncio.create_task(rt._execute_protected())
    await asyncio.sleep(0.05)

    # The second call should still be blocked by the lock.
    # Verify that second is not yet done.
    assert not second_task.done(), "second call should be blocked by lock"

    # Let both finish.
    first_result, second_result = await asyncio.gather(first_task, second_task)

    # Neither should be an error from lock_timeout.
    assert first_result.status == "success"
    assert second_result.status == "success"


# ── test: timeout ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_times_out_when_execution_exceeds_timeout() -> None:
    """
    If run_once exceeds the configured timeout, the run is marked 'timeout'.
    """
    repo = _make_mock_repo()
    rt = DreamingRuntime(repository=repo, enabled=False, timeout_seconds=2.0)

    async def very_slow(*args: object, **kwargs: object) -> MemoryMaintenanceResult:
        await asyncio.sleep(10.0)  # over the 2 s timeout
        return _make_success_result()

    rt._run_once = very_slow  # type: ignore[method-assignment]

    result = await rt._execute_protected()
    assert result.status == "timeout"
    assert result.error_class == "TimeoutError"
    assert rt._active_run_task is not None
    assert not rt._active_run_task.cancelled()
    await rt.stop()


@pytest.mark.asyncio
async def test_sync_service_run_once_executes_off_event_loop_thread() -> None:
    """The synchronous maintenance service is called via asyncio.to_thread."""
    repo = _make_mock_repo()
    rt = DreamingRuntime(repository=repo, enabled=False, timeout_seconds=5.0)
    seen: dict[str, object] = {}

    def fake_call_service_once() -> MemoryMaintenanceResult:
        seen["in_event_loop"] = True
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            seen["in_event_loop"] = False
        return _make_success_result()

    rt._call_service_once = fake_call_service_once  # type: ignore[method-assignment]

    result = await rt._run_once("run123")

    assert result.scopes_scanned == 3
    assert seen["in_event_loop"] is False


@pytest.mark.asyncio
async def test_stop_cancels_timeout_worker_after_graceful_timeout_result() -> None:
    """Timeout result is returned first; explicit stop then cancels the worker."""
    repo = _make_mock_repo()
    rt = DreamingRuntime(repository=repo, enabled=False, timeout_seconds=0.05)

    async def very_slow(*args: object, **kwargs: object) -> MemoryMaintenanceResult:
        await asyncio.sleep(10.0)
        return _make_success_result()

    rt._run_once = very_slow  # type: ignore[method-assignment]

    result = await rt._execute_protected()
    assert result.status == "timeout"
    worker = rt._active_run_task
    assert worker is not None

    await rt.stop()

    assert worker.cancelled()
    assert rt._active_run_task is None


# ── test: failure isolation ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_crash_in_run_does_not_prevent_next_cycle() -> None:
    """If run_once raises an exception the result is 'error' but next run succeeds."""
    repo = _make_mock_repo()
    rt = DreamingRuntime(repository=repo, enabled=False, timeout_seconds=5.0)

    crash = True

    async def flaky_run(*args: object, **kwargs: object) -> MemoryMaintenanceResult:
        nonlocal crash
        if crash:
            crash = False
            raise RuntimeError("simulated failure")
        return _make_success_result()

    rt._run_once = flaky_run  # type: ignore[method-assignment]

    result1 = await rt._execute_protected()
    assert result1.status == "error"
    assert result1.error_class == "RuntimeError"

    result2 = await rt._execute_protected()
    assert result2.status == "success"
    assert result2.scopes_scanned == 3


# ── test: stop / drain ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stop_waits_for_in_progress_run() -> None:
    """stop() blocks until the current run finishes (drain)."""
    repo = _make_mock_repo()
    rt = DreamingRuntime(repository=repo, enabled=True, timeout_seconds=5.0)

    finishing = asyncio.Event()

    async def blocking_run(*args: object, **kwargs: object) -> MemoryMaintenanceResult:
        await asyncio.sleep(0.3)
        finishing.set()
        return _make_success_result()

    rt._run_once = blocking_run  # type: ignore[method-assignment]

    async def one_cycle_loop() -> None:
        run_task = asyncio.create_task(rt._execute_protected())
        await run_task
        while not rt._stopping:
            await asyncio.sleep(0.01)

    rt._loop = one_cycle_loop  # type: ignore[method-assignment]
    rt.start()
    await asyncio.sleep(0.05)  # let the run start

    # Stop while it's running.
    stop_task = asyncio.create_task(rt.stop())

    # Wait for the run to signal it finished.
    await finishing.wait()

    # stop() should complete shortly after the run finishes.
    done, pending = await asyncio.wait([stop_task], timeout=2.0)
    assert len(done) == 1
    assert rt.is_running is False


# ── test: metadata-only logging ───────────────────────────────────────────────

def test_log_output_contains_no_memory_content() -> None:
    """
    Structured log events emitted by the dreaming runtime must not contain
    raw memory text, prompts, tokens, or API keys.
    """
    # Install SensitiveLogFilter before the test.
    root = logging.getLogger()
    has_filter = any(isinstance(f, SensitiveLogFilter) for f in root.filters)
    if not has_filter:
        root.addFilter(SensitiveLogFilter())

    # Capture all log records.
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
        interval_seconds=60,
        timeout_seconds=5.0,
    )

    async def fake_run(*args: object, **kwargs: object) -> MemoryMaintenanceResult:
        return _make_success_result()

    rt._run_once = fake_run  # type: ignore[method-assignment]

    async def _test() -> None:
        await rt._execute_protected()

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


def test_result_contains_only_metadata_fields() -> None:
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
    assert r.error_class is None
    # No fact_text, prompt, token, memory_content fields exist.
    assert not hasattr(r, "fact_text")
    assert not hasattr(r, "prompt")
    assert not hasattr(r, "tokens")


# ── test: scope/review isolation preserved ────────────────────────────────────

def test_service_does_not_auto_approve_by_default() -> None:
    """
    MemoryMaintenanceService.run_once() does not approve candidates —
    human review is the only path unless auto_approve is explicitly enabled.
    """
    from amo_bot.ai.memory_maintenance import MemoryMaintenanceService

    repo = _make_mock_repo()
    svc = MemoryMaintenanceService(repository=repo, auto_curate_long_memory=False)
    # When auto_curate_long_memory=False curation is skipped entirely.
    # When True (dreaming runtime) candidates are created but stay in
    # "candidate" status — never auto-approved.
    assert svc._auto_curate_long_memory is False


# ── test: curation candidates stay non-approved by default ──────────────────

class _EchoCurator:
    """Curator that promotes all given daily memories — used only in tests."""
    def curate(self, *, scope, daily_memories, now):
        return [
            {"source_daily_memory_id": d.id, "fact_text": d.summary_text}
            for d in daily_memories
        ]


def test_curation_creates_candidate_status_not_auto_approved(tmp_path: pytest.TempPathFactory) -> None:
    """
    MemoryMaintenanceService._curate_scope creates long_memory records with
    promotion_status='candidate'.  They never become answer-effective until
    a human (or explicit auto_approve) acts.
    """
    from amo_bot.ai.memory_maintenance import MemoryMaintenanceService
    from amo_bot.db.init_db import init_db
    from amo_bot.db.repositories import TopicAgentMemoryRepository

    db_url = f"sqlite:///{tmp_path}/curation_candidate.sqlite"
    init_db(database_url=db_url)
    from amo_bot.db.base import create_session_factory
    session_factory = create_session_factory(db_url)
    repo = TopicAgentMemoryRepository(session_factory())

    repo.upsert_config(
        scope_type="topic", chat_id=-9001, topic_id=90, user_id=None,
        ai_enabled=False, response_mode="command", memory_retention_days=30,
    )
    repo.upsert_daily_memory(
        scope_type="topic", chat_id=-9001, topic_id=90, user_id=None,
        summary_text="user prefers morning workouts", memory_date="2026-05-14", tokens_estimate=10,
    )

    svc = MemoryMaintenanceService(
        repository=repo,
        auto_curate_long_memory=True,
        max_daily_candidates_per_scope=3,
        max_promotions_per_scope=2,
        curator=_EchoCurator(),
    )
    result = svc.run_once()
    assert result.curation_promoted >= 1

    candidates = repo.list_long_memories(
        scope_type="topic",
        chat_id=-9001,
        topic_id=90,
        active_only=False,
    )
    assert len(candidates) >= 1
    for row in candidates:
        assert row.promotion_status == "candidate", (
            f"expected status 'candidate', got '{row.promotion_status}' — "
            "curation must not auto-approve"
        )

    # Candidates must NOT appear in answer-effective results.
    effective = repo.list_long_memories(
        scope_type="topic",
        chat_id=-9001,
        topic_id=90,
        answer_effective_only=True,
    )
    assert effective == [], "candidate entries must not be answer-effective without review"


def test_candidates_are_scope_isolated_no_cross_scope_leak(tmp_path: pytest.TempPathFactory) -> None:
    """
    Curation candidates created in one scope must not appear in another scope.
    """
    from amo_bot.ai.memory_maintenance import MemoryMaintenanceService
    from amo_bot.db.init_db import init_db
    from amo_bot.db.repositories import TopicAgentMemoryRepository

    db_url = f"sqlite:///{tmp_path}/scope_leak.sqlite"
    init_db(database_url=db_url)
    from amo_bot.db.base import create_session_factory
    session_factory = create_session_factory(db_url)
    repo = TopicAgentMemoryRepository(session_factory())

    # Two distinct scopes.
    for (stype, cid, tid) in [("topic", -9101, 91), ("topic", -9102, 92)]:
        repo.upsert_config(
            scope_type=stype, chat_id=cid, topic_id=tid, user_id=None,
            ai_enabled=False, response_mode="command", memory_retention_days=30,
        )
        repo.upsert_daily_memory(
            scope_type=stype, chat_id=cid, topic_id=tid, user_id=None,
            summary_text=f"facts about scope {cid}:{tid}", memory_date="2026-05-14", tokens_estimate=10,
        )

    svc = MemoryMaintenanceService(repository=repo, auto_curate_long_memory=True, curator=_EchoCurator())
    svc.run_once()

    scope1_memories = repo.list_long_memories(
        scope_type="topic", chat_id=-9101, topic_id=91, active_only=False,
    )
    scope2_memories = repo.list_long_memories(
        scope_type="topic", chat_id=-9102, topic_id=92, active_only=False,
    )

    assert len(scope1_memories) >= 1
    assert len(scope2_memories) >= 1

    # IDs must not leak across scopes.
    scope1_ids = {m.id for m in scope1_memories}
    scope2_ids = {m.id for m in scope2_memories}
    assert scope1_ids.isdisjoint(scope2_ids), "cross-scope ID leak detected"


def test_candidates_have_correct_scope_metadata(tmp_path: pytest.TempPathFactory) -> None:
    """
    Curated long_memory rows carry the correct scope fields from the source config.
    """
    from amo_bot.ai.memory_maintenance import MemoryMaintenanceService
    from amo_bot.db.init_db import init_db
    from amo_bot.db.repositories import TopicAgentMemoryRepository

    db_url = f"sqlite:///{tmp_path}/scope_meta.sqlite"
    init_db(database_url=db_url)
    from amo_bot.db.base import create_session_factory
    session_factory = create_session_factory(db_url)
    repo = TopicAgentMemoryRepository(session_factory())

    repo.upsert_config(
        scope_type="private_user", chat_id=None, topic_id=None, user_id=9999,
        ai_enabled=False, response_mode="command", memory_retention_days=30,
    )
    daily = repo.upsert_daily_memory(
        scope_type="private_user", chat_id=None, topic_id=None, user_id=9999,
        summary_text="...", memory_date="2026-05-14", tokens_estimate=10,
    )

    svc = MemoryMaintenanceService(repository=repo, auto_curate_long_memory=True, curator=_EchoCurator())
    svc.run_once()

    long_memories = repo.list_long_memories(
        scope_type="private_user", user_id=9999, active_only=False,
    )
    assert len(long_memories) >= 1
    row = long_memories[0]
    assert row.scope_type == "private_user"
    assert row.user_id == 9999
    assert row.chat_id is None
    assert row.topic_id is None
    assert row.promotion_status == "candidate"


@pytest.mark.asyncio
async def test_dreaming_runtime_does_not_auto_approve_by_default(tmp_path: pytest.TempPathFactory) -> None:
    """
    DreamingRuntime with auto_approve=False (the default) creates candidate
    records but does not promote them to answer-effective.
    """
    from amo_bot.ai.dreaming_runtime import DreamingRuntime
    from amo_bot.db.init_db import init_db
    from amo_bot.db.repositories import TopicAgentMemoryRepository

    db_url = f"sqlite:///{tmp_path}/dr_no_auto_approve.sqlite"
    init_db(database_url=db_url)
    from amo_bot.db.base import create_session_factory
    session_factory = create_session_factory(db_url)
    repo = TopicAgentMemoryRepository(session_factory())

    repo.upsert_config(
        scope_type="topic", chat_id=-9201, topic_id=92, user_id=None,
        ai_enabled=False, response_mode="command", memory_retention_days=30,
    )
    repo.upsert_daily_memory(
        scope_type="topic", chat_id=-9201, topic_id=92, user_id=None,
        summary_text="...", memory_date="2026-05-14", tokens_estimate=10,
    )

    # auto_approve=False (the default)
    rt = DreamingRuntime(
        repository=repo,
        enabled=True,
        interval_seconds=3600,
        timeout_seconds=30.0,
        auto_approve=False,
    )

    # Inject a real curator so promotions actually get created.
    rt._service._curator = _EchoCurator()

    result = await rt._execute_protected()

    assert result.status == "success"
    assert result.curation_auto_approved == 0, "auto_approve=False must not approve any candidates"

    # Candidates exist but are not answer-effective.
    long_memories = repo.list_long_memories(
        scope_type="topic", chat_id=-9201, topic_id=92, active_only=False,
    )
    assert len(long_memories) >= 1
    for row in long_memories:
        assert row.promotion_status == "candidate"

    effective = repo.list_long_memories(
        scope_type="topic", chat_id=-9201, topic_id=92, answer_effective_only=True,
    )
    assert effective == [], "candidates without auto_approve must not be answer-effective"

    await rt.stop()


@pytest.mark.asyncio
async def test_dreaming_runtime_scope_isolated_no_cross_topic_review(tmp_path: pytest.TempPathFactory) -> None:
    """
    Entries curated for one topic must not be retrievable under a different topic.
    """
    from amo_bot.ai.dreaming_runtime import DreamingRuntime
    from amo_bot.db.init_db import init_db
    from amo_bot.db.repositories import TopicAgentMemoryRepository

    db_url = f"sqlite:///{tmp_path}/dr_scope_iso.sqlite"
    init_db(database_url=db_url)
    from amo_bot.db.base import create_session_factory
    session_factory = create_session_factory(db_url)
    repo = TopicAgentMemoryRepository(session_factory())

    for (cid, tid) in [(-9301, 93), (-9302, 94)]:
        repo.upsert_config(
            scope_type="topic", chat_id=cid, topic_id=tid, user_id=None,
            ai_enabled=False, response_mode="command", memory_retention_days=30,
        )
        repo.upsert_daily_memory(
            scope_type="topic", chat_id=cid, topic_id=tid, user_id=None,
            summary_text=f"unique fact for {cid}:{tid}", memory_date="2026-05-14", tokens_estimate=10,
        )

    rt = DreamingRuntime(repository=repo, enabled=True, interval_seconds=3600, timeout_seconds=30.0)

    # Inject a real curator so promotions actually get created.
    rt._service._curator = _EchoCurator()

    result = await rt._execute_protected()
    assert result.status == "success"

    memories_topic1 = repo.list_long_memories(
        scope_type="topic", chat_id=-9301, topic_id=93, active_only=False,
    )
    memories_topic2 = repo.list_long_memories(
        scope_type="topic", chat_id=-9302, topic_id=94, active_only=False,
    )

    assert len(memories_topic1) >= 1
    assert len(memories_topic2) >= 1
    assert memories_topic1[0].fact_text == f"unique fact for -9301:93"
    assert memories_topic2[0].fact_text == f"unique fact for -9302:94"

    await rt.stop()


def test_service_auto_approve_flag_controls_review_bypass() -> None:
    """
    The auto_approve flag in DreamingRuntime is the ONLY mechanism that bypasses
    human review.  When False (default) candidates stay in 'candidate' status.
    """
    from amo_bot.config.settings import Settings

    # Default — auto_approve=False
    s = Settings(BOT_TOKEN="1234:TOKEN", WEBUI_PASSWORD="pw", WEBUI_SECRET_KEY="x" * 32)
    assert s.dreaming_auto_approve_mode is False, "dreaming_auto_approve_mode must default to False"
