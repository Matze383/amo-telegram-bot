import pytest

from amo_bot.ai.capability_policy import (
    CapabilityActor,
    CapabilityActorType,
    CapabilityCallEnvelope,
    CapabilityDecisionResult,
    CapabilityInputSummaryItem,
    CapabilityPolicyDecision,
    CapabilityScope,
    CapabilityScopeType,
    validate_capability_call_envelope,
)


def _valid_envelope() -> CapabilityCallEnvelope:
    return CapabilityCallEnvelope(
        actor=CapabilityActor(actor_type=CapabilityActorType.AI, actor_id="ai-router"),
        scope=CapabilityScope(scope_type=CapabilityScopeType.TOPIC, scope_id="topic-42"),
        capability_name="ki.memory.read",
        capability_version="1.0.0",
        input_summary=(
            CapabilityInputSummaryItem(key="query", value_type="text", approx_size=120),
        ),
        request_id="req-123",
        risk_flags=("memory_access",),
    )


def test_envelope_validation_default_denies_known_capability() -> None:
    decision = validate_capability_call_envelope(
        _valid_envelope(),
        expected_capability_name="ki.memory.read",
        expected_capability_version="1.0.0",
    )

    assert decision == CapabilityPolicyDecision(
        result=CapabilityDecisionResult.DENY,
        reason_code="default_deny",
    )


def test_allow_decision_is_reachable_via_explicit_bounded_hint() -> None:
    decision = validate_capability_call_envelope(
        _valid_envelope(),
        expected_capability_name="ki.memory.read",
        expected_capability_version="1.0.0",
        policy_result_hint=CapabilityDecisionResult.ALLOW,
    )

    assert decision == CapabilityPolicyDecision(
        result=CapabilityDecisionResult.ALLOW,
        reason_code="policy_allow",
    )


def test_allow_with_redaction_is_reachable_via_explicit_bounded_hint() -> None:
    decision = validate_capability_call_envelope(
        _valid_envelope(),
        expected_capability_name="ki.memory.read",
        expected_capability_version="1.0.0",
        policy_result_hint="allow_with_redaction",
    )

    assert decision == CapabilityPolicyDecision(
        result=CapabilityDecisionResult.ALLOW_WITH_REDACTION,
        reason_code="policy_allow_with_redaction",
    )


def test_unrecognized_hint_stays_default_deny() -> None:
    decision = validate_capability_call_envelope(
        _valid_envelope(),
        expected_capability_name="ki.memory.read",
        expected_capability_version="1.0.0",
        policy_result_hint="allow_execute_shell",
    )

    assert decision == CapabilityPolicyDecision(
        result=CapabilityDecisionResult.DENY,
        reason_code="default_deny",
    )


def test_invalid_actor_and_scope_are_rejected_by_dto_validation() -> None:
    with pytest.raises(ValueError, match="actor_id must not be empty"):
        CapabilityActor(actor_type=CapabilityActorType.USERPLUGIN, actor_id=" ")

    with pytest.raises(ValueError, match="scope_id must not be empty"):
        CapabilityScope(scope_type=CapabilityScopeType.GROUP, scope_id="")


def test_unknown_capability_denied_safely() -> None:
    decision = validate_capability_call_envelope(
        _valid_envelope(),
        expected_capability_name="ki.memory.search",
        expected_capability_version="1.0.0",
    )
    assert decision.result == CapabilityDecisionResult.DENY
    assert decision.reason_code == "unknown_capability"


def test_capability_version_mismatch_denied_safely() -> None:
    decision = validate_capability_call_envelope(
        _valid_envelope(),
        expected_capability_name="ki.memory.read",
        expected_capability_version="2.0.0",
    )
    assert decision.result == CapabilityDecisionResult.DENY
    assert decision.reason_code == "capability_version_mismatch"


def test_reason_code_leakage_guard_blocks_unsafe_codes() -> None:
    with pytest.raises(ValueError, match="unsafe reason_code"):
        CapabilityPolicyDecision(result=CapabilityDecisionResult.DENY, reason_code="sql_error: relation users")

    with pytest.raises(ValueError, match="unsafe reason_code"):
        CapabilityPolicyDecision(result=CapabilityDecisionResult.ALLOW, reason_code="ALLOWED")


def test_input_bounds_are_enforced() -> None:
    with pytest.raises(ValueError, match="input_summary exceeds maximum items"):
        CapabilityCallEnvelope(
            actor=CapabilityActor(actor_type=CapabilityActorType.AI, actor_id="ai-router"),
            scope=CapabilityScope(scope_type=CapabilityScopeType.TOPIC, scope_id="topic-42"),
            capability_name="ki.memory.read",
            capability_version="1.0.0",
            input_summary=tuple(
                CapabilityInputSummaryItem(key=f"k{i}", value_type="text", approx_size=1)
                for i in range(17)
            ),
            request_id="req-123",
        )
