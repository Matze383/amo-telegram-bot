from __future__ import annotations

from dataclasses import dataclass

from amo_bot.db.repositories import ResearchEvalCaseRecord, ResearchEvalCaseRepository
from amo_bot.telegram.webtool_auto_research import decide_auto_research
from amo_bot.telegram.webtool_evidence import classify_evidence_domain
from amo_bot.telegram.webtool_research_orchestrator import build_research_plan, should_chain_auto_research


_FAIL_CLOSED_DOMAINS = {"weather", "crypto", "stock", "sports", "news"}


@dataclass(frozen=True, slots=True)
class ResearchEvalHarnessResult:
    case_key: str
    domain: str
    prompt_domain: str
    auto_research_enabled: bool
    would_chain: bool
    would_followup_on_weak_initial_evidence: bool
    fail_closed_required: bool
    passed: bool
    reason: str


def run_research_eval_cases(
    repository: ResearchEvalCaseRepository,
    *,
    domain: str | None = None,
    limit: int = 100,
) -> tuple[ResearchEvalHarnessResult, ...]:
    """Run enabled sanitized eval cases through production routing/gate predicates."""

    cases = repository.list_enabled(domain=domain, limit=limit)
    return tuple(_run_case(case) for case in cases)


def _run_case(case: ResearchEvalCaseRecord) -> ResearchEvalHarnessResult:
    prompt_domain = classify_evidence_domain(case.sanitized_prompt)
    decision = decide_auto_research(case.sanitized_prompt)
    would_chain = should_chain_auto_research(
        case.sanitized_prompt,
        capability=decision.capability,
        reason=decision.reason,
    )
    source_hosts = _metadata_source_hosts(case)
    weak_plan = build_research_plan(
        request_text=case.sanitized_prompt,
        capability=decision.capability,
        reason=decision.reason,
        source_hosts=source_hosts,
    )
    would_followup = weak_plan.should_followup_search
    fail_closed_required = prompt_domain in _FAIL_CLOSED_DOMAINS

    expected_status = (case.expected_status or "").strip().lower()
    if expected_status in {"needs_improvement", "low_quality", "unavailable", "needs_multi_source_web"}:
        if case.domain in {"source_quality", "generic"}:
            passed = decision.enabled or fail_closed_required or bool(case.sanitized_prompt.strip())
            reason = "feedback_case_available_as_regression_input" if passed else "empty_feedback_case"
        elif expected_status == "low_quality" and source_hosts:
            passed = prompt_domain == case.domain and would_followup
            reason = "weak_initial_evidence_would_plan_followup" if passed else "weak_initial_evidence_not_planned"
        else:
            passed = prompt_domain == case.domain and (decision.enabled or fail_closed_required)
            reason = "research_gate_matches_case_domain" if passed else "case_not_reached_by_research_gate"
    else:
        passed = bool(case.sanitized_prompt.strip())
        reason = "case_loaded"

    return ResearchEvalHarnessResult(
        case_key=case.case_key,
        domain=case.domain,
        prompt_domain=prompt_domain,
        auto_research_enabled=decision.enabled,
        would_chain=would_chain,
        would_followup_on_weak_initial_evidence=would_followup,
        fail_closed_required=fail_closed_required,
        passed=passed,
        reason=reason,
    )


def _metadata_source_hosts(case: ResearchEvalCaseRecord) -> tuple[str, ...]:
    metadata = case.expected_metadata or {}
    raw_hosts = metadata.get("source_hosts") if isinstance(metadata, dict) else None
    if not isinstance(raw_hosts, list):
        return ()
    hosts: list[str] = []
    for raw in raw_hosts:
        host = str(raw or "").strip().lower()
        if host and host not in hosts:
            hosts.append(host)
    return tuple(hosts[:10])
