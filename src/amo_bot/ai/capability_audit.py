from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol


_MAX_EVENT_ID_LENGTH = 128
_MAX_ID_LENGTH = 128
_MAX_REASON_CODE_LENGTH = 64
_MAX_SUMMARY_LENGTH = 256
_MAX_DETAIL_KEY_LENGTH = 64
_MAX_DETAIL_VALUE_LENGTH = 128
_MAX_DETAIL_ITEMS = 8
_MAX_EVENTS_STORED = 10_000

_SAFE_DETAIL_KEYS: tuple[str, ...] = (
    "capability_name",
    "capability_version",
    "actor_type",
    "scope_type",
    "decision_result",
    "reason_code",
    "risk_flags_count",
    "input_summary_count",
    "input_summary_approx_bytes",
    "error_code",
)


class CapabilityAuditEventStatus(StrEnum):
    REQUESTED = "requested"
    DENIED = "denied"
    ALLOWED = "allowed"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class CapabilityAuditEvent:
    event_id: str
    request_id: str
    capability_name: str
    capability_version: str
    status: CapabilityAuditEventStatus
    summary: str
    reason_code: str | None = None
    details: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        _ensure_bounded_non_empty(self.event_id, "event_id", _MAX_EVENT_ID_LENGTH)
        _ensure_bounded_non_empty(self.request_id, "request_id", _MAX_ID_LENGTH)
        _ensure_bounded_non_empty(self.capability_name, "capability_name", _MAX_ID_LENGTH)
        _ensure_bounded_non_empty(self.capability_version, "capability_version", 32)
        _ensure_summary(self.summary)
        if self.reason_code is not None:
            _ensure_safe_reason_code(self.reason_code)
        _ensure_safe_details(self.details)


class CapabilityAuditRecorder(Protocol):
    def record(self, event: CapabilityAuditEvent) -> None:
        ...


@dataclass(slots=True)
class InMemoryCapabilityAuditSink(CapabilityAuditRecorder):
    events: list[CapabilityAuditEvent] = field(default_factory=list)

    def record(self, event: CapabilityAuditEvent) -> None:
        if len(self.events) >= _MAX_EVENTS_STORED:
            raise ValueError("audit event store limit reached")
        self.events.append(event)


@dataclass(frozen=True, slots=True)
class CapabilityExecutionResult:
    status: CapabilityAuditEventStatus
    reason_code: str | None = None


class CapabilityAuditTrail:
    def __init__(self, recorder: CapabilityAuditRecorder | None = None) -> None:
        self._recorder = recorder
        self._seq = 0

    def record_requested(
        self,
        *,
        request_id: str,
        capability_name: str,
        capability_version: str,
        actor_type: str,
        scope_type: str,
        input_summary_count: int,
        input_summary_approx_bytes: int,
        risk_flags_count: int,
    ) -> CapabilityAuditEvent | None:
        event = CapabilityAuditEvent(
            event_id=self._next_event_id(request_id),
            request_id=request_id,
            capability_name=capability_name,
            capability_version=capability_version,
            status=CapabilityAuditEventStatus.REQUESTED,
            summary=_bounded_summary("request_received"),
            details=_sanitize_details(
                {
                    "actor_type": actor_type,
                    "scope_type": scope_type,
                    "input_summary_count": str(max(0, input_summary_count)),
                    "input_summary_approx_bytes": str(max(0, input_summary_approx_bytes)),
                    "risk_flags_count": str(max(0, risk_flags_count)),
                }
            ),
        )
        return self._record_if_enabled(event)

    def record_decision(
        self,
        *,
        request_id: str,
        capability_name: str,
        capability_version: str,
        decision_result: str,
        reason_code: str,
    ) -> CapabilityAuditEvent | None:
        normalized_decision = decision_result.strip().lower()
        status = (
            CapabilityAuditEventStatus.ALLOWED
            if normalized_decision in {"allow", "allow_with_redaction"}
            else CapabilityAuditEventStatus.DENIED
        )
        event = CapabilityAuditEvent(
            event_id=self._next_event_id(request_id),
            request_id=request_id,
            capability_name=capability_name,
            capability_version=capability_version,
            status=status,
            summary=_bounded_summary("policy_decision"),
            reason_code=reason_code,
            details=_sanitize_details(
                {
                    "decision_result": normalized_decision,
                    "reason_code": reason_code,
                }
            ),
        )
        return self._record_if_enabled(event)

    def record_completed(
        self,
        *,
        request_id: str,
        capability_name: str,
        capability_version: str,
    ) -> CapabilityExecutionResult:
        event = CapabilityAuditEvent(
            event_id=self._next_event_id(request_id),
            request_id=request_id,
            capability_name=capability_name,
            capability_version=capability_version,
            status=CapabilityAuditEventStatus.COMPLETED,
            summary=_bounded_summary("execution_completed"),
        )
        self._record_if_enabled(event)
        return CapabilityExecutionResult(status=CapabilityAuditEventStatus.COMPLETED)

    def record_failed(
        self,
        *,
        request_id: str,
        capability_name: str,
        capability_version: str,
        error_code: str,
    ) -> CapabilityExecutionResult:
        safe_error_code = _normalize_error_code(error_code)
        event = CapabilityAuditEvent(
            event_id=self._next_event_id(request_id),
            request_id=request_id,
            capability_name=capability_name,
            capability_version=capability_version,
            status=CapabilityAuditEventStatus.FAILED,
            summary=_bounded_summary("execution_failed"),
            reason_code=safe_error_code,
            details=_sanitize_details({"error_code": safe_error_code}),
        )
        self._record_if_enabled(event)
        return CapabilityExecutionResult(status=CapabilityAuditEventStatus.FAILED, reason_code=safe_error_code)

    def _next_event_id(self, request_id: str) -> str:
        self._seq += 1
        normalized_request_id = request_id.strip()[:64]
        return f"{normalized_request_id}:{self._seq}"

    def _record_if_enabled(self, event: CapabilityAuditEvent) -> CapabilityAuditEvent | None:
        if self._recorder is None:
            return None
        try:
            self._recorder.record(event)
        except Exception:
            # Audit sink errors are best-effort only and must never alter policy/execution flow.
            return None
        return event


def _ensure_bounded_non_empty(value: str, field_name: str, max_length: int) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    if len(normalized) > max_length:
        raise ValueError(f"{field_name} exceeds maximum length")


def _ensure_summary(summary: str) -> None:
    _ensure_bounded_non_empty(summary, "summary", _MAX_SUMMARY_LENGTH)


def _ensure_safe_reason_code(reason_code: str) -> None:
    _ensure_bounded_non_empty(reason_code, "reason_code", _MAX_REASON_CODE_LENGTH)
    if any(ch.isupper() for ch in reason_code):
        raise ValueError("reason_code must be lowercase")
    allowed = "abcdefghijklmnopqrstuvwxyz0123456789_"
    if any(ch not in allowed for ch in reason_code):
        raise ValueError("reason_code contains unsupported characters")


def _ensure_safe_details(details: tuple[tuple[str, str], ...]) -> None:
    if len(details) > _MAX_DETAIL_ITEMS:
        raise ValueError("details exceed maximum items")
    for key, value in details:
        _ensure_bounded_non_empty(key, "detail key", _MAX_DETAIL_KEY_LENGTH)
        _ensure_bounded_non_empty(value, "detail value", _MAX_DETAIL_VALUE_LENGTH)
        if key not in _SAFE_DETAIL_KEYS:
            raise ValueError("detail key not allowed")


def _sanitize_details(items: dict[str, str]) -> tuple[tuple[str, str], ...]:
    sanitized: list[tuple[str, str]] = []
    for key in _SAFE_DETAIL_KEYS:
        if key not in items:
            continue
        raw = str(items[key]).strip()
        if not raw:
            continue
        bounded = raw[:_MAX_DETAIL_VALUE_LENGTH]
        sanitized.append((key, bounded))
        if len(sanitized) >= _MAX_DETAIL_ITEMS:
            break
    return tuple(sanitized)


def _bounded_summary(value: str) -> str:
    return value.strip()[:_MAX_SUMMARY_LENGTH]


def _normalize_error_code(error_code: str) -> str:
    normalized = "".join(ch for ch in error_code.strip().lower() if ch.isalnum() or ch == "_")
    if not normalized:
        return "execution_error"
    return normalized[:_MAX_REASON_CODE_LENGTH]
