from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .capability_audit import CapabilityAuditRecorder, CapabilityAuditTrail
from .capability_policy import (
    CapabilityActor,
    CapabilityActorType,
    CapabilityScope,
    CapabilityScopeType,
)

_SELF_IMPROVEMENT_CAPABILITY_NAME = "ki.self_improvement.propose"
_SELF_IMPROVEMENT_CAPABILITY_VERSION = "cp_i1"
_MAX_PROPOSAL_CHARS = 4_000
_MAX_TITLE_CHARS = 120
_MAX_STEPS = 10


class SelfImprovementAction(StrEnum):
    PROPOSE = "propose"
    MODIFY_RUNTIME = "modify_runtime"
    MODIFY_PROMPT = "modify_prompt"
    MODIFY_POLICY = "modify_policy"
    PUSH_CODE = "push_code"
    MERGE_PR = "merge_pr"


class SelfImprovementResult(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class SelfImprovementProposal:
    title: str
    rationale: str
    steps: tuple[str, ...]
    risk_notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        title = self.title.strip()
        if not title:
            raise ValueError("proposal title must not be empty")
        if len(title) > _MAX_TITLE_CHARS:
            raise ValueError("proposal title exceeds maximum length")

        rationale = self.rationale.strip()
        if not rationale:
            raise ValueError("proposal rationale must not be empty")
        if len(rationale) > _MAX_PROPOSAL_CHARS:
            raise ValueError("proposal rationale exceeds maximum length")

        if not self.steps:
            raise ValueError("proposal steps must not be empty")
        if len(self.steps) > _MAX_STEPS:
            raise ValueError("proposal steps exceed maximum length")

        for step in self.steps:
            normalized = step.strip()
            if not normalized:
                raise ValueError("proposal step must not be empty")
            if len(normalized) > _MAX_PROPOSAL_CHARS:
                raise ValueError("proposal step exceeds maximum length")

        for note in self.risk_notes:
            normalized = note.strip()
            if not normalized:
                raise ValueError("proposal risk note must not be empty")
            if len(normalized) > _MAX_PROPOSAL_CHARS:
                raise ValueError("proposal risk note exceeds maximum length")


@dataclass(frozen=True, slots=True)
class SelfImprovementRequest:
    request_id: str
    actor: CapabilityActor
    scope: CapabilityScope
    action: SelfImprovementAction
    proposal: SelfImprovementProposal | None = None


@dataclass(frozen=True, slots=True)
class SelfImprovementDecision:
    result: SelfImprovementResult
    reason_code: str
    proposal_text: str | None = None

    @property
    def allowed(self) -> bool:
        return self.result is SelfImprovementResult.ALLOW


def evaluate_self_improvement_request(
    request: SelfImprovementRequest,
    *,
    audit_recorder: CapabilityAuditRecorder | None = None,
) -> SelfImprovementDecision:
    audit = CapabilityAuditTrail(recorder=audit_recorder)
    action_value = request.action.value if isinstance(request.action, SelfImprovementAction) else str(request.action)
    audit.record_requested(
        request_id=request.request_id,
        capability_name=_SELF_IMPROVEMENT_CAPABILITY_NAME,
        capability_version=_SELF_IMPROVEMENT_CAPABILITY_VERSION,
        actor_type=request.actor.actor_type.value,
        scope_type=request.scope.scope_type.value,
        input_summary_count=1,
        input_summary_approx_bytes=len(action_value),
        risk_flags_count=0,
    )

    deny = _evaluate_denial_reason(request)
    if deny is not None:
        audit.record_decision(
            request_id=request.request_id,
            capability_name=_SELF_IMPROVEMENT_CAPABILITY_NAME,
            capability_version=_SELF_IMPROVEMENT_CAPABILITY_VERSION,
            decision_result=SelfImprovementResult.DENY.value,
            reason_code=deny,
        )
        return SelfImprovementDecision(result=SelfImprovementResult.DENY, reason_code=deny)

    assert request.proposal is not None
    proposal_text = _render_proposal(request.proposal)
    audit.record_decision(
        request_id=request.request_id,
        capability_name=_SELF_IMPROVEMENT_CAPABILITY_NAME,
        capability_version=_SELF_IMPROVEMENT_CAPABILITY_VERSION,
        decision_result=SelfImprovementResult.ALLOW.value,
        reason_code="policy_allow_read_only_proposal",
    )
    return SelfImprovementDecision(
        result=SelfImprovementResult.ALLOW,
        reason_code="policy_allow_read_only_proposal",
        proposal_text=proposal_text,
    )


def _evaluate_denial_reason(request: SelfImprovementRequest) -> str | None:
    if request.actor.actor_type is not CapabilityActorType.AI:
        return "actor_type_not_allowed"
    if request.scope.scope_type not in {CapabilityScopeType.TOPIC, CapabilityScopeType.USER}:
        return "scope_not_allowed"

    if request.action in {
        SelfImprovementAction.MODIFY_RUNTIME,
        SelfImprovementAction.MODIFY_PROMPT,
        SelfImprovementAction.MODIFY_POLICY,
        SelfImprovementAction.PUSH_CODE,
        SelfImprovementAction.MERGE_PR,
    }:
        return "self_improvement_action_denied"

    if request.action is not SelfImprovementAction.PROPOSE:
        return "default_deny"

    if request.proposal is None:
        return "proposal_required"

    return None


def _render_proposal(proposal: SelfImprovementProposal) -> str:
    lines: list[str] = [
        f"Title: {proposal.title.strip()}",
        f"Rationale: {proposal.rationale.strip()}",
        "Plan:",
    ]
    for idx, step in enumerate(proposal.steps, start=1):
        lines.append(f"{idx}. {step.strip()}")

    if proposal.risk_notes:
        lines.append("Risks:")
        for note in proposal.risk_notes:
            lines.append(f"- {note.strip()}")

    lines.append("Execution: read-only proposal only (no autonomous changes)")
    return "\n".join(lines)
