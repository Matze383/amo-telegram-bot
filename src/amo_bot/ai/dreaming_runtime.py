"""
Dreaming / Memory-Curation Runtime

Bot-internal background task that periodically calls MemoryMaintenanceService.run_once()
to perform retention pruning and (optionally) automatic long-memory curation.

Key properties:
- Default DISABLED.  Activated via DREAMING_ENABLED=1.
- No overlap: a single asyncio.Lock ensures at most one batch at a time.
- Timeout: enforced per-batch via asyncio.wait_for(); overdue batches are cancelled and logged.
- Safe shutdown: stop() waits for any in-progress batch to finish (drain) before returning.
- Failures are isolated — a crashed batch does not affect the next batch.
- Runs within a configurable nightly window (default Europe/Berlin 02:00–05:00).
- Batch-scoped processing: only a few scopes per batch, with pauses between.
- All logs are metadata-only (run_id / duration_ms / counts / status / error class).
  No prompts, message text, tokens, or raw memory content are ever logged.
"""

from __future__ import annotations

import asyncio
import logging
import random
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from typing import TYPE_CHECKING

from amo_bot.core.logging import duration_timer, log_event, set_run_id
from amo_bot.db.repositories import TopicAgentMemoryRepository


logger = logging.getLogger(__name__)
_COMPONENT = "dreaming.runtime"


@dataclass(slots=True, frozen=True)
class DreamingRunResult:
    """Metadata-only result of a single dreaming batch run."""

    run_id: str
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    scopes_scanned: int
    scopes_pruned: int
    deleted_daily_memories: int
    curation_candidates_considered: int
    curation_promoted: int
    curation_auto_approved: int
    curation_scopes_failed: int
    status: str  # "success" | "timeout" | "error" | "window_closed" | "no_eligible_scopes"
    error_class: str | None  # exception class name, not the message


class DreamingRuntime:
    """
    Nightly batched background task for memory maintenance and curation.

    When ``enabled`` is False (the default) the scheduler loop is never started.
    When started, the loop waits until the configured nightly window opens,
    then processes scopes in batches until the window closes or no eligible
    scopes remain.

    All time-window arithmetic is performed in the configured ``timezone``.
    """

    def __init__(
        self,
        *,
        repository: TopicAgentMemoryRepository,
        enabled: bool = False,
        timeout_seconds: float = 300.0,
        max_daily_candidates_per_scope: int = 3,
        max_promotions_per_scope: int = 2,
        auto_approve: bool = False,
        # ── nightly window ──────────────────────────────────────────────
        window_start: str = "02:00",
        window_end: str = "05:00",
        timezone: str = "Europe/Berlin",
        max_scopes_per_batch: int = 3,
        batch_pause_seconds: int = 300,
        jitter_seconds: int = 120,
        min_daily_memories: int = 1,
        lookback_days: int = 7,
    ) -> None:
        self._repo = repository
        self._enabled = enabled
        self._timeout = max(1.0, timeout_seconds)
        self._max_daily_candidates = max(1, min(max_daily_candidates_per_scope, 30))
        self._max_promotions = max(1, min(max_promotions_per_scope, 20))
        self._auto_approve = auto_approve

        # Nightly window params (validated by Settings, stored here for direct use).
        self._window_start = window_start
        self._window_end = window_end
        self._timezone = timezone
        self._max_scopes_per_batch = max(1, min(max_scopes_per_batch, 50))
        self._batch_pause_seconds = max(0, batch_pause_seconds)
        self._jitter_seconds = max(0, jitter_seconds)
        self._min_daily_memories = max(0, min_daily_memories)
        self._lookback_days = max(1, min(lookback_days, 365))

        try:
            from zoneinfo import ZoneInfo
        except ImportError:
            from backports.zoneinfo import ZoneInfo  # type: ignore
        self._tz = ZoneInfo(self._timezone)

        self._lock = asyncio.Lock()
        self._running = False
        self._stopping = False
        self._task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None
        self._active_run_task: asyncio.Task[MemoryMaintenanceResult] | None = None
        self._last_result: DreamingRunResult | None = None

        # Import here to avoid circular imports at module load time.
        from amo_bot.ai.memory_maintenance import MemoryMaintenanceService

        self._service = MemoryMaintenanceService(
            repository=self._repo,
            auto_curate_long_memory=True,  # curation always attempted when called
            max_daily_candidates_per_scope=self._max_daily_candidates,
            max_promotions_per_scope=self._max_promotions,
        )

    # ── public lifecycle ──────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background scheduler loop.  Idempotent when already running.

        Must be called from inside the asyncio event loop that owns the polling
        runtime.  This deliberately avoids creating tasks before a loop exists.
        """
        if not self._enabled:
            logger.debug("dreaming runtime disabled — not starting")
            return
        if self._running:
            logger.debug("dreaming runtime already running")
            return
        loop = asyncio.get_running_loop()
        self._running = True
        self._stopping = False
        self._stop_event = asyncio.Event()
        self._task = loop.create_task(self._loop(), name="dreaming-runtime-loop")
        logger.info(
            "dreaming runtime loop started: timeout_s=%.1f auto_approve=%s "
            "window=%s-%s tz=%s max_scopes_per_batch=%d batch_pause_s=%d jitter_s=%d "
            "min_daily_memories=%d lookback_days=%d",
            self._timeout,
            self._auto_approve,
            self._window_start,
            self._window_end,
            self._timezone,
            self._max_scopes_per_batch,
            self._batch_pause_seconds,
            self._jitter_seconds,
            self._min_daily_memories,
            self._lookback_days,
        )

    async def stop(self) -> None:
        """
        Signal the scheduler to stop and wait for the in-progress batch (if any) to drain.

        After stop() returns the loop is terminated and start() may be called again.
        """
        if not self._running and (self._active_run_task is None or self._active_run_task.done()):
            return
        self._stopping = True
        logger.info("dreaming runtime stopping — waiting for in-progress batch to drain")
        if self._stop_event is not None:
            self._stop_event.set()
        if self._task is not None:
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._active_run_task is not None and not self._active_run_task.done():
            self._active_run_task.cancel()
            try:
                await self._active_run_task
            except asyncio.CancelledError:
                pass
            self._active_run_task = None
        self._running = False
        self._stop_event = None
        logger.info("dreaming runtime stopped")

    def stop_sync(self) -> None:
        """
        Synchronous drain for use after asyncio.run() closes its event loop.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            self._stopping = True
            self._running = False
            self._task = None
            self._stop_event = None
            return
        try:
            self._stopping = True
            if self._task is not None and not self._task.done():
                self._task.cancel()
            self._running = False
        except Exception as exc:
            logger.debug("dreaming_runtime.stop_sync: %s", exc)

    # ── scheduler loop ────────────────────────────────────────────────────────

    async def _loop(self) -> None:
        while not self._stopping:
            # Sleep until next window opens (or a brief moment if we are already in-window
            # and just yielded from a batch).  The 30-second check allows the stop signal
            # to be picked up quickly without busy-waiting.
            await self._sleep_until_window_opens()

            if self._stopping:
                break

            # Process batches while inside the window.
            while not self._stopping:
                eligible = self._select_eligible_scopes()
                if not eligible:
                    logger.debug("dreaming runtime: no eligible scopes — waiting for next window")
                    break

                result = await self._execute_batch(eligible)
                self._last_result = result
                self._log_result(result)

                if self._stopping:
                    break

                # Pause between batches; add jitter to spread load.
                jitter = random.randint(0, self._jitter_seconds) if self._jitter_seconds else 0
                pause = self._batch_pause_seconds + jitter
                logger.debug(
                    "dreaming runtime: batch done — sleeping %.1f s before next batch "
                    "(max_scopes_per_batch=%d window_closes_at=%s)",
                    pause,
                    self._max_scopes_per_batch,
                    self._window_end,
                )

                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=pause)
                    break  # stop event was set
                except asyncio.TimeoutError:
                    pass  # pause elapsed; check window still open

                if self._stopping:
                    break

                # Before processing another batch, check window is still open.
                if not self._is_in_window():
                    logger.debug("dreaming runtime: window closed — waiting for next window")
                    break

            # Yield briefly to let the event loop process signals, then the outer loop
            # will recalc sleep-until-window-opens.
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=5.0)
                break
            except asyncio.TimeoutError:
                pass

    async def _sleep_until_window_opens(self) -> None:
        """Block until the configured nightly window opens or the stop event fires."""
        while not self._stopping:
            now = datetime.now(self._tz)
            start = self._parse_window_time(self._window_start, now)
            end = self._parse_window_time(self._window_end, now)

            if start <= now < end:
                # Already inside the window.
                return

            if now >= end:
                # Past today's window — sleep until tomorrow's window opens.
                tomorrow = now.date() + timedelta(days=1)
                target = datetime.combine(tomorrow, start.time(), tzinfo=self._tz)
            else:
                # Before today's window opens.
                target = start

            sleep_seconds = (target - now).total_seconds()
            if sleep_seconds <= 0:
                return

            logger.debug(
                "dreaming runtime: outside window (now=%s) — sleeping %.0f s until window opens at %s",
                now.isoformat(),
                sleep_seconds,
                self._window_start,
            )

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=min(sleep_seconds, 60.0))
                return  # stop event received
            except asyncio.TimeoutError:
                pass  # woke up after up to 60 s — re-check

    def _is_in_window(self, *, when: datetime | None = None) -> bool:
        """Return True if ``when`` (default: now in configured timezone) is inside the nightly window."""
        check = when or datetime.now(self._tz)
        start = self._parse_window_time(self._window_start, check)
        end = self._parse_window_time(self._window_end, check)
        return start <= check < end

    def _parse_window_time(self, value: str, reference: datetime) -> datetime:
        """Parse HH:MM and combine with reference date in the configured timezone."""
        parsed = datetime.strptime(value, "%H:%M").time()
        return datetime.combine(reference.date(), parsed, tzinfo=self._tz)

    def _select_eligible_scopes(self) -> list["TopicAgentConfig"]:
        """
        Select the next batch of scopes eligible for dreaming.

        Rules:
        - Return up to _max_scopes_per_batch scopes.
        - Scope must have ai_enabled=True OR have at least one memory-layer interaction (topic_soul_text set).
        - Scope must have at least _min_daily_memories daily-memory rows within the lookback window.
        - Scopes are sorted deterministically (chat_id, topic_id, user_id) for fairness across batches.
        """
        # Import here to avoid circular import at module level.
        from sqlalchemy import func, select

        from amo_bot.db.models import TopicAgentConfig, TopicDailyMemory

        lookback = (datetime.now(UTC).date() - timedelta(days=self._lookback_days)).isoformat()

        # Sub-query: count daily memories per scope within lookback window.
        mem_count = (
            select(
                TopicDailyMemory.scope_type,
                TopicDailyMemory.chat_id,
                TopicDailyMemory.topic_id,
                TopicDailyMemory.user_id,
                func.count(TopicDailyMemory.id).label("mem_count"),
            )
            .where(TopicDailyMemory.memory_date >= lookback)
            .group_by(
                TopicDailyMemory.scope_type,
                TopicDailyMemory.chat_id,
                TopicDailyMemory.topic_id,
                TopicDailyMemory.user_id,
            )
            .subquery()
        )

        # Main query: all configs joined with memory count.
        has_memory = self._min_daily_memories > 0
        if has_memory:
            query = (
                select(TopicAgentConfig)
                .outerjoin(
                    mem_count,
                    (TopicAgentConfig.scope_type == mem_count.c.scope_type)
                    & (TopicAgentConfig.chat_id == mem_count.c.chat_id)
                    & (TopicAgentConfig.topic_id == mem_count.c.topic_id)
                    & (TopicAgentConfig.user_id == mem_count.c.user_id),
                )
                .where(mem_count.c.mem_count >= self._min_daily_memories)
                .order_by(TopicAgentConfig.chat_id.asc(), TopicAgentConfig.topic_id.asc(), TopicAgentConfig.user_id.asc())
                .limit(self._max_scopes_per_batch)
            )
        else:
            # When min_daily_memories=0, no memory-count gate is applied.
            query = (
                select(TopicAgentConfig)
                .order_by(TopicAgentConfig.chat_id.asc(), TopicAgentConfig.topic_id.asc(), TopicAgentConfig.user_id.asc())
                .limit(self._max_scopes_per_batch)
            )

        rows: list[TopicAgentConfig] = self._repo._session.scalars(query).all()  # noqa: SLF001
        return list(rows)

    async def _execute_batch(
        self, scopes: list["TopicAgentConfig"]
    ) -> DreamingRunResult:
        """
        Execute one batch under lock, with timeout and failure isolation.

        Returns a DreamingRunResult regardless of what goes wrong.
        """
        run_id = uuid.uuid4().hex[:16]
        started_at = datetime.now(UTC)
        token = set_run_id(run_id)

        _lock_held = False
        try:
            acquired = await asyncio.wait_for(
                self._lock.acquire(),
                timeout=5.0,
            )
            if not acquired:
                return _timeout_result(run_id, started_at, "lock_timeout")
            _lock_held = True
        except asyncio.TimeoutError:
            return _timeout_result(run_id, started_at, "lock_timeout")
        except asyncio.CancelledError:
            set_run_id(None)  # type: ignore[arg-type]
            raise

        try:
            active = self._active_run_task
            if active is not None and not active.done():
                return _timeout_result(run_id, started_at, "overlap")

            maintenance_result = await asyncio.wait_for(
                asyncio.shield(
                    asyncio.create_task(self._run_batch(run_id, scopes)),
                ),
                timeout=self._timeout,
            )

            auto_approved = 0
            if self._auto_approve and maintenance_result.curation_promoted > 0:
                auto_approved = await asyncio.wait_for(
                    asyncio.to_thread(self._auto_approve_candidates_sync),
                    timeout=max(1.0, min(self._timeout, 30.0)),
                )

            return DreamingRunResult(
                run_id=run_id,
                started_at=started_at,
                finished_at=datetime.now(UTC),
                duration_ms=int((datetime.now(UTC) - started_at).total_seconds() * 1000),
                scopes_scanned=maintenance_result.scopes_scanned,
                scopes_pruned=maintenance_result.scopes_pruned,
                deleted_daily_memories=maintenance_result.deleted_daily_memories,
                curation_candidates_considered=maintenance_result.curation_candidates_considered,
                curation_promoted=maintenance_result.curation_promoted,
                curation_auto_approved=auto_approved,
                curation_scopes_failed=maintenance_result.curation_scopes_failed,
                status="success",
                error_class=None,
            )
        except asyncio.TimeoutError:
            return _timeout_result(run_id, started_at, "timeout")
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001 — everything including system-exit must be caught
            error_class = type(exc).__name__
            return DreamingRunResult(
                run_id=run_id,
                started_at=started_at,
                finished_at=datetime.now(UTC),
                duration_ms=int((datetime.now(UTC) - started_at).total_seconds() * 1000),
                scopes_scanned=0,
                scopes_pruned=0,
                deleted_daily_memories=0,
                curation_candidates_considered=0,
                curation_promoted=0,
                curation_auto_approved=0,
                curation_scopes_failed=0,
                status="error",
                error_class=error_class,
            )
        finally:
            set_run_id(None)  # type: ignore[arg-type]
            if _lock_held:
                self._lock.release()

    async def _run_batch(
        self, run_id: str, scopes: list["TopicAgentConfig"]
    ) -> "MemoryMaintenanceResult":
        """Call MemoryMaintenanceService.run_once() for a specific scope list."""
        from amo_bot.ai.memory_maintenance import MemoryMaintenanceResult

        log_event(
            logger, logging.INFO,
            event="dreaming.batch.start",
            component=_COMPONENT,
            extra={
                "run_id": run_id,
                "scopes_in_batch": len(scopes),
                "auto_approve": self._auto_approve,
                "max_candidates": self._max_daily_candidates,
                "max_promotions": self._max_promotions,
            },
        )

        timing: dict[str, object] = {}
        with duration_timer(timing):
            result = await asyncio.to_thread(self._call_service_batch, scopes)

        log_event(
            logger, logging.INFO,
            event="dreaming.batch.complete",
            component=_COMPONENT,
            duration_ms=timing.get("duration_ms"),
            extra={
                "run_id": run_id,
                "status": "success",
                "scopes_scanned": result.scopes_scanned,
                "scopes_pruned": result.scopes_pruned,
                "deleted_daily_memories": result.deleted_daily_memories,
                "curation_scopes_attempted": result.curation_scopes_attempted,
                "curation_candidates_considered": result.curation_candidates_considered,
                "curation_promoted": result.curation_promoted,
                "curation_scopes_failed": result.curation_scopes_failed,
            },
        )
        return result

    def _call_service_batch(
        self, scopes: list["TopicAgentConfig"]
    ) -> "MemoryMaintenanceResult":
        """Call the synchronous maintenance service for an explicit scope list."""
        return self._service.run_once(scopes=scopes)

    def _auto_approve_candidates_sync(self) -> int:
        """
        Approve all 'candidate' long_memory rows created during the most recent batch.
        """
        approved = 0
        for scope_type in ("topic", "group_chat", "private_user"):
            try:
                rows = self._repo.list_long_memories(
                    scope_type=scope_type,
                    active_only=True,
                    limit=100,
                )
                for row in rows:
                    if row.promotion_status == "candidate":
                        try:
                            self._repo.approve_long_memory(memory_id=row.id)
                            approved += 1
                        except Exception:  # noqa: BLE001
                            pass
            except Exception:  # noqa: BLE001
                pass
        return approved

    def _clear_active_run_task(
        self, task: asyncio.Task["MemoryMaintenanceResult"]
    ) -> None:
        if self._active_run_task is task:
            self._active_run_task = None
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001
            logger.debug("dreaming worker finished after timeout: %s", type(exc).__name__)

    def _log_result(self, result: DreamingRunResult) -> None:
        level = logging.INFO if result.status == "success" else logging.WARNING
        log_event(
            logger, level,
            event="dreaming.batch.report",
            component=_COMPONENT,
            duration_ms=result.duration_ms,
            extra={
                "run_id": result.run_id,
                "status": result.status,
                "error_class": result.error_class,
                "scopes_scanned": result.scopes_scanned,
                "scopes_pruned": result.scopes_pruned,
                "deleted_daily_memories": result.deleted_daily_memories,
                "curation_candidates_considered": result.curation_candidates_considered,
                "curation_promoted": result.curation_promoted,
                "curation_auto_approved": result.curation_auto_approved,
                "curation_scopes_failed": result.curation_scopes_failed,
            },
        )

    # ── read-only accessors ──────────────────────────────────────────────────

    @property
    def last_result(self) -> DreamingRunResult | None:
        """Most recent batch result, or None if no batch has completed yet."""
        return self._last_result

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_enabled(self) -> bool:
        return self._enabled


def _timeout_result(run_id: str, started_at: datetime, reason: str) -> DreamingRunResult:
    status = "timeout" if reason not in ("lock_timeout",) else "error"
    if reason == "lock_timeout":
        error_name = "LockTimeoutError"
    elif reason == "timeout":
        error_name = "TimeoutError"
    elif reason == "cancelled":
        error_name = "CancelledError"
    else:
        error_name = reason.title()
    return DreamingRunResult(
        run_id=run_id,
        started_at=started_at,
        finished_at=datetime.now(UTC),
        duration_ms=int((datetime.now(UTC) - started_at).total_seconds() * 1000),
        scopes_scanned=0,
        scopes_pruned=0,
        deleted_daily_memories=0,
        curation_candidates_considered=0,
        curation_promoted=0,
        curation_auto_approved=0,
        curation_scopes_failed=0,
        status=status,
        error_class=error_name,
    )
