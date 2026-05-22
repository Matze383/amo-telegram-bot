from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .capability_policy import CapabilityActorType, CapabilityScopeType


class CorePolicyDecisionResult(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class CoreCapabilityPolicyDecision:
    result: CorePolicyDecisionResult
    reason_code: str

    @property
    def allowed(self) -> bool:
        return self.result is CorePolicyDecisionResult.ALLOW


@dataclass(frozen=True, slots=True)
class CoreCapabilityPolicyRequest:
    actor_type: CapabilityActorType
    capability_name: str
    scope_type: CapabilityScopeType


_SAFE_DENY_REASONS: set[str] = {
    "default_deny",
    "unknown_capability",
    "actor_type_not_allowed",
    "scope_not_allowed",
}

_KNOWN_CAPABILITIES: set[str] = {
    "ki.memory.read",
    "rss.fetch",
    "ki.websearch.query",
    "plugin.status.read",
}

# Central allowlist by actor type -> capability -> permitted scope set.
# Default deny is enforced when any mapping step is missing.
_CORE_POLICY_ALLOWLIST: dict[CapabilityActorType, dict[str, set[CapabilityScopeType]]] = {
    CapabilityActorType.AI: {
        "ki.memory.read": {CapabilityScopeType.TOPIC, CapabilityScopeType.USER},
    },
    CapabilityActorType.USERPLUGIN: {
        "plugin.status.read": {CapabilityScopeType.TOPIC, CapabilityScopeType.USER},
    },
}


def evaluate_core_capability_policy(request: CoreCapabilityPolicyRequest) -> CoreCapabilityPolicyDecision:
    capability_key = _normalize_capability_name(request.capability_name)
    if not capability_key:
        return _deny("unknown_capability")
    if not _is_known_capability(capability_key):
        return _deny("unknown_capability")

    actor_policies = _CORE_POLICY_ALLOWLIST.get(request.actor_type)
    if actor_policies is None:
        return _deny("default_deny")

    allowed_scopes = actor_policies.get(capability_key)
    if allowed_scopes is None:
        return _deny("actor_type_not_allowed")

    if request.scope_type not in allowed_scopes:
        return _deny("scope_not_allowed")

    return CoreCapabilityPolicyDecision(result=CorePolicyDecisionResult.ALLOW, reason_code="policy_allow")


def _normalize_capability_name(value: str) -> str:
    return value.strip().lower() if isinstance(value, str) else ""


def _is_known_capability(capability_name: str) -> bool:
    return capability_name in _KNOWN_CAPABILITIES


def _deny(reason_code: str) -> CoreCapabilityPolicyDecision:
    if reason_code not in _SAFE_DENY_REASONS:
        reason_code = "default_deny"
    return CoreCapabilityPolicyDecision(result=CorePolicyDecisionResult.DENY, reason_code=reason_code)
