from amo_bot.ai import (
    CapabilityActorType,
    CapabilityScopeType,
    CorePolicyDecisionResult,
    CoreCapabilityPolicyRequest,
    evaluate_core_capability_policy,
)


def test_unknown_capability_denied_explicitly() -> None:
    decision = evaluate_core_capability_policy(
        CoreCapabilityPolicyRequest(
            actor_type=CapabilityActorType.AI,
            capability_name="ki.network.call",
            scope_type=CapabilityScopeType.TOPIC,
        )
    )
    assert decision.result == CorePolicyDecisionResult.DENY
    assert decision.reason_code == "unknown_capability"


def test_ai_allowlist_scope_matrix() -> None:
    allow = evaluate_core_capability_policy(
        CoreCapabilityPolicyRequest(
            actor_type=CapabilityActorType.AI,
            capability_name="ki.memory.read",
            scope_type=CapabilityScopeType.TOPIC,
        )
    )
    assert allow.result == CorePolicyDecisionResult.ALLOW
    assert allow.reason_code == "policy_allow"

    deny_scope = evaluate_core_capability_policy(
        CoreCapabilityPolicyRequest(
            actor_type=CapabilityActorType.AI,
            capability_name="ki.memory.read",
            scope_type=CapabilityScopeType.GROUP,
        )
    )
    assert deny_scope.result == CorePolicyDecisionResult.DENY
    assert deny_scope.reason_code == "scope_not_allowed"


def test_ki_cannot_inherit_admin_rights_via_userplugin_capability() -> None:
    denied = evaluate_core_capability_policy(
        CoreCapabilityPolicyRequest(
            actor_type=CapabilityActorType.AI,
            capability_name="plugin.status.read",
            scope_type=CapabilityScopeType.TOPIC,
        )
    )
    assert denied.result == CorePolicyDecisionResult.DENY
    assert denied.reason_code == "actor_type_not_allowed"


def test_userplugin_cannot_tunnel_through_ki_capabilities() -> None:
    denied = evaluate_core_capability_policy(
        CoreCapabilityPolicyRequest(
            actor_type=CapabilityActorType.USERPLUGIN,
            capability_name="ki.memory.read",
            scope_type=CapabilityScopeType.TOPIC,
        )
    )
    assert denied.result == CorePolicyDecisionResult.DENY
    assert denied.reason_code == "actor_type_not_allowed"


def test_userplugin_scope_isolation() -> None:
    allowed = evaluate_core_capability_policy(
        CoreCapabilityPolicyRequest(
            actor_type=CapabilityActorType.USERPLUGIN,
            capability_name="plugin.status.read",
            scope_type=CapabilityScopeType.USER,
        )
    )
    assert allowed.result == CorePolicyDecisionResult.ALLOW
    assert allowed.reason_code == "policy_allow"

    denied_scope = evaluate_core_capability_policy(
        CoreCapabilityPolicyRequest(
            actor_type=CapabilityActorType.USERPLUGIN,
            capability_name="plugin.status.read",
            scope_type=CapabilityScopeType.GROUP,
        )
    )
    assert denied_scope.result == CorePolicyDecisionResult.DENY
    assert denied_scope.reason_code == "scope_not_allowed"
