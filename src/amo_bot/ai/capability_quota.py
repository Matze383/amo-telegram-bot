from __future__ import annotations

from collections.abc import MutableMapping
from dataclasses import dataclass
from enum import StrEnum

from .capability_policy import CapabilityActorType, CapabilityScopeType


class QuotaDecisionResult(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class CapabilityQuotaDecision:
    result: QuotaDecisionResult
    reason_code: str
    current_count: int
    limit: int

    @property
    def allowed(self) -> bool:
        return self.result is QuotaDecisionResult.ALLOW


@dataclass(frozen=True, slots=True)
class CapabilityQuotaRequest:
    capability_name: str
    actor_type: CapabilityActorType
    actor_id: str
    scope_type: CapabilityScopeType
    scope_id: str

    def __post_init__(self) -> None:
        if not _normalize_key_part(self.capability_name):
            raise ValueError("capability_name must not be empty")
        if not _normalize_key_part(self.actor_id):
            raise ValueError("actor_id must not be empty")
        if not _normalize_key_part(self.scope_id):
            raise ValueError("scope_id must not be empty")


@dataclass(frozen=True, slots=True)
class CapabilityQuotaRule:
    limit: int

    def __post_init__(self) -> None:
        if self.limit <= 0:
            raise ValueError("limit must be positive")


class CapabilityQuotaCounterStore:
    """In-memory scoped counters for capability quota checks."""

    def __init__(self) -> None:
        self._counters: dict[tuple[str, str, str, str, str], int] = {}

    def get(self, key: tuple[str, str, str, str, str]) -> int:
        return self._counters.get(key, 0)

    def increment(self, key: tuple[str, str, str, str, str]) -> int:
        new_value = self.get(key) + 1
        self._counters[key] = new_value
        return new_value

    def reset(self) -> None:
        self._counters.clear()


class CoreCapabilityQuotaLimiter:
    """Quota foundation: deny before execution and increment only on allow."""

    def __init__(
        self,
        *,
        rules: MutableMapping[str, CapabilityQuotaRule] | None = None,
        counter_store: CapabilityQuotaCounterStore | None = None,
    ) -> None:
        self._rules = rules if rules is not None else {}
        self._counter_store = counter_store if counter_store is not None else CapabilityQuotaCounterStore()

    def evaluate(self, request: CapabilityQuotaRequest) -> CapabilityQuotaDecision:
        rule = self._rules.get(_normalize_key_part(request.capability_name))
        if rule is None:
            return CapabilityQuotaDecision(
                result=QuotaDecisionResult.DENY,
                reason_code="quota_not_configured",
                current_count=0,
                limit=0,
            )

        key = _build_counter_key(request)
        current_count = self._counter_store.get(key)
        if current_count >= rule.limit:
            return CapabilityQuotaDecision(
                result=QuotaDecisionResult.DENY,
                reason_code="quota_exceeded",
                current_count=current_count,
                limit=rule.limit,
            )

        new_count = self._counter_store.increment(key)
        return CapabilityQuotaDecision(
            result=QuotaDecisionResult.ALLOW,
            reason_code="quota_allow",
            current_count=new_count,
            limit=rule.limit,
        )

    def reset_counters(self) -> None:
        self._counter_store.reset()


def _build_counter_key(request: CapabilityQuotaRequest) -> tuple[str, str, str, str, str]:
    return (
        _normalize_key_part(request.capability_name),
        request.actor_type.value,
        _normalize_key_part(request.actor_id),
        request.scope_type.value,
        _normalize_key_part(request.scope_id),
    )


def _normalize_key_part(value: str) -> str:
    return value.strip().lower() if isinstance(value, str) else ""
