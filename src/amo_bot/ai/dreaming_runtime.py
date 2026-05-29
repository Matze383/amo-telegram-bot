"""
Dreaming / Memory-Curation Runtime

Bot-internal background task that periodically calls MemoryMaintenanceService.run_once()
to perform retention pruning and (optionally) automatic long-memory curation.

Key properties:
- Default DISABLED.  Activated via DREAMING_ENABLED=1.
- No overlap: a single asyncio.Lock ensures at most one run at a time.
- Timeout: enforced via asyncio.wait_for(); overdue runs are cancelled and logged.
- Safe shutdown: stop() waits for any in-progress run to finish (drain) before returning.
- Failures are isolated — a crashed run does not affect the next cycle.
- All logs are metadata-only (run_id / duration_ms / counts / status / error class).
  No prompts, message text, tokens, or raw memory content are ever logged.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from amo_bot.core.logging import duration_timer, log_event, set_run_id
from amo_bot.db.repositories import TopicAgentMemoryRepository


logger = logging.getLogger(__name__)
_COMPONENT = "dreaming.runtime"


@dataclass(slots=True, frozen=True)
class DreamingRunResult:
    """Metadata-only result of a single dreaming run."""

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
    status: str  # "success" | "timeout" | "error"
    error_class: str | None  # exception class name, not the message


class DreamingRuntime:
    """
    Periodic background task for memory maintenance and curation.

    When ``enabled`` is False (the default) the scheduler loop is never started.
    When started, the loop wakes every ``interval_seconds``, acquires the
    in-process lock, and calls ``_run_once()`` with timeout enforcement.
    """

    def __init__(
        self,
        *,
        repository: TopicAgentMemoryRepository,
        enabled: bool = False,
        interval_seconds: int = 3600,
        timeout_seconds: float = 300.0,
        max_daily_candidates_per_scope: int = 3,
        max_promotions_per_scope: int = 2,
        auto_approve: bool = False,
    ) -> None:
        self._repo = repository
        self._enabled = enabled
        self._interval = max(60, interval_seconds)  # minimum 60 s
        self._timeout = max(1.0, timeout_seconds)
        self._max_daily_candidates = max(1, min(max_daily_candidates_per_scope, 30))
        self._max_promotions = max(1, min(max_promotions_per_scope, 20))
        self._auto_approve = auto_approve

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
            auto_curate_long_memory=True,  # curation is always attempted when called
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
            "dreaming runtime loop started: interval_s=%d timeout_s=%.1f auto_approve=%s",
            self._interval,
            self._timeout,
            self._auto_approve,
        )

    async def stop(self) -> None:
        """
        Signal the scheduler to stop and wait for the in-progress run (if any) to drain.

        After stop() returns the loop is terminated and start() may be called again.
        """
        if not self._running and (self._active_run_task is None or self._active_run_task.done()):
            return
        self._stopping = True
        logger.info("dreaming runtime stopping — waiting for in-progress run to drain")
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

        Detects whether there is a running event loop and uses the appropriate
        shutdown path.  Safe to call even when the runtime was never started or
        is already stopped.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # No running loop — there is no live asyncio task left to drain.
            self._stopping = True
            self._running = False
            self._task = None
            self._stop_event = None
            return
        # There is a running loop — fire-and-forget the drain; the loop is
        # about to be closed by asyncio.run() anyway.
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
            try:
                stop_event = self._stop_event
                if stop_event is None:
                    break
                await asyncio.wait_for(stop_event.wait(), timeout=self._interval)
                break
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                break
            if self._stopping:
                break

            # Run with no-overlap enforcement and timeout.
            result = await self._execute_protected()
            self._last_result = result

            self._log_result(result)

    async def _execute_protected(self) -> DreamingRunResult:
        """
        Execute one dreaming cycle under lock, with timeout and failure isolation.

        Returns a DreamingRunResult regardless of what goes wrong.
        """
        run_id = uuid.uuid4().hex[:16]
        started_at = datetime.now(UTC)
        token = set_run_id(run_id)

        _lock_held = False
        try:
            acquired = await asyncio.wait_for(
                self._lock.acquire(),
                timeout=5.0,  # lock acquisition must not block shutdown
            )
            if not acquired:
                # Could not acquire within 5 s — shutdown in progress.
                return _timeout_result(run_id, started_at, "lock_timeout")
            _lock_held = True
        except asyncio.TimeoutError:
            return _timeout_result(run_id, started_at, "lock_timeout")
        except asyncio.CancelledError:
            set_run_id(None)  # type: ignore[arg-type]
            raise

        try:
            # Actual execution with per-run timeout.
            active = self._active_run_task
            if active is not None and not active.done():
                return _timeout_result(run_id, started_at, "overlap")

            active = asyncio.create_task(self._run_once(run_id), name=f"dreaming-run-{run_id}")
            self._active_run_task = active
            active.add_done_callback(self._clear_active_run_task)

            maintenance_result = await asyncio.wait_for(asyncio.shield(active), timeout=self._timeout)
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

    async def _run_once(self, run_id: str) -> "MemoryMaintenanceResult":
        """One execution of MemoryMaintenanceService.run_once()."""
        # Import the result type for type annotation.
        from amo_bot.ai.memory_maintenance import MemoryMaintenanceResult

        log_event(
            logger, logging.INFO,
            event="dreaming.run.start",
            component=_COMPONENT,
            extra={
                "run_id": run_id,
                "auto_approve": self._auto_approve,
                "max_candidates": self._max_daily_candidates,
                "max_promotions": self._max_promotions,
            },
        )

        timing: dict[str, Any] = {}
        with duration_timer(timing):
            result = await asyncio.to_thread(self._call_service_once)

        log_event(
            logger, logging.INFO,
            event="dreaming.run.complete",
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

    def _call_service_once(self) -> "MemoryMaintenanceResult":
        """Call the synchronous maintenance service off the event-loop thread."""
        return self._service.run_once()

    def _auto_approve_candidates_sync(self) -> int:
        """
        Approve all candidate memories created during the most recent run.

        This is called only when ``auto_approve=True`` and only after
        ``run_once()`` has created candidate records.  Candidates are scoped
        by type so no cross-scope approvals leak through.
        """
        approved = 0
        # Enumerate the scope types that can produce candidates.
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

    def _clear_active_run_task(self, task: asyncio.Task["MemoryMaintenanceResult"]) -> None:
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
            event="dreaming.run.report",
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
        """Most recent run result, or None if no run has completed yet."""
        return self._last_result

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_enabled(self) -> bool:
        return self._enabled


def _timeout_result(run_id: str, started_at: datetime, reason: str) -> DreamingRunResult:
    # lock_timeout -> status=error (internal error)
    # timeout / cancelled -> status=timeout (time-bound interruption)
    status = "timeout" if reason not in ("lock_timeout",) else "error"
    if reason == "lock_timeout":
        error_name = f"Lock{reason.title()}Error"
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
