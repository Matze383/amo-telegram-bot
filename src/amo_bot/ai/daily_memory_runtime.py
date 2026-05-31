from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from amo_bot.ai.memory_maintenance import MemoryMaintenanceResult, MemoryMaintenanceService
from amo_bot.core.logging import duration_timer, log_event, set_run_id
from amo_bot.db.repositories import TopicAgentMemoryRepository


logger = logging.getLogger(__name__)
_COMPONENT = "daily-memory.runtime"


@dataclass(slots=True, frozen=True)
class DailyMemoryRunResult:
    run_id: str
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    scopes_scanned: int
    scopes_pruned: int
    deleted_daily_memories: int
    aggregation_scopes_attempted: int
    recent_rows_seen: int
    daily_rows_upserted: int
    scopes_skipped_no_new_data: int
    aggregation_scopes_failed: int
    status: str
    error_class: str | None


class DailyMemoryRuntime:
    def __init__(
        self,
        *,
        repository: TopicAgentMemoryRepository,
        enabled: bool = False,
        interval_seconds: int = 21600,
        timeout_seconds: float = 300.0,
        max_input_messages: int = 200,
        max_chars_per_message: int = 500,
        max_summary_chars: int = 6000,
        min_messages: int = 1,
        max_scopes_per_run: int = 10,
    ) -> None:
        self._repo = repository
        self._enabled = enabled
        self._interval_seconds = max(300, int(interval_seconds))
        self._timeout = max(1.0, float(timeout_seconds))
        self._max_input_messages = max(1, min(int(max_input_messages), 5000))
        self._max_chars_per_message = max(50, min(int(max_chars_per_message), 5000))
        self._max_summary_chars = max(500, min(int(max_summary_chars), 50000))
        self._min_messages = max(0, int(min_messages))
        self._max_scopes_per_run = max(1, min(int(max_scopes_per_run), 100))

        self._service = MemoryMaintenanceService(repository=self._repo, auto_curate_long_memory=False)
        self._running = False
        self._stopping = False
        self._task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None
        self._lock = asyncio.Lock()
        self._last_result: DailyMemoryRunResult | None = None

    def start(self) -> None:
        if not self._enabled or self._running:
            return
        loop = asyncio.get_running_loop()
        self._running = True
        self._stopping = False
        self._stop_event = asyncio.Event()
        self._task = loop.create_task(self._loop(), name="daily-memory-runtime-loop")

    async def stop(self) -> None:
        if not self._running:
            return
        self._stopping = True
        if self._stop_event is not None:
            self._stop_event.set()
        if self._task is not None:
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._running = False
        self._task = None
        self._stop_event = None

    async def _loop(self) -> None:
        while not self._stopping:
            await self.run_once()
            if self._stopping:
                break
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._interval_seconds)
                break
            except asyncio.TimeoutError:
                pass

    async def run_once(self) -> DailyMemoryRunResult:
        import uuid

        run_id = uuid.uuid4().hex[:16]
        started_at = datetime.now(UTC)
        token = set_run_id(run_id)
        try:
            async with self._lock:
                result: MemoryMaintenanceResult = await asyncio.wait_for(
                    asyncio.to_thread(
                        self._service.run_once,
                        daily_max_input_messages=self._max_input_messages,
                        daily_max_chars_per_message=self._max_chars_per_message,
                        daily_max_summary_chars=self._max_summary_chars,
                        daily_min_messages=self._min_messages,
                        daily_max_scopes=self._max_scopes_per_run,
                    ),
                    timeout=self._timeout,
                )
            now = datetime.now(UTC)
            report = DailyMemoryRunResult(
                run_id=run_id,
                started_at=started_at,
                finished_at=now,
                duration_ms=int((now - started_at).total_seconds() * 1000),
                scopes_scanned=result.scopes_scanned,
                scopes_pruned=result.scopes_pruned,
                deleted_daily_memories=result.deleted_daily_memories,
                aggregation_scopes_attempted=result.aggregation_scopes_attempted,
                recent_rows_seen=result.recent_rows_seen,
                daily_rows_upserted=result.daily_rows_upserted,
                scopes_skipped_no_new_data=result.scopes_skipped_no_new_data,
                aggregation_scopes_failed=result.aggregation_scopes_failed,
                status="success",
                error_class=None,
            )
            self._last_result = report
            return report
        except Exception as exc:  # noqa: BLE001
            now = datetime.now(UTC)
            report = DailyMemoryRunResult(
                run_id=run_id,
                started_at=started_at,
                finished_at=now,
                duration_ms=int((now - started_at).total_seconds() * 1000),
                scopes_scanned=0,
                scopes_pruned=0,
                deleted_daily_memories=0,
                aggregation_scopes_attempted=0,
                recent_rows_seen=0,
                daily_rows_upserted=0,
                scopes_skipped_no_new_data=0,
                aggregation_scopes_failed=0,
                status="error",
                error_class=type(exc).__name__,
            )
            self._last_result = report
            return report
        finally:
            set_run_id(None)

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    @property
    def last_result(self) -> DailyMemoryRunResult | None:
        return self._last_result
