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
    failure_class: str
    prompt_domain: str
    auto_research_enabled: bool
    would_chain: bool
    would_followup_on_weak_initial_evidence: bool
    fail_closed_required: bool
    routing_pass: bool
    source_quality_pass: bool
    answer_quality_risk: bool
    passed: bool
    reason: str


@dataclass(frozen=True, slots=True)
class ResearchEvalHarnessReport:
    total: int
    passed: int
    failed: int
    routing_pass: int
    routing_fail: int
    source_quality_pass: int
    source_quality_fail: int
    answer_quality_risk: int
    results: tuple[ResearchEvalHarnessResult, ...]


def run_research_eval_cases(
    repository: ResearchEvalCaseRepository,
    *,
    domain: str | None = None,
    limit: int = 100,
) -> tuple[ResearchEvalHarnessResult, ...]:
    """Run enabled sanitized eval cases through production routing/gate predicates."""

    cases = repository.list_enabled(domain=domain, limit=limit)
    return tuple(_run_case(case) for case in cases)


def build_research_eval_report(
    repository: ResearchEvalCaseRepository,
    *,
    domain: str | None = None,
    limit: int = 100,
) -> ResearchEvalHarnessReport:
    """Build a compact QA report with separate routing/source/answer-quality signals."""

    results = run_research_eval_cases(repository, domain=domain, limit=limit)
    routing_pass = sum(1 for result in results if result.routing_pass)
    source_quality_cases = [result for result in results if result.failure_class == "source_quality"]
    source_quality_pass = sum(1 for result in source_quality_cases if result.source_quality_pass)
    passed = sum(1 for result in results if result.passed)
    return ResearchEvalHarnessReport(
        total=len(results),
        passed=passed,
        failed=len(results) - passed,
        routing_pass=routing_pass,
        routing_fail=len(results) - routing_pass,
        source_quality_pass=source_quality_pass,
        source_quality_fail=len(source_quality_cases) - source_quality_pass,
        answer_quality_risk=sum(1 for result in results if result.answer_quality_risk),
        results=results,
    )


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
    failure_class = _metadata_failure_class(case)
    if case.domain in {"source_quality", "generic"}:
        routing_pass = decision.enabled or fail_closed_required
    else:
        routing_pass = _domains_match(case.domain, prompt_domain) and (decision.enabled or fail_closed_required)
    source_quality_pass = failure_class != "source_quality" or would_followup or len(source_hosts) != 1
    answer_quality_risk = failure_class in {"answer_quality", "answer_quality_risk", "incorrect_answer"}

    expected_status = (case.expected_status or "").strip().lower()
    if expected_status in {"needs_improvement", "low_quality", "unavailable", "needs_multi_source_web"}:
        if case.domain in {"source_quality", "generic"}:
            passed = decision.enabled or fail_closed_required or bool(case.sanitized_prompt.strip())
            reason = "feedback_case_available_as_regression_input" if passed else "empty_feedback_case"
        elif expected_status == "low_quality" and source_hosts:
            passed = _domains_match(case.domain, prompt_domain) and would_followup
            reason = "weak_initial_evidence_would_plan_followup" if passed else "weak_initial_evidence_not_planned"
        else:
            passed = routing_pass
            reason = "research_gate_matches_case_domain" if passed else "case_not_reached_by_research_gate"
    else:
        passed = bool(case.sanitized_prompt.strip())
        reason = "case_loaded"

    return ResearchEvalHarnessResult(
        case_key=case.case_key,
        domain=case.domain,
        failure_class=failure_class,
        prompt_domain=prompt_domain,
        auto_research_enabled=decision.enabled,
        would_chain=would_chain,
        would_followup_on_weak_initial_evidence=would_followup,
        fail_closed_required=fail_closed_required,
        routing_pass=routing_pass,
        source_quality_pass=source_quality_pass,
        answer_quality_risk=answer_quality_risk,
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


def _metadata_failure_class(case: ResearchEvalCaseRecord) -> str:
    metadata = case.expected_metadata or {}
    raw_failure_class = metadata.get("failure_class") if isinstance(metadata, dict) else None
    failure_class = str(raw_failure_class or "").strip().lower()
    return failure_class or "unknown"


def _domains_match(case_domain: str, prompt_domain: str) -> bool:
    if case_domain == prompt_domain:
        return True
    return {case_domain, prompt_domain} == {"finance", "stock"}
