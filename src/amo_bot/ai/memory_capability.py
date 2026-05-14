from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .capability_policy import CapabilityActorType, CapabilityScopeType


class MemorySensitivityClass(StrEnum):
    PRIVATE = "private"
    SENSITIVE = "sensitive"


class MemoryOperation(StrEnum):
    GET = "get"
    SEARCH = "search"
    PUT = "put"
    DELETE = "delete"


class MemoryPolicyDecisionResult(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class MemoryCapabilityDescriptor:
    capability_name: str
    operation: MemoryOperation
    sensitivity: MemorySensitivityClass = MemorySensitivityClass.SENSITIVE


@dataclass(frozen=True, slots=True)
class MemoryCapabilityRequest:
    actor_type: CapabilityActorType
    capability_name: str
    scope_type: CapabilityScopeType
    consent_granted: bool


@dataclass(frozen=True, slots=True)
class MemoryCapabilityDecision:
    result: MemoryPolicyDecisionResult
    reason_code: str

    @property
    def allowed(self) -> bool:
        return self.result is MemoryPolicyDecisionResult.ALLOW


MEMORY_CAPABILITIES: dict[str, MemoryCapabilityDescriptor] = {
    "ki.memory.get": MemoryCapabilityDescriptor(
        capability_name="ki.memory.get",
        operation=MemoryOperation.GET,
    ),
    "ki.memory.search": MemoryCapabilityDescriptor(
        capability_name="ki.memory.search",
        operation=MemoryOperation.SEARCH,
    ),
    "ki.memory.put": MemoryCapabilityDescriptor(
        capability_name="ki.memory.put",
        operation=MemoryOperation.PUT,
    ),
    "ki.memory.delete": MemoryCapabilityDescriptor(
        capability_name="ki.memory.delete",
        operation=MemoryOperation.DELETE,
    ),
}

_SAFE_DENY_REASONS: set[str] = {
    "default_deny",
    "unknown_capability",
    "actor_type_not_allowed",
    "scope_not_allowed",
    "consent_required",
}

_MEMORY_SCOPE_ALLOWLIST: dict[CapabilityActorType, dict[str, set[CapabilityScopeType]]] = {
    CapabilityActorType.AI: {
        "ki.memory.get": {CapabilityScopeType.TOPIC, CapabilityScopeType.USER},
        "ki.memory.search": {CapabilityScopeType.TOPIC, CapabilityScopeType.USER},
        "ki.memory.put": {CapabilityScopeType.TOPIC, CapabilityScopeType.USER},
        "ki.memory.delete": {CapabilityScopeType.TOPIC, CapabilityScopeType.USER},
    },
    # UserPlugins share the same capability layer with strict scoping,
    # but do not inherit any admin/tunnel privileges.
    CapabilityActorType.USERPLUGIN: {
        "ki.memory.get": {CapabilityScopeType.USER},
        "ki.memory.search": {CapabilityScopeType.USER},
        "ki.memory.put": {CapabilityScopeType.USER},
        "ki.memory.delete": {CapabilityScopeType.USER},
    },
}


def evaluate_memory_capability_policy(request: MemoryCapabilityRequest) -> MemoryCapabilityDecision:
    capability_key = _normalize_capability_name(request.capability_name)
    if not capability_key:
        return _deny("unknown_capability")

    descriptor = MEMORY_CAPABILITIES.get(capability_key)
    if descriptor is None:
        return _deny("unknown_capability")

    actor_policies = _MEMORY_SCOPE_ALLOWLIST.get(request.actor_type)
    if actor_policies is None:
        return _deny("default_deny")

    allowed_scopes = actor_policies.get(capability_key)
    if allowed_scopes is None:
        return _deny("actor_type_not_allowed")

    if request.scope_type not in allowed_scopes:
        return _deny("scope_not_allowed")

    # Memory is always sensitive by default. Explicit consent is mandatory.
    if not request.consent_granted:
        return _deny("consent_required")

    return MemoryCapabilityDecision(result=MemoryPolicyDecisionResult.ALLOW, reason_code="policy_allow")


def _normalize_capability_name(value: str) -> str:
    return value.strip().lower() if isinstance(value, str) else ""


def _deny(reason_code: str) -> MemoryCapabilityDecision:
    if reason_code not in _SAFE_DENY_REASONS:
        reason_code = "default_deny"
    return MemoryCapabilityDecision(result=MemoryPolicyDecisionResult.DENY, reason_code=reason_code)
