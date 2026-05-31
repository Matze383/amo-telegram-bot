from __future__ import annotations

from datetime import UTC, datetime

import pytest

from amo_bot.ai.daily_memory_runtime import DailyMemoryRuntime
from amo_bot.ai.memory_maintenance import MemoryMaintenanceResult


class _DummyRepo:
    pass


class _DummyService:
    def __init__(self, result: MemoryMaintenanceResult) -> None:
        self._result = result
        self.calls: list[dict[str, int]] = []

    def run_once(self, **kwargs):  # noqa: ANN003
        self.calls.append(kwargs)
        return self._result


def _result() -> MemoryMaintenanceResult:
    return MemoryMaintenanceResult(
        run_at=datetime.now(UTC),
        scopes_scanned=2,
        scopes_pruned=1,
        deleted_daily_memories=3,
        aggregation_scopes_attempted=2,
        recent_rows_seen=200,
        daily_rows_upserted=2,
        scopes_skipped_no_new_data=0,
        aggregation_scopes_failed=0,
        curation_scopes_attempted=0,
        curation_candidates_considered=0,
        curation_promoted=0,
        curation_scopes_failed=0,
    )


@pytest.mark.asyncio
async def test_daily_runtime_run_once_metadata_only_and_no_long_curation() -> None:
    rt = DailyMemoryRuntime(
        repository=_DummyRepo(),
        enabled=True,
        max_input_messages=123,
        max_chars_per_message=321,
        max_summary_chars=2222,
        min_messages=2,
        max_scopes_per_run=7,
    )
    svc = _DummyService(_result())
    rt._service = svc  # type: ignore[assignment]

    res = await rt.run_once()

    assert res.status == "success"
    assert res.error_class is None
    assert res.scopes_scanned == 2
    assert res.daily_rows_upserted == 2
    assert not hasattr(res, "summary_text")

    assert len(svc.calls) == 1
    kwargs = svc.calls[0]
    assert kwargs["daily_max_input_messages"] == 123
    assert kwargs["daily_max_chars_per_message"] == 321
    assert kwargs["daily_max_summary_chars"] == 2222
    assert kwargs["daily_min_messages"] == 2
    assert kwargs["daily_max_scopes"] == 7


@pytest.mark.asyncio
async def test_daily_runtime_start_stop_loop_runs_outside_dream_window_conceptually() -> None:
    rt = DailyMemoryRuntime(repository=_DummyRepo(), enabled=True, interval_seconds=300)
    svc = _DummyService(_result())
    rt._service = svc  # type: ignore[assignment]

    rt.start()
    assert rt.is_running is True
    await rt.stop()
    assert rt.is_running is False
