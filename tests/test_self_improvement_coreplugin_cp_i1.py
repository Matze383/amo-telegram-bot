from amo_bot.ai import (
    CapabilityActor,
    CapabilityActorType,
    CapabilityScope,
    CapabilityScopeType,
    InMemoryCapabilityAuditSink,
)
from amo_bot.ai.self_improvement_coreplugin_cp_i1 import (
    SelfImprovementAction,
    SelfImprovementDecision,
    SelfImprovementProposal,
    SelfImprovementRequest,
    SelfImprovementResult,
    evaluate_self_improvement_request,
)


def _actor() -> CapabilityActor:
    return CapabilityActor(actor_type=CapabilityActorType.AI, actor_id="ai:local")


def _non_ai_actor() -> CapabilityActor:
    return CapabilityActor(actor_type=CapabilityActorType.USERPLUGIN, actor_id="plugin:local")


def _scope() -> CapabilityScope:
    return CapabilityScope(scope_type=CapabilityScopeType.TOPIC, scope_id="topic:1")


def test_cp_i1_allows_read_only_proposal_and_returns_text_plan() -> None:
    sink = InMemoryCapabilityAuditSink()
    request = SelfImprovementRequest(
        request_id="cp-i1-allow",
        actor=_actor(),
        scope=_scope(),
        action=SelfImprovementAction.PROPOSE,
        proposal=SelfImprovementProposal(
            title="Improve guardrails",
            rationale="Reduce unsafe autonomous behavior.",
            steps=("Add policy checks", "Add deny-path tests"),
            risk_notes=("Avoid breaking existing capability checks",),
        ),
    )

    decision = evaluate_self_improvement_request(request, audit_recorder=sink)

    assert isinstance(decision, SelfImprovementDecision)
    assert decision.result == SelfImprovementResult.ALLOW
    assert decision.reason_code == "policy_allow_read_only_proposal"
    assert decision.proposal_text is not None
    assert "Plan:" in decision.proposal_text
    assert "Execution: read-only proposal only" in decision.proposal_text

    assert len(sink.events) == 2
    assert sink.events[1].status.value == "allowed"
    assert sink.events[1].reason_code == "policy_allow_read_only_proposal"


def test_cp_i1_denies_runtime_prompt_policy_push_modifications_and_audits() -> None:
    denied_actions = (
        SelfImprovementAction.MODIFY_RUNTIME,
        SelfImprovementAction.MODIFY_PROMPT,
        SelfImprovementAction.MODIFY_POLICY,
        SelfImprovementAction.PUSH_CODE,
        SelfImprovementAction.MERGE_PR,
    )

    for index, action in enumerate(denied_actions, start=1):
        sink = InMemoryCapabilityAuditSink()
        request = SelfImprovementRequest(
            request_id=f"cp-i1-deny-{index}",
            actor=_actor(),
            scope=_scope(),
            action=action,
            proposal=SelfImprovementProposal(
                title="Denied action test",
                rationale="Ensure forbidden actions are blocked.",
                steps=("Attempt forbidden action",),
            ),
        )

        decision = evaluate_self_improvement_request(request, audit_recorder=sink)

        assert decision.result == SelfImprovementResult.DENY
        assert decision.reason_code == "self_improvement_action_denied"
        assert decision.proposal_text is None

        assert len(sink.events) == 2
        assert sink.events[1].status.value == "denied"
        assert sink.events[1].reason_code == "self_improvement_action_denied"


def test_cp_i1_propose_without_proposal_is_denied_and_audited() -> None:
    sink = InMemoryCapabilityAuditSink()
    request = SelfImprovementRequest(
        request_id="cp-i1-missing-proposal",
        actor=_actor(),
        scope=_scope(),
        action=SelfImprovementAction.PROPOSE,
        proposal=None,
    )

    decision = evaluate_self_improvement_request(request, audit_recorder=sink)

    assert decision.result == SelfImprovementResult.DENY
    assert decision.reason_code == "proposal_required"
    assert decision.proposal_text is None
    assert len(sink.events) == 2
    assert sink.events[1].status.value == "denied"
    assert sink.events[1].reason_code == "proposal_required"


def test_cp_i1_denies_non_ai_actor_and_audits() -> None:
    sink = InMemoryCapabilityAuditSink()
    request = SelfImprovementRequest(
        request_id="cp-i1-non-ai-actor",
        actor=_non_ai_actor(),
        scope=_scope(),
        action=SelfImprovementAction.PROPOSE,
        proposal=SelfImprovementProposal(
            title="Proposal should be rejected",
            rationale="Actor type must be AI for CP-I1.",
            steps=("Try to submit proposal",),
        ),
    )

    decision = evaluate_self_improvement_request(request, audit_recorder=sink)

    assert decision.result == SelfImprovementResult.DENY
    assert decision.reason_code == "actor_type_not_allowed"
    assert decision.proposal_text is None
    assert len(sink.events) == 2
    assert sink.events[1].status.value == "denied"
    assert sink.events[1].reason_code == "actor_type_not_allowed"


def test_cp_i1_denies_disallowed_scope_and_audits() -> None:
    sink = InMemoryCapabilityAuditSink()
    request = SelfImprovementRequest(
        request_id="cp-i1-scope-deny",
        actor=_actor(),
        scope=CapabilityScope(scope_type=CapabilityScopeType.GROUP, scope_id="group:1"),
        action=SelfImprovementAction.PROPOSE,
        proposal=SelfImprovementProposal(
            title="Proposal should be rejected",
            rationale="Group scope is not allowed for CP-I1.",
            steps=("Try to submit proposal",),
        ),
    )

    decision = evaluate_self_improvement_request(request, audit_recorder=sink)

    assert decision.result == SelfImprovementResult.DENY
    assert decision.reason_code == "scope_not_allowed"
    assert decision.proposal_text is None
    assert len(sink.events) == 2
    assert sink.events[1].status.value == "denied"
    assert sink.events[1].reason_code == "scope_not_allowed"


def test_cp_i1_default_denies_unknown_action_and_audits() -> None:
    sink = InMemoryCapabilityAuditSink()
    request = SelfImprovementRequest(
        request_id="cp-i1-default-deny",
        actor=_actor(),
        scope=_scope(),
        action="extended_action",  # type: ignore[arg-type]
        proposal=SelfImprovementProposal(
            title="Unknown action should be denied",
            rationale="Defensive default deny for future/extended actions.",
            steps=("Try unknown action",),
        ),
    )

    decision = evaluate_self_improvement_request(request, audit_recorder=sink)

    assert decision.result == SelfImprovementResult.DENY
    assert decision.reason_code == "default_deny"
    assert decision.proposal_text is None
    assert len(sink.events) == 2
    assert sink.events[1].status.value == "denied"
    assert sink.events[1].reason_code == "default_deny"
