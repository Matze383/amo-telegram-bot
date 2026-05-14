from amo_bot.ai import (
    CapabilityActorType,
    CapabilityScopeType,
    CapabilityAuditTrail,
    InMemoryCapabilityAuditSink,
    MEMORY_CAPABILITIES,
    MemoryPolicyDecisionResult,
    MemorySensitivityClass,
    MemoryCapabilityRequest,
    evaluate_memory_capability_policy,
)


def test_cp_g1_memory_capability_catalog_is_sensitive_by_default() -> None:
    assert set(MEMORY_CAPABILITIES.keys()) == {
        "ki.memory.get",
        "ki.memory.search",
        "ki.memory.put",
        "ki.memory.delete",
    }
    for descriptor in MEMORY_CAPABILITIES.values():
        assert descriptor.sensitivity in {MemorySensitivityClass.SENSITIVE, MemorySensitivityClass.PRIVATE}


def test_cp_g1_scope_and_consent_deny_without_consent() -> None:
    decision = evaluate_memory_capability_policy(
        MemoryCapabilityRequest(
            actor_type=CapabilityActorType.AI,
            capability_name="ki.memory.search",
            scope_type=CapabilityScopeType.TOPIC,
            consent_granted=False,
        )
    )
    assert decision.result == MemoryPolicyDecisionResult.DENY
    assert decision.reason_code == "consent_required"


def test_cp_g1_userplugin_scope_isolation_and_no_tunnel() -> None:
    denied_scope = evaluate_memory_capability_policy(
        MemoryCapabilityRequest(
            actor_type=CapabilityActorType.USERPLUGIN,
            capability_name="ki.memory.get",
            scope_type=CapabilityScopeType.TOPIC,
            consent_granted=True,
        )
    )
    assert denied_scope.result == MemoryPolicyDecisionResult.DENY
    assert denied_scope.reason_code == "scope_not_allowed"


def test_cp_g1_default_deny_for_actor_outside_memory_allowlist() -> None:
    denied_group_scope = evaluate_memory_capability_policy(
        MemoryCapabilityRequest(
            actor_type=CapabilityActorType.USERPLUGIN,
            capability_name="ki.memory.search",
            scope_type=CapabilityScopeType.GROUP,
            consent_granted=True,
        )
    )
    assert denied_group_scope.result == MemoryPolicyDecisionResult.DENY
    assert denied_group_scope.reason_code == "scope_not_allowed"


def test_cp_g1_unknown_or_empty_capability_denied_with_unknown_capability() -> None:
    denied_unknown = evaluate_memory_capability_policy(
        MemoryCapabilityRequest(
            actor_type=CapabilityActorType.AI,
            capability_name="ki.memory.nonexistent",
            scope_type=CapabilityScopeType.USER,
            consent_granted=True,
        )
    )
    assert denied_unknown.result == MemoryPolicyDecisionResult.DENY
    assert denied_unknown.reason_code == "unknown_capability"

    denied_empty = evaluate_memory_capability_policy(
        MemoryCapabilityRequest(
            actor_type=CapabilityActorType.AI,
            capability_name="   ",
            scope_type=CapabilityScopeType.USER,
            consent_granted=True,
        )
    )
    assert denied_empty.result == MemoryPolicyDecisionResult.DENY
    assert denied_empty.reason_code == "unknown_capability"


def test_cp_g1_userplugin_cannot_inherit_topic_or_group_memory_privileges_even_with_consent() -> None:
    denied_topic = evaluate_memory_capability_policy(
        MemoryCapabilityRequest(
            actor_type=CapabilityActorType.USERPLUGIN,
            capability_name="ki.memory.put",
            scope_type=CapabilityScopeType.TOPIC,
            consent_granted=True,
        )
    )
    assert denied_topic.result == MemoryPolicyDecisionResult.DENY
    assert denied_topic.reason_code == "scope_not_allowed"

    denied_group = evaluate_memory_capability_policy(
        MemoryCapabilityRequest(
            actor_type=CapabilityActorType.USERPLUGIN,
            capability_name="ki.memory.put",
            scope_type=CapabilityScopeType.GROUP,
            consent_granted=True,
        )
    )
    assert denied_group.result == MemoryPolicyDecisionResult.DENY
    assert denied_group.reason_code == "scope_not_allowed"


def test_cp_g1_allowed_when_actor_scope_and_consent_match() -> None:
    allowed = evaluate_memory_capability_policy(
        MemoryCapabilityRequest(
            actor_type=CapabilityActorType.AI,
            capability_name="ki.memory.put",
            scope_type=CapabilityScopeType.USER,
            consent_granted=True,
        )
    )
    assert allowed.result == MemoryPolicyDecisionResult.ALLOW
    assert allowed.reason_code == "policy_allow"


def test_cp_g1_audit_does_not_include_raw_memory_text() -> None:
    sink = InMemoryCapabilityAuditSink()
    trail = CapabilityAuditTrail(recorder=sink)

    trail.record_requested(
        request_id="req-g1-1",
        capability_name="ki.memory.search",
        capability_version="1.0.0",
        actor_type="ai",
        scope_type="topic",
        input_summary_count=1,
        input_summary_approx_bytes=256,
        risk_flags_count=1,
    )

    synthetic_memory_text = "CP_G1_SYNTHETIC_MEMORY_TEXT_DO_NOT_LOG"
    trail.record_failed(
        request_id="req-g1-1",
        capability_name="ki.memory.search",
        capability_version="1.0.0",
        error_code=synthetic_memory_text,
    )

    serialized = "\n".join(
        f"{event.summary}|{event.reason_code or ''}|{event.details}" for event in sink.events
    )
    assert synthetic_memory_text not in serialized
