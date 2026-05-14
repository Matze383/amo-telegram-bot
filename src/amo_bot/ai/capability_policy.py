from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .capability_audit import CapabilityAuditRecorder, CapabilityAuditTrail

_MAX_ID_LENGTH = 128
_MAX_VERSION_LENGTH = 32
_MAX_REQUEST_ID_LENGTH = 128
_MAX_SUMMARY_KEY_LENGTH = 64
_MAX_SUMMARY_VALUE_LENGTH = 256
_MAX_REASON_CODE_LENGTH = 64
_MAX_RISK_FLAGS = 16

_REASON_CODE_PREFIXES: tuple[str, ...] = (
    "invalid_",
    "unknown_",
    "policy_",
    "default_",
    "capability_",
)


class CapabilityActorType(StrEnum):
    AI = "ai"
    USERPLUGIN = "userplugin"


class CapabilityScopeType(StrEnum):
    GROUP = "group"
    TOPIC = "topic"
    USER = "user"


class CapabilityDecisionResult(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    ALLOW_WITH_REDACTION = "allow_with_redaction"


@dataclass(frozen=True, slots=True)
class CapabilityActor:
    actor_type: CapabilityActorType
    actor_id: str

    def __post_init__(self) -> None:
        _ensure_non_empty_bounded_str(self.actor_id, "actor_id", _MAX_ID_LENGTH)


@dataclass(frozen=True, slots=True)
class CapabilityScope:
    scope_type: CapabilityScopeType
    scope_id: str

    def __post_init__(self) -> None:
        _ensure_non_empty_bounded_str(self.scope_id, "scope_id", _MAX_ID_LENGTH)


@dataclass(frozen=True, slots=True)
class CapabilityInputSummaryItem:
    key: str
    value_type: str
    approx_size: int

    def __post_init__(self) -> None:
        _ensure_non_empty_bounded_str(self.key, "key", _MAX_SUMMARY_KEY_LENGTH)
        _ensure_non_empty_bounded_str(self.value_type, "value_type", _MAX_SUMMARY_VALUE_LENGTH)
        if self.approx_size < 0:
            raise ValueError("approx_size must be >= 0")


@dataclass(frozen=True, slots=True)
class CapabilityCallEnvelope:
    actor: CapabilityActor
    scope: CapabilityScope
    capability_name: str
    capability_version: str
    input_summary: tuple[CapabilityInputSummaryItem, ...]
    request_id: str
    risk_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _ensure_non_empty_bounded_str(self.capability_name, "capability_name", _MAX_ID_LENGTH)
        _ensure_non_empty_bounded_str(self.capability_version, "capability_version", _MAX_VERSION_LENGTH)
        _ensure_non_empty_bounded_str(self.request_id, "request_id", _MAX_REQUEST_ID_LENGTH)
        if len(self.input_summary) > _MAX_RISK_FLAGS:
            raise ValueError("input_summary exceeds maximum items")
        if len(self.risk_flags) > _MAX_RISK_FLAGS:
            raise ValueError("risk_flags exceeds maximum items")
        for flag in self.risk_flags:
            _ensure_non_empty_bounded_str(flag, "risk_flag", _MAX_SUMMARY_VALUE_LENGTH)


@dataclass(frozen=True, slots=True)
class CapabilityPolicyDecision:
    result: CapabilityDecisionResult
    reason_code: str

    def __post_init__(self) -> None:
        if not _is_safe_reason_code(self.reason_code):
            raise ValueError("unsafe reason_code")


def build_capability_denial(reason_code: str) -> CapabilityPolicyDecision:
    return CapabilityPolicyDecision(result=CapabilityDecisionResult.DENY, reason_code=reason_code)


def validate_capability_call_envelope(
    envelope: CapabilityCallEnvelope,
    *,
    expected_capability_name: str,
    expected_capability_version: str,
    policy_result_hint: CapabilityDecisionResult | str | None = None,
    audit_recorder: CapabilityAuditRecorder | None = None,
) -> CapabilityPolicyDecision:
    expected_name = _normalize_identifier(expected_capability_name)
    expected_version = expected_capability_version.strip()
    audit = CapabilityAuditTrail(recorder=audit_recorder)
    audit.record_requested(
        request_id=envelope.request_id,
        capability_name=envelope.capability_name,
        capability_version=envelope.capability_version,
        actor_type=envelope.actor.actor_type.value,
        scope_type=envelope.scope.scope_type.value,
        input_summary_count=len(envelope.input_summary),
        input_summary_approx_bytes=sum(item.approx_size for item in envelope.input_summary),
        risk_flags_count=len(envelope.risk_flags),
    )

    if not expected_name:
        decision = build_capability_denial("invalid_expected_capability")
        audit.record_decision(
            request_id=envelope.request_id,
            capability_name=envelope.capability_name,
            capability_version=envelope.capability_version,
            decision_result=decision.result.value,
            reason_code=decision.reason_code,
        )
        return decision
    if not expected_version:
        decision = build_capability_denial("invalid_expected_version")
        audit.record_decision(
            request_id=envelope.request_id,
            capability_name=envelope.capability_name,
            capability_version=envelope.capability_version,
            decision_result=decision.result.value,
            reason_code=decision.reason_code,
        )
        return decision

    if _normalize_identifier(envelope.capability_name) != expected_name:
        decision = build_capability_denial("unknown_capability")
        audit.record_decision(
            request_id=envelope.request_id,
            capability_name=envelope.capability_name,
            capability_version=envelope.capability_version,
            decision_result=decision.result.value,
            reason_code=decision.reason_code,
        )
        return decision
    if envelope.capability_version.strip() != expected_version:
        decision = build_capability_denial("capability_version_mismatch")
        audit.record_decision(
            request_id=envelope.request_id,
            capability_name=envelope.capability_name,
            capability_version=envelope.capability_version,
            decision_result=decision.result.value,
            reason_code=decision.reason_code,
        )
        return decision

    decision_result = _parse_policy_result_hint(policy_result_hint)
    if decision_result is CapabilityDecisionResult.ALLOW:
        decision = CapabilityPolicyDecision(result=CapabilityDecisionResult.ALLOW, reason_code="policy_allow")
        audit.record_decision(
            request_id=envelope.request_id,
            capability_name=envelope.capability_name,
            capability_version=envelope.capability_version,
            decision_result=decision.result.value,
            reason_code=decision.reason_code,
        )
        return decision
    if decision_result is CapabilityDecisionResult.ALLOW_WITH_REDACTION:
        decision = CapabilityPolicyDecision(
            result=CapabilityDecisionResult.ALLOW_WITH_REDACTION,
            reason_code="policy_allow_with_redaction",
        )
        audit.record_decision(
            request_id=envelope.request_id,
            capability_name=envelope.capability_name,
            capability_version=envelope.capability_version,
            decision_result=decision.result.value,
            reason_code=decision.reason_code,
        )
        return decision

    decision = build_capability_denial("default_deny")
    audit.record_decision(
        request_id=envelope.request_id,
        capability_name=envelope.capability_name,
        capability_version=envelope.capability_version,
        decision_result=decision.result.value,
        reason_code=decision.reason_code,
    )
    return decision


def _ensure_non_empty_bounded_str(value: str, field_name: str, max_length: int) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    if len(normalized) > max_length:
        raise ValueError(f"{field_name} exceeds maximum length")


def _normalize_identifier(value: str) -> str:
    return value.strip().lower() if isinstance(value, str) else ""


def _parse_policy_result_hint(
    policy_result_hint: CapabilityDecisionResult | str | None,
) -> CapabilityDecisionResult | None:
    if policy_result_hint is None:
        return None
    if isinstance(policy_result_hint, CapabilityDecisionResult):
        return policy_result_hint
    if isinstance(policy_result_hint, str):
        normalized = policy_result_hint.strip().lower()
        if normalized == CapabilityDecisionResult.ALLOW.value:
            return CapabilityDecisionResult.ALLOW
        if normalized == CapabilityDecisionResult.ALLOW_WITH_REDACTION.value:
            return CapabilityDecisionResult.ALLOW_WITH_REDACTION
        return None
    return None


def _is_safe_reason_code(reason_code: str) -> bool:
    if not isinstance(reason_code, str):
        return False
    normalized = reason_code.strip()
    if not normalized or len(normalized) > _MAX_REASON_CODE_LENGTH:
        return False
    if any(ch.isupper() for ch in normalized):
        return False
    if any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789_" for ch in normalized):
        return False
    return normalized.startswith(_REASON_CODE_PREFIXES)
