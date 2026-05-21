from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import Any, Awaitable, Callable, Protocol


_TERMINAL_EVENTS = {"done", "error", "cancel", "timeout"}


class TelegramLiveEditAdapter(Protocol):
    async def consume(self, *, chat_id: int, message_thread_id: int | None, event: dict[str, Any]) -> None: ...


@dataclass(slots=True)
class LiveEditFailure:
    stage: str
    code: str


LiveEditFailureRecorder = Callable[[LiveEditFailure], Awaitable[None]]


@dataclass(slots=True)
class DisabledTelegramLiveEditAdapter:
    """No-op adapter seam for future live Telegram send/edit streaming.

    Disabled by default and intentionally does not send/edit Telegram messages.
    """

    failure_recorder: LiveEditFailureRecorder | None = None

    async def consume(self, *, chat_id: int, message_thread_id: int | None, event: dict[str, Any]) -> None:
        _ = chat_id
        _ = message_thread_id
        _ = event
        return


@dataclass(slots=True)
class SafeTelegramLiveEditAdapter:
    """Internal controller seam for canonical events with fail-closed metadata-only recording."""

    enabled: bool = False
    send_text: Callable[[int, str, int | None], Awaitable[object]] | None = None
    edit_text: Callable[[int, int, str, int | None], Awaitable[object]] | None = None
    failure_recorder: LiveEditFailureRecorder | None = None
    min_edit_interval_seconds: float = 0.35
    max_consecutive_edit_failures: int = 2
    _live_message_id: int | None = None
    _last_edit_at: float | None = None
    _consecutive_edit_failures: int = 0
    _degraded: bool = False
    _terminal_outcome: str | None = None

    async def consume(self, *, chat_id: int, message_thread_id: int | None, event: dict[str, Any]) -> None:
        if not self.enabled:
            return

        event_type = str(event.get("event") or "").strip().casefold()
        if self._terminal_outcome is not None:
            return

        if event_type == "start":
            if self.send_text is None:
                await self._record_failure(stage="start", code="send_missing")
                return
            try:
                result = await self.send_text(chat_id, "…", message_thread_id)
            except Exception:
                await self._record_failure(stage="start", code="send_failed")
                return
            self._live_message_id = self._extract_message_id(result)
            if self._live_message_id is None:
                await self._record_failure(stage="start", code="send_missing_message_id")
            return

        if event_type == "delta":
            if self._degraded:
                return
            if self._live_message_id is None:
                await self._record_failure(stage="delta", code="missing_live_message")
                return
            if self.edit_text is None:
                await self._record_failure(stage="delta", code="edit_missing")
                return
            if self._is_throttled():
                await self._record_failure(stage="delta", code="edit_throttled")
                return
            try:
                await self.edit_text(chat_id, self._live_message_id, "…", message_thread_id)
                self._last_edit_at = monotonic()
                self._consecutive_edit_failures = 0
            except Exception:
                self._consecutive_edit_failures += 1
                await self._record_failure(stage="delta", code="edit_failed")
                if self._consecutive_edit_failures >= self.max_consecutive_edit_failures:
                    self._degraded = True
                    await self._record_failure(stage="delta", code="edit_disabled_after_failures")
            return

        if event_type in _TERMINAL_EVENTS:
            self._terminal_outcome = event_type
            return

        await self._record_failure(stage="event", code="unsupported_event")

    async def _record_failure(self, *, stage: str, code: str) -> None:
        if self.failure_recorder is None:
            return
        await self.failure_recorder(LiveEditFailure(stage=stage, code=code))

    def _is_throttled(self) -> bool:
        if self.min_edit_interval_seconds <= 0 or self._last_edit_at is None:
            return False
        now = monotonic()
        return (now - self._last_edit_at) < self.min_edit_interval_seconds

    @staticmethod
    def _extract_message_id(value: object) -> int | None:
        if isinstance(value, dict):
            raw = value.get("message_id")
            if isinstance(raw, int):
                return raw
        return None
