from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, Protocol
from urllib.parse import parse_qs, urlparse

from amo_bot.ai.research_extraction_quality import classify_extraction_quality, extraction_length_bucket
from amo_bot.auth.roles import Role
from amo_bot.core.logging import log_event
from amo_bot.telegram import sports_query
from amo_bot.telegram.update_parser import TelegramMessage
from amo_bot.telegram.webtool_auto_research import decide_auto_research
from amo_bot.telegram.webtool_chat_integration import (
    WebtoolChatTrigger,
    build_empty_result_retry_queries,
    build_empty_result_retry_query,
    build_web_research_followup_query,
    build_webtool_request,
    compact_webtool_result_text,
    is_web_research_followup_feedback,
    sanitize_webtool_user_facing_text,
)
from amo_bot.telegram.webtool_evidence import (
    DomainEvidenceResult,
    EvidenceSource,
    WebEvidencePipeline,
    classify_evidence_domain,
    format_domain_evidence_note,
    format_domain_fail_closed_response,
)
from amo_bot.telegram.webtool_news_corroboration import NewsCorroborationResult, assess_news_corroboration


_COMPONENT = "telegram.webtool_research_orchestrator"
logger = logging.getLogger(__name__)

_AUTO_RESEARCH_NO_RESULT_TEXT = {
    "quota_exceeded": "the attempt was limited by quota/policy",
    "provider_timeout": "the provider timed out",
    "provider_error": "the provider returned an error",
    "provider_unavailable": "the provider was unavailable",
    "search_provider_not_configured": "the provider was unavailable",
    "empty_result": "the search returned no usable hits",
}

_AUTO_RESEARCH_CHAIN_MAX_URLS = 5
_AUTO_RESEARCH_CHAIN_MAX_STATIC_SUCCESSES = 5
_AUTO_RESEARCH_CHAIN_MAX_BROWSER_FALLBACKS = 3
_AUTO_RESEARCH_CHAIN_PER_PAGE_TEXT_CAP = 1500
_AUTO_RESEARCH_CHAIN_FINAL_CAP = 1600
_AUTO_RESEARCH_SEARCH_SUMMARY_CAP = 900
_AUTO_RESEARCH_CHAIN_SNIPPET_CAP = 500
_AUTO_RESEARCH_CHAIN_MIN_EXTRACT_CHARS = 40
_AUTO_RESEARCH_PLAN_MAX_FOLLOWUP_SEARCHES = 1
_SPORTS_RESULT_SCORE_RE = re.compile(r"\b\d{1,2}\s*(?:-|:|–|—)\s*\d{1,2}\b")
_SPORTS_RESULT_RELATION_RE = re.compile(
    r"\b(?:against|vs\.?|versus|gegen|beat|beats|defeated|lost|drew|draw|unentschieden)\b",
    re.IGNORECASE,
)
_SPORTS_RESULT_OPPONENT_STOPWORDS = frozenset(
    {
        "copa",
        "cup",
        "euro",
        "fifa",
        "group",
        "historical",
        "live",
        "match",
        "page",
        "score",
        "second",
        "source",
        "stage",
        "summary",
        "uefa",
        "world",
    }
)
_AUTO_RESEARCH_CHAIN_FRESHNESS_RE = re.compile(
    r"\b(?:"
    r"current|aktuell(?:e[nrms]?)?|jetzt|heute|live|realtime|real-time|right\s+now|"
    r"derzeit|stand|status|neueste(?:n)?|latest|news|nachrichten|release|version|"
    r"update|verf(?:ü|ue)gbar(?:keit)?|availability|weather|wetter|traffic|verkehr|"
    r"outage|st(?:ö|oe)rung|kurs|preis|price|rate|market|markt|exchange|fx"
    r")\b",
    re.IGNORECASE,
)
_AUTO_RESEARCH_CHAIN_STRONG_FRESHNESS_RE = re.compile(
    r"\b(?:"
    r"jetzt|heute|live|realtime|real-time|right\s+now|derzeit|neueste(?:n)?|latest|"
    r"news|nachrichten|release|version|update|verf(?:ü|ue)gbar(?:keit)?|availability|"
    r"weather|wetter|traffic|verkehr|outage|st(?:ö|oe)rung|kurs|preis|price|rate|market|markt"
    r")\b",
    re.IGNORECASE,
)
_AUTO_RESEARCH_CHAIN_EXPLICIT_CURRENT_PHRASE_RE = re.compile(
    r"(?:"
    r"was\s+gibt\s+es\s+(?:heute\s+)?neues\s+zu|"
    r"aktueller?\s+stand\s+(?:zu|von)?|"
    r"current\s+status\s+(?:of|for)|"
    r"latest\s+\S+|"
    r"neueste(?:n)?\s+\S+"
    r")",
    re.IGNORECASE,
)
_AUTO_RESEARCH_CHAIN_TIMELESS_EDU_RE = re.compile(
    r"\b(?:erkl(?:ä|ae)re|explain|what\s+is|was\s+ist|how\s+does|wie\s+funktioniert|tutorial|grundlagen|basics)\b",
    re.IGNORECASE,
)
_WEATHER_INTENT_RE = re.compile(
    r"\b(?:wetter|weather|temperatur|temperature|regen|rain|forecast|vorhersage)\b",
    re.IGNORECASE,
)
_WEATHER_LOCATION_RE = re.compile(
    r"\b(?:in|für|fuer|for)\s+([A-ZÄÖÜ][\wÄÖÜäöüß.-]*(?:\s+[A-ZÄÖÜ][\wÄÖÜäöüß.-]*){0,3})",
    re.IGNORECASE,
)
_AUTO_RESEARCH_TECHNICAL_RESPONSE_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"Die detaillierten Live-Daten der Folge-Extraktion konnten nicht vollständig bestätigt werden\s*[–-]\s*"
            r"die Angaben basieren auf der Suchübersicht\.?",
            re.IGNORECASE,
        ),
        "Die Angaben stammen aus den verfügbaren Web-Suchergebnissen; eine zusätzliche Seitenbestätigung war diesmal nicht möglich.",
    ),
    (re.compile(r"\bFolge[-\s]?Extraktion\b", re.IGNORECASE), "Quellenprüfung"),
    (re.compile(r"\bSuchübersicht\b", re.IGNORECASE), "Suchergebnisse"),
    (re.compile(r"\bSuchuebersicht\b", re.IGNORECASE), "Suchergebnisse"),
)


@dataclass(frozen=True, slots=True)
class WebResearchOrchestratorRequest:
    message: TelegramMessage
    normalized_text: str
    role: Role
    locale: str
    is_triggered_path: bool
    reply_context_text: str
    scope: str


@dataclass(frozen=True, slots=True)
class WebResearchOrchestratorResult:
    auto_note: str = ""
    user_response: str = ""


@dataclass(frozen=True, slots=True)
class SearchPlanStep:
    operation: str
    reason: str
    query: str = ""
    url: str = ""
    max_attempts: int = 1


@dataclass(frozen=True, slots=True)
class ResearchPlan:
    domain: str
    evidence_status: str
    source_host_count: int
    warning_codes: tuple[str, ...] = ()
    steps: tuple[SearchPlanStep, ...] = ()

    @property
    def should_followup_search(self) -> bool:
        return any(step.operation == "websearch" for step in self.steps)


@dataclass(frozen=True, slots=True)
class ResearchSearchStrategy:
    """Typed query-planner boundary: what to run, why, and within which budget."""

    domain: str
    capability: str
    query: str = ""
    url: str = ""
    max_search_results: int = 5
    max_followup_searches: int = _AUTO_RESEARCH_PLAN_MAX_FOLLOWUP_SEARCHES
    max_source_urls: int = _AUTO_RESEARCH_CHAIN_MAX_URLS
    requires_source_check: bool = False


@dataclass(frozen=True, slots=True)
class QueryPlannerStageOutput:
    """Stage 1: normalized query intent and the first tool operation to attempt."""

    enabled: bool
    domain: str
    capability: str
    reason: str
    query: str = ""
    url: str = ""
    is_followup_research: bool = False
    strategy: ResearchSearchStrategy | None = None


@dataclass(frozen=True, slots=True)
class SelectedResearchSource:
    """Typed source-selection boundary with deterministic relevance metadata."""

    url: str
    host: str
    score: float
    reason: str


@dataclass(frozen=True, slots=True)
class SearchExecutionStageOutput:
    """Stage 2: bounded websearch/webtool execution result metadata."""

    result: Any
    capability: str
    reason: str
    retry_attempted: bool = False


@dataclass(frozen=True, slots=True)
class SourceSelectionStageOutput:
    """Stage 3: selected source URLs and follow-up search plan."""

    plan: ResearchPlan
    selected_urls: tuple[str, ...] = ()
    selected_sources: tuple[SelectedResearchSource, ...] = ()


@dataclass(frozen=True, slots=True)
class ExtractionBrowserStageOutput:
    """Stage 4: checked source-page evidence from static extraction/browser."""

    plan: ResearchPlan
    attempted_urls: tuple[str, ...] = ()
    extracts: tuple[tuple[str, str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class EvidenceValidationStageOutput:
    """Stage 5: final evidence gate before answer synthesis."""

    domain: str
    status: str
    can_synthesize: bool
    search_text: str = ""
    search_hosts: tuple[str, ...] = ()
    checked_extracts: tuple[tuple[str, str, str], ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AnswerSynthesisStageOutput:
    """Stage 6: safe answer context or deterministic fail-closed response."""

    auto_note: str = ""
    user_response: str = ""


@dataclass(frozen=True, slots=True)
class SportsResultEvidenceAssessment:
    confirmed: bool
    confidence: float
    units: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BrowserFallbackDecision:
    enabled: bool
    reason: str = ""


class ResearchSourceQualityReader(Protocol):
    def assess_hosts(self, *, domain: str, hosts: tuple[str, ...]) -> tuple[Any, ...]:
        ...


class ResearchSourceObservationWriter(Protocol):
    def record_observation(
        self,
        *,
        provider_name: str,
        domain: str,
        outcome: str,
        confidence: float | None = None,
        source_name: str | None = None,
        source_hosts: tuple[str, ...] | None = None,
        source_urls: tuple[str, ...] | None = None,
        source_count: int | None = None,
        warning_codes: tuple[str, ...] | None = None,
        warning_count: int | None = None,
        error_class: str | None = None,
        timing_ms: int | None = None,
        metadata: dict[str, object] | None = None,
    ) -> Any:
        ...


class DbBackedResearchSourceQualityReader:
    """Read sanitized source-observation health without reading queries or full URLs."""

    def __init__(self, *, session_factory) -> None:
        self._session_factory = session_factory

    def assess_hosts(self, *, domain: str, hosts: tuple[str, ...]) -> tuple[Any, ...]:
        from amo_bot.db.repositories import ResearchSourceObservationRepository

        since = datetime.now(UTC) - timedelta(days=14)
        with self._session_factory() as session:
            return ResearchSourceObservationRepository(session).assess_recent_hosts(
                domain=domain,
                source_hosts=hosts,
                since=since,
            )


class DbBackedResearchSourceObservationWriter:
    """Write sanitized source-chain observations without persisting raw URLs or prompts."""

    def __init__(self, *, session_factory) -> None:
        self._session_factory = session_factory

    def record_observation(self, **kwargs: Any) -> Any:
        from amo_bot.db.repositories import ResearchSourceObservationRepository

        with self._session_factory() as session:
            return ResearchSourceObservationRepository(session).record_observation(**kwargs)


class WebResearchOrchestrator:
    def __init__(
        self,
        *,
        webtool_dispatcher: Any,
        evidence_pipeline: WebEvidencePipeline | None = None,
        source_quality_reader: ResearchSourceQualityReader | None = None,
        source_observation_writer: ResearchSourceObservationWriter | None = None,
    ) -> None:
        self._webtool_dispatcher = webtool_dispatcher
        self._evidence_pipeline = evidence_pipeline
        self._source_quality_reader = source_quality_reader
        self._source_observation_writer = source_observation_writer

    def execute(self, request: WebResearchOrchestratorRequest) -> WebResearchOrchestratorResult:
        if self._webtool_dispatcher is None:
            return WebResearchOrchestratorResult()

        query_stage = build_query_planner_stage(
            request_text=request.normalized_text,
            is_triggered_path=request.is_triggered_path,
            reply_context_text=request.reply_context_text,
        )
        if not query_stage.enabled:
            return WebResearchOrchestratorResult()
        decision_auto = SimpleNamespace(
            enabled=query_stage.enabled,
            capability=query_stage.capability,
            reason=query_stage.reason,
            query=query_stage.query,
            url=query_stage.url,
        )
        is_followup_research = query_stage.is_followup_research

        domain_evidence = self._evaluate_domain_evidence(request)
        if domain_evidence is not None:
            if domain_evidence.confirmed:
                return WebResearchOrchestratorResult(auto_note=format_domain_evidence_note(domain_evidence, locale=request.locale))
            if (
                domain_evidence.status != "needs_profiled_web_research"
                and domain_evidence.domain in {"weather", "crypto", "stock", "sports"}
                and not _should_continue_with_generic_websearch(domain_evidence)
            ):
                return WebResearchOrchestratorResult(
                    user_response=format_domain_fail_closed_response(
                        domain=domain_evidence.domain,
                        locale=request.locale,
                        warnings=domain_evidence.warnings,
                    )
                )
            decision_auto = _with_profiled_source_query(decision_auto, domain_evidence)

        tool_result = self._run_primary_search(request, decision_auto=decision_auto, is_followup_research=is_followup_research)
        retry_attempted = False
        if (
            decision_auto.capability == "websearch"
            and not (tool_result.allowed and (tool_result.text or "").strip())
            and (tool_result.reason or "") == "empty_result"
        ):
            initial_query = (decision_auto.query or "").strip()
            retry_queries = tuple(
                query
                for query in build_empty_result_retry_queries(request.normalized_text)
                if query and query != initial_query
            )
            for retry_query in retry_queries:
                retry_attempted = True
                tool_result = self._run_retry_search(
                    request,
                    decision_auto=decision_auto,
                    retry_query=retry_query,
                    is_followup_research=is_followup_research,
                )
                if tool_result.allowed and (tool_result.text or "").strip():
                    break

        if not (tool_result.allowed and (tool_result.text or "").strip()):
            weather_response = _format_weather_no_result_response(
                request_text=request.normalized_text,
                reason=tool_result.reason,
                locale=request.locale,
            )
            if weather_response:
                return WebResearchOrchestratorResult(user_response=weather_response)
            if retry_attempted:
                return WebResearchOrchestratorResult(
                    auto_note=_format_auto_research_retry_no_result_note(
                        capability=decision_auto.capability,
                        reason=tool_result.reason,
                        locale=request.locale,
                    )
                )
            return WebResearchOrchestratorResult(
                auto_note=_format_auto_research_no_result_note(
                    capability=decision_auto.capability,
                    reason=tool_result.reason,
                    locale=request.locale,
                )
            )

        search_stage = SearchExecutionStageOutput(
            result=tool_result,
            capability=decision_auto.capability,
            reason=decision_auto.reason,
            retry_attempted=retry_attempted,
        )
        source_selection = build_source_selection_stage(
            request_text=request.normalized_text,
            search_execution=search_stage,
            source_quality_reader=self._source_quality_reader,
            allow_sports_result_followup=not retry_attempted,
        )
        plan = source_selection.plan
        if plan.should_followup_search:
            tool_result = self._run_planned_followup_search(
                request,
                current_result=tool_result,
                plan=plan,
                is_followup_research=is_followup_research,
            )
            search_stage = SearchExecutionStageOutput(
                result=tool_result,
                capability=decision_auto.capability,
                reason=decision_auto.reason,
                retry_attempted=retry_attempted,
            )

        chain_extracts: list[tuple[str, str, str]] = []
        chain_urls: tuple[str, ...] = ()
        extraction_stage: ExtractionBrowserStageOutput | None = None
        if should_chain_auto_research(
            request.normalized_text,
            capability=decision_auto.capability,
            reason=decision_auto.reason,
        ):
            chain_urls, chain_extracts = self._run_chain(
                request,
                tool_result=tool_result,
                decision_auto=decision_auto,
                is_followup_research=is_followup_research,
            )
            self._record_chain_source_observations(
                request=request,
                chain_urls=chain_urls,
                chain_extracts=tuple(chain_extracts),
            )
            extraction_stage = build_extraction_browser_stage(
                request_text=request.normalized_text,
                capability=decision_auto.capability,
                reason=decision_auto.reason,
                search_text=getattr(tool_result, "text", "") or "",
                source_hosts=tuple(getattr(tool_result, "hosts", ()) or ()),
                source_urls=tuple(getattr(tool_result, "sources", ()) or ()),
                extracts=tuple(chain_extracts),
                source_quality_reader=self._source_quality_reader,
            )

        if chain_extracts:
            sports_result_response = _format_sports_result_unconfirmed_response(
                request_text=request.normalized_text,
                search_text=tool_result.text,
                extracts=tuple(chain_extracts),
                locale=request.locale,
            )
            if sports_result_response:
                return WebResearchOrchestratorResult(user_response=sports_result_response)
            sports_search_text, sports_extracts = _filter_sports_result_evidence(
                request_text=request.normalized_text,
                search_text=tool_result.text,
                extracts=tuple(chain_extracts),
            )
            news_corroboration_response = _format_news_corroboration_response(
                request_text=request.normalized_text,
                extracts=sports_extracts,
                locale=request.locale,
            )
            if news_corroboration_response:
                return WebResearchOrchestratorResult(user_response=news_corroboration_response)
            source_quality = _assess_chain_source_quality(
                domain=classify_evidence_domain(request.normalized_text),
                extracts=sports_extracts,
                reader=self._source_quality_reader,
            )
            news_gate_response = _format_news_insufficient_sources_response(
                request_text=request.normalized_text,
                extract_hosts=tuple(host for _, host, _ in sports_extracts),
                locale=request.locale,
                source_quality=source_quality,
            )
            if news_gate_response:
                return WebResearchOrchestratorResult(user_response=news_gate_response)
            final_extraction_stage = build_extraction_browser_stage(
                request_text=request.normalized_text,
                capability=decision_auto.capability,
                reason=decision_auto.reason,
                search_text=sports_search_text,
                source_hosts=tuple(tool_result.hosts or ()),
                source_urls=tuple(tool_result.sources or ()),
                extracts=sports_extracts,
                source_quality_reader=self._source_quality_reader,
            )
            validation_stage = validate_research_evidence(
                request_text=request.normalized_text,
                search_execution=search_stage,
                extraction=final_extraction_stage,
            )
            synthesis_stage = synthesize_research_answer(
                validation=validation_stage,
                capability=decision_auto.capability,
                followup=is_followup_research,
                locale=request.locale,
            )
            if synthesis_stage.user_response:
                return WebResearchOrchestratorResult(user_response=synthesis_stage.user_response)
            auto_note = synthesis_stage.auto_note
        elif chain_urls:
            user_response = _format_weather_unconfirmed_response(
                request_text=request.normalized_text,
                search_text=tool_result.text,
                search_hosts=tuple(tool_result.hosts or ()),
                locale=request.locale,
            )
            if user_response:
                return WebResearchOrchestratorResult(user_response=user_response)
            domain_unconfirmed = _format_domain_chain_unconfirmed_response(
                request_text=request.normalized_text,
                locale=request.locale,
                reason="source_check_inconclusive",
            )
            if domain_unconfirmed:
                return WebResearchOrchestratorResult(user_response=domain_unconfirmed)
            validation_stage = validate_research_evidence(
                request_text=request.normalized_text,
                search_execution=search_stage,
                extraction=extraction_stage,
            )
            synthesis_stage = synthesize_research_answer(
                validation=validation_stage,
                capability=decision_auto.capability,
                locale=request.locale,
                followup=is_followup_research,
            )
            if synthesis_stage.user_response:
                return WebResearchOrchestratorResult(user_response=synthesis_stage.user_response)
            auto_note = synthesis_stage.auto_note
        else:
            validation_stage = validate_research_evidence(
                request_text=request.normalized_text,
                search_execution=search_stage,
                extraction=extraction_stage,
            )
            synthesis_stage = synthesize_research_answer(
                validation=validation_stage,
                capability=decision_auto.capability,
                locale=request.locale,
                followup=is_followup_research,
            )
            if synthesis_stage.user_response:
                return WebResearchOrchestratorResult(user_response=synthesis_stage.user_response)
            user_response = _format_weather_unconfirmed_response(
                request_text=request.normalized_text,
                search_text=tool_result.text,
                search_hosts=tuple(tool_result.hosts or ()),
                locale=request.locale,
            )
            if user_response:
                return WebResearchOrchestratorResult(user_response=user_response)
            auto_note = synthesis_stage.auto_note
        return WebResearchOrchestratorResult(auto_note=auto_note)

    def _evaluate_domain_evidence(self, request: WebResearchOrchestratorRequest) -> DomainEvidenceResult | None:
        if self._evidence_pipeline is None:
            return None
        domain = classify_evidence_domain(request.normalized_text)
        if domain in {"weather", "crypto"}:
            result = self._run_structured_evidence(request, domain=domain)
            self._log_domain_evidence(request, result)
            return result

        result = self._evidence_pipeline.evaluate(query=request.normalized_text, locale=request.locale)
        if result.domain == "generic" or result.status == "not_applicable":
            return None
        self._log_domain_evidence(request, result)
        return result

    def _run_planned_followup_search(
        self,
        request: WebResearchOrchestratorRequest,
        *,
        current_result: Any,
        plan: ResearchPlan,
        is_followup_research: bool,
    ) -> Any:
        result = current_result
        attempts = 0
        for step in plan.steps:
            if step.operation != "websearch" or not step.query:
                continue
            if attempts >= _AUTO_RESEARCH_PLAN_MAX_FOLLOWUP_SEARCHES:
                break
            attempts += 1
            followup_request = build_webtool_request(
                trigger=WebtoolChatTrigger(capability="websearch", query=step.query, url=""),
                user_id=request.message.from_user.id,
                role=request.role,
                chat_id=request.message.chat.id,
                topic_id=request.message.message_thread_id,
                locale=request.locale,
                evidence_domain=plan.domain,
            )
            followup_result = self._webtool_dispatcher.execute(followup_request)
            self._log_result(
                event="ai.webtool.research_plan_followup",
                request=request,
                mode="followup_plan" if is_followup_research else "auto_plan",
                operation="websearch",
                decision=step.reason,
                result=followup_result,
                extra={
                    "scope": request.scope,
                    "plan_domain": plan.domain,
                    "plan_status": plan.evidence_status,
                    "plan_warning_count": len(plan.warning_codes),
                    "source_host_count": plan.source_host_count,
                    "attempt": attempts,
                },
            )
            if followup_result.allowed and (followup_result.text or "").strip():
                result = _merge_search_results(result, followup_result)
        return result

    def _run_structured_evidence(self, request: WebResearchOrchestratorRequest, *, domain: str) -> DomainEvidenceResult:
        capability = f"{domain}_evidence"
        tool_request = build_webtool_request(
            trigger=WebtoolChatTrigger(capability=capability, query=request.normalized_text, url=""),
            user_id=request.message.from_user.id,
            role=request.role,
            chat_id=request.message.chat.id,
            topic_id=request.message.message_thread_id,
            locale=request.locale,
            evidence_domain=domain,
        )
        result = self._webtool_dispatcher.execute(tool_request)
        if result.allowed and (result.text or "").strip() and result.sources:
            fetched_at = datetime.now(UTC).replace(microsecond=0).isoformat()
            metadata = getattr(result, "metadata", {}) or {}
            source_names = tuple(str(name) for name in metadata.get("source_names", ()) if str(name).strip())
            sources = tuple(
                EvidenceSource(
                    source_names[idx] if idx < len(source_names) else _host_from_url(source) or domain,
                    source,
                    fetched_at,
                )
                for idx, source in enumerate(result.sources[:5])
            )
            return DomainEvidenceResult(
                domain=domain,
                status="confirmed",
                confidence=0.9,
                text=result.text,
                sources=sources,
                warnings=(f"{domain}_evidence_quota_checked",),
            )
        return DomainEvidenceResult(
            domain=domain,
            status="unavailable",
            confidence=0.0,
            text="",
            warnings=(str(getattr(result, "reason", "") or f"{domain}_evidence_unavailable"),),
        )

    def _log_domain_evidence(self, request: WebResearchOrchestratorRequest, result: DomainEvidenceResult) -> None:
        log_event(
            logger,
            logging.INFO,
            event="ai.webtool.domain_evidence",
            component=_COMPONENT,
            chat_id=request.message.chat.id,
            message_id=request.message.message_id,
            message_thread_id=request.message.message_thread_id,
            user_id=request.message.from_user.id,
            extra={
                "domain": result.domain,
                "status": result.status,
                "confidence": result.confidence,
                "source_count": len(result.sources),
                "warning_count": len(result.warnings),
                "scope": request.scope,
            },
        )

    def _run_primary_search(self, request: WebResearchOrchestratorRequest, *, decision_auto: Any, is_followup_research: bool) -> Any:
        tool_request = build_webtool_request(
            trigger=WebtoolChatTrigger(
                capability=decision_auto.capability,
                query=decision_auto.query,
                url=decision_auto.url,
            ),
            user_id=request.message.from_user.id,
            role=request.role,
            chat_id=request.message.chat.id,
            topic_id=request.message.message_thread_id,
            locale=request.locale,
            evidence_domain=classify_evidence_domain(request.normalized_text),
        )
        result = self._webtool_dispatcher.execute(tool_request)
        self._log_result(
            event="ai.webtool.auto_research",
            request=request,
            mode="followup" if is_followup_research else "auto",
            operation=decision_auto.capability,
            decision=decision_auto.reason,
            result=result,
            extra={"scope": request.scope},
        )
        return result

    def _run_retry_search(
        self,
        request: WebResearchOrchestratorRequest,
        *,
        decision_auto: Any,
        retry_query: str,
        is_followup_research: bool,
    ) -> Any:
        retry_request = build_webtool_request(
            trigger=WebtoolChatTrigger(capability="websearch", query=retry_query, url=""),
            user_id=request.message.from_user.id,
            role=request.role,
            chat_id=request.message.chat.id,
            topic_id=request.message.message_thread_id,
            locale=request.locale,
            evidence_domain=classify_evidence_domain(request.normalized_text),
        )
        result = self._webtool_dispatcher.execute(retry_request)
        self._log_result(
            event="ai.webtool.auto_research_retry",
            request=request,
            mode="followup" if is_followup_research else "auto",
            operation="websearch",
            decision=decision_auto.reason,
            result=result,
            extra={
                "retry_attempted": True,
                "retry_reason": "empty_result",
                "query_length": len(retry_query),
                "scope": request.scope,
            },
        )
        return result

    def _run_chain(
        self,
        request: WebResearchOrchestratorRequest,
        *,
        tool_result: Any,
        decision_auto: Any,
        is_followup_research: bool,
    ) -> tuple[tuple[str, ...], list[tuple[str, str, str]]]:
        chain_plan = build_research_chain_plan(
            request_text=request.normalized_text,
            capability=decision_auto.capability,
            reason=decision_auto.reason,
            search_text=getattr(tool_result, "text", "") or "",
            source_hosts=tuple(getattr(tool_result, "hosts", ()) or ()),
            source_urls=tuple(getattr(tool_result, "sources", ()) or ()),
            source_quality_reader=self._source_quality_reader,
        )
        chain_steps = tuple(step for step in chain_plan.steps if step.operation == "webscraping" and step.url)
        chain_urls = tuple(step.url for step in chain_steps)
        chain_extracts: list[tuple[str, str, str]] = []
        static_successes = 0
        browser_fallbacks_used = 0
        static_attempts = 0
        reason_buckets: dict[str, int] = {}
        content_length_buckets: dict[str, int] = {}
        error_class_buckets: dict[str, int] = {}
        timeout_count = 0

        def record_chain_attempt(result: Any, text_len: int) -> None:
            nonlocal timeout_count
            quality = classify_extraction_quality(
                getattr(result, "text", "") or "",
                min_chars=_AUTO_RESEARCH_CHAIN_MIN_EXTRACT_CHARS,
            )
            reason_bucket = _chain_failure_reason(result, text_len, quality_warnings=quality.warning_codes)
            reason_buckets[reason_bucket] = reason_buckets.get(reason_bucket, 0) + 1
            length_bucket = quality.text_length_bucket
            content_length_buckets[length_bucket] = content_length_buckets.get(length_bucket, 0) + 1
            if "timeout" in reason_bucket:
                timeout_count += 1
            error_value = getattr(result, "error", None) if result is not None else None
            if error_value is not None:
                error_class = type(error_value).__name__
                error_class_buckets[error_class] = error_class_buckets.get(error_class, 0) + 1

        for step in chain_steps:
            url = step.url
            if static_successes >= _AUTO_RESEARCH_CHAIN_MAX_STATIC_SUCCESSES:
                break
            chain_result = self._execute_url_tool(request, capability="webscraping", url=url)
            static_attempts += 1
            chain_text = _compact_chain_text(chain_result.text or "")
            record_chain_attempt(chain_result, len(chain_text))
            chain_quality = classify_extraction_quality(
                chain_text,
                min_chars=_AUTO_RESEARCH_CHAIN_MIN_EXTRACT_CHARS,
            )
            if chain_result.allowed and chain_quality.usable:
                chain_extracts.append(("webscraping", (tuple(chain_result.hosts or ()) or (_host_from_url(url),))[0], chain_text))
                static_successes += 1
                continue
            browser_decision = should_attempt_browser_fallback(
                request_text=request.normalized_text,
                url=url,
                search_text=getattr(tool_result, "text", "") or "",
                scrape_result=chain_result,
                scrape_quality=chain_quality,
                static_failure_count=static_attempts,
            )
            if browser_fallbacks_used < _AUTO_RESEARCH_CHAIN_MAX_BROWSER_FALLBACKS and browser_decision.enabled:
                browser_result = self._execute_url_tool(request, capability="browser", url=url)
                browser_fallbacks_used += 1
                browser_text = _compact_chain_text(browser_result.text or "")
                record_chain_attempt(browser_result, len(browser_text))
                browser_quality = classify_extraction_quality(
                    browser_text,
                    min_chars=_AUTO_RESEARCH_CHAIN_MIN_EXTRACT_CHARS,
                )
                if browser_result.allowed and browser_quality.usable:
                    chain_extracts.append(("browser", (tuple(browser_result.hosts or ()) or (_host_from_url(url),))[0], browser_text))
                    break

        log_event(
            logger,
            logging.INFO,
            event="ai.webtool.auto_research_chain",
            component=_COMPONENT,
            chat_id=request.message.chat.id,
            message_id=request.message.message_id,
            message_thread_id=request.message.message_thread_id,
            user_id=request.message.from_user.id,
            extra={
                "mode": "followup_chain" if is_followup_research else "auto_chain",
                "status": "success" if chain_extracts else "no_usable_extraction",
                **_chain_diagnostic_snapshot(
                    search_hosts=tuple(tool_result.hosts or ()),
                    chain_urls=chain_urls,
                    static_attempts=static_attempts,
                    browser_attempts=browser_fallbacks_used,
                    chain_extracts=chain_extracts,
                    reason_buckets=reason_buckets,
                    content_length_buckets=content_length_buckets,
                    timeout_count=timeout_count,
                    error_class_buckets=error_class_buckets,
                ),
                "plan_status": chain_plan.evidence_status,
                "plan_warning_count": len(chain_plan.warning_codes),
                "plan_step_count": len(chain_plan.steps),
                "plan_reason_codes": tuple(sorted(set(chain_plan.warning_codes))),
                "scope": request.scope,
                "operation": decision_auto.capability,
            },
        )
        return chain_urls, chain_extracts

    def _execute_url_tool(self, request: WebResearchOrchestratorRequest, *, capability: str, url: str) -> Any:
        trigger = WebtoolChatTrigger(capability=capability, query="", url=url)
        tool_request = build_webtool_request(
            trigger=trigger,
            user_id=request.message.from_user.id,
            role=request.role,
            chat_id=request.message.chat.id,
            topic_id=request.message.message_thread_id,
            locale=request.locale,
            evidence_domain=classify_evidence_domain(request.normalized_text),
        )
        return self._webtool_dispatcher.execute(tool_request)

    def _record_chain_source_observations(
        self,
        *,
        request: WebResearchOrchestratorRequest,
        chain_urls: tuple[str, ...],
        chain_extracts: tuple[tuple[str, str, str], ...],
    ) -> None:
        if self._source_observation_writer is None:
            return
        domain = classify_evidence_domain(request.normalized_text)
        if domain == "generic":
            return
        if chain_extracts:
            hosts = _normalize_source_hosts(
                source_hosts=tuple(host for _, host, _ in chain_extracts),
                source_urls=(),
            )
            outcome = "confirmed"
            warnings: tuple[str, ...] = ()
            metadata: dict[str, object] = {
                "operation": "source_chain",
                "status": "confirmed",
            }
            confidence = 0.8
        elif chain_urls:
            hosts = _normalize_source_hosts(source_hosts=(), source_urls=chain_urls)
            outcome = "source_check_inconclusive"
            warnings = ("source_check_inconclusive",)
            metadata = {
                "operation": "source_chain",
                "status": "source_check_inconclusive",
                "reason": "no_usable_extract",
            }
            confidence = 0.0
        else:
            return
        if not hosts:
            return
        try:
            self._source_observation_writer.record_observation(
                provider_name="webresearch_source_chain",
                source_name="source_chain",
                domain=domain,
                outcome=outcome,
                confidence=confidence,
                source_hosts=hosts,
                source_urls=(),
                source_count=len(hosts),
                warning_codes=warnings,
                warning_count=len(warnings),
                metadata=metadata,
            )
        except Exception as exc:
            log_event(
                logger,
                logging.WARNING,
                event="research_source_chain_observation_write_failed",
                component=_COMPONENT,
                reason_code="observation_write_failed",
                extra={
                    "domain": domain,
                    "host_count": len(hosts),
                    "error_class": type(exc).__name__,
                },
            )

    def _log_result(
        self,
        *,
        event: str,
        request: WebResearchOrchestratorRequest,
        mode: str,
        operation: str,
        decision: str,
        result: Any,
        extra: dict[str, Any],
    ) -> None:
        log_event(
            logger,
            logging.INFO,
            event=event,
            component=_COMPONENT,
            chat_id=request.message.chat.id,
            message_id=request.message.message_id,
            message_thread_id=request.message.message_thread_id,
            user_id=request.message.from_user.id,
            extra={
                "mode": mode,
                "operation": operation,
                "decision": decision,
                "status": "allow" if result.allowed else "deny",
                "reason": result.reason,
                "error_class": type(result.error).__name__ if result.error else None,
                "source_count": len(result.sources),
                "host_count": len(result.hosts),
                **extra,
            },
        )


def _should_continue_with_generic_websearch(result: DomainEvidenceResult) -> bool:
    """Allow sports fallback to configured websearch when no profiled source exists."""
    if result.domain != "sports" or result.status == "needs_profiled_web_research":
        return False
    return any(
        warning.startswith("sports_domain_profile_not_configured")
        or warning.startswith("sports_domain_profile_no_usable_source:")
        for warning in result.warnings
    )


def _with_profiled_source_query(decision_auto: Any, result: DomainEvidenceResult) -> Any:
    if result.domain != "sports" or result.status != "needs_profiled_web_research":
        return decision_auto
    if getattr(decision_auto, "capability", "") != "websearch":
        return decision_auto
    hosts = _learned_source_hosts_from_warnings(result.warnings)
    if not hosts:
        return decision_auto
    base_query = str(getattr(decision_auto, "query", "") or "").strip()
    if not base_query:
        return decision_auto
    site_filter = " OR ".join(f"site:{host}" for host in hosts[:2])
    query = f"{base_query} ({site_filter})"
    return SimpleNamespace(
        enabled=getattr(decision_auto, "enabled", True),
        capability=getattr(decision_auto, "capability", "websearch"),
        reason=getattr(decision_auto, "reason", ""),
        query=re.sub(r"\s+", " ", query).strip()[:180].rstrip(),
        url=getattr(decision_auto, "url", ""),
    )


def _learned_source_hosts_from_warnings(warnings: tuple[str, ...]) -> tuple[str, ...]:
    hosts: list[str] = []
    for warning in warnings:
        if not warning.startswith("learned_sources:"):
            continue
        for raw_host in warning.split(":", 1)[1].split("|"):
            host = _normalize_source_hosts(source_hosts=(raw_host,), source_urls=())
            if host and host[0] not in hosts:
                hosts.append(host[0])
    return tuple(hosts[:3])


def should_chain_auto_research(text: str, *, capability: str, reason: str | None = None) -> bool:
    """Return True for bounded follow-up extraction on current-data websearches."""
    if capability != "websearch":
        return False
    if reason == "user_feedback_followup":
        return True
    raw = text or ""
    if not raw.strip():
        return False
    if _AUTO_RESEARCH_CHAIN_EXPLICIT_CURRENT_PHRASE_RE.search(raw):
        return True
    has_sports_freshness = sports_query.has_sports_signal(raw)
    has_freshness = bool(_AUTO_RESEARCH_CHAIN_FRESHNESS_RE.search(raw)) or has_sports_freshness
    if not has_freshness:
        return False
    has_strong_freshness = bool(_AUTO_RESEARCH_CHAIN_STRONG_FRESHNESS_RE.search(raw)) or (
        sports_query.has_phase(raw) or sports_query.infer_need(raw) != "sport_context"
    )
    if _AUTO_RESEARCH_CHAIN_TIMELESS_EDU_RE.search(raw) and not has_strong_freshness:
        return False
    return True


def build_research_plan(
    *,
    request_text: str,
    capability: str,
    reason: str | None,
    search_text: str = "",
    source_hosts: tuple[str, ...] = (),
    source_urls: tuple[str, ...] = (),
    source_quality_reader: ResearchSourceQualityReader | None = None,
    allow_sports_result_followup: bool = True,
) -> ResearchPlan:
    """Plan a bounded extra search when initial web evidence is too narrow."""
    domain = classify_evidence_domain(request_text)
    hosts = _normalize_source_hosts(source_hosts=source_hosts, source_urls=source_urls)
    warnings: list[str] = []
    evidence_status = "adequate_initial_evidence"

    if (
        capability != "websearch"
        or reason == "user_feedback_followup"
        or not should_chain_auto_research(request_text, capability=capability, reason=reason)
    ):
        return ResearchPlan(domain=domain, evidence_status=evidence_status, source_host_count=len(hosts))

    if not hosts:
        warnings.append("no_source_hosts")
    elif len(hosts) < 2 and domain in {"news", "stock", "sports", "generic"}:
        warnings.append("single_source_host")

    source_quality = _assess_hosts_source_quality(domain=domain, hosts=hosts, reader=source_quality_reader)
    if source_quality:
        if source_quality.get("conflict_hosts"):
            warnings.append("source_observation_conflict")
        if source_quality.get("weak_hosts"):
            warnings.append("source_observation_weak")
    sports_result_assessment = _assess_sports_result_evidence(search_text, request_text=request_text)
    if allow_sports_result_followup and _is_concrete_sports_result_question(request_text) and not sports_result_assessment.confirmed:
        warnings.append("sports_result_opponent_score_missing")

    if not warnings:
        return ResearchPlan(domain=domain, evidence_status=evidence_status, source_host_count=len(hosts))

    query = _build_planned_followup_query(request_text=request_text, domain=domain, warnings=tuple(warnings))
    steps = (SearchPlanStep(operation="websearch", reason="weak_initial_evidence", query=query),) if query else ()
    return ResearchPlan(
        domain=domain,
        evidence_status="weak_initial_evidence",
        source_host_count=len(hosts),
        warning_codes=tuple(dict.fromkeys(warnings)),
        steps=steps,
    )


def build_research_chain_plan(
    *,
    request_text: str,
    capability: str,
    reason: str | None,
    search_text: str,
    source_hosts: tuple[str, ...] = (),
    source_urls: tuple[str, ...] = (),
    source_quality_reader: ResearchSourceQualityReader | None = None,
) -> ResearchPlan:
    """Plan bounded source-page checks after a search, with metadata-only reason codes."""
    domain = classify_evidence_domain(request_text)
    hosts = _normalize_source_hosts(source_hosts=source_hosts, source_urls=source_urls)
    urls = _select_chain_urls(source_urls)
    warnings: list[str] = []

    if capability != "websearch" or not should_chain_auto_research(request_text, capability=capability, reason=reason):
        return ResearchPlan(domain=domain, evidence_status="chain_not_applicable", source_host_count=len(hosts))

    search_quality = classify_extraction_quality(search_text, min_chars=_AUTO_RESEARCH_CHAIN_MIN_EXTRACT_CHARS)
    warnings.extend(_chain_plan_initial_warnings(domain=domain, hosts=hosts, urls=urls, search_quality=search_quality))
    source_quality = _assess_hosts_source_quality(domain=domain, hosts=hosts, reader=source_quality_reader)
    if source_quality:
        if source_quality.get("conflict_hosts"):
            warnings.append("source_observation_conflict")
        if source_quality.get("weak_hosts"):
            warnings.append("source_observation_weak")

    if _has_dynamic_page_hint(request_text=request_text, search_text=search_text, urls=urls, domain=domain):
        warnings.append("dynamic_page_hint")

    if not urls:
        return ResearchPlan(
            domain=domain,
            evidence_status="no_usable_source",
            source_host_count=len(hosts),
            warning_codes=tuple(dict.fromkeys(warnings or ["no_usable_source"])),
        )

    steps = tuple(SearchPlanStep(operation="webscraping", reason=_source_check_reason(url=url, domain=domain), url=url) for url in urls)
    status = "source_check_planned"
    if warnings:
        status = "weak_initial_evidence"
    return ResearchPlan(
        domain=domain,
        evidence_status=status,
        source_host_count=len(hosts),
        warning_codes=tuple(dict.fromkeys(warnings)),
        steps=steps,
    )


def build_query_planner_stage(
    *,
    request_text: str,
    is_triggered_path: bool = False,
    reply_context_text: str = "",
) -> QueryPlannerStageOutput:
    """Build the query-planner contract without executing any tool."""
    decision_auto = decide_auto_research(request_text)
    is_followup_research = False
    if not decision_auto.enabled and is_triggered_path and is_web_research_followup_feedback(request_text):
        followup_query = build_web_research_followup_query(
            feedback_text=request_text,
            context_text=reply_context_text,
        )
        if followup_query:
            decision_auto = SimpleNamespace(
                enabled=True,
                capability="websearch",
                reason="user_feedback_followup",
                query=followup_query,
                url="",
            )
            is_followup_research = True
    domain = classify_evidence_domain(request_text)
    capability = str(getattr(decision_auto, "capability", "") or "")
    reason = str(getattr(decision_auto, "reason", "") or "")
    query = str(getattr(decision_auto, "query", "") or "")
    url = str(getattr(decision_auto, "url", "") or "")
    enabled = bool(decision_auto.enabled)
    strategy = None
    if enabled:
        strategy = ResearchSearchStrategy(
            domain=domain,
            capability=capability,
            query=query,
            url=url,
            requires_source_check=should_chain_auto_research(request_text, capability=capability, reason=reason),
        )
    return QueryPlannerStageOutput(
        enabled=enabled,
        domain=domain,
        capability=capability,
        reason=reason,
        query=query,
        url=url,
        is_followup_research=is_followup_research,
        strategy=strategy,
    )


def build_source_selection_stage(
    *,
    request_text: str,
    search_execution: SearchExecutionStageOutput,
    source_quality_reader: ResearchSourceQualityReader | None = None,
    allow_sports_result_followup: bool = True,
) -> SourceSelectionStageOutput:
    """Build the source-selection contract from a completed search stage."""
    result = search_execution.result
    plan = build_research_plan(
        request_text=request_text,
        capability=search_execution.capability,
        reason=search_execution.reason,
        search_text=getattr(result, "text", "") or "",
        source_hosts=tuple(getattr(result, "hosts", ()) or ()),
        source_urls=tuple(getattr(result, "sources", ()) or ()),
        source_quality_reader=source_quality_reader,
        allow_sports_result_followup=allow_sports_result_followup,
    )
    chain_plan = build_research_chain_plan(
        request_text=request_text,
        capability=search_execution.capability,
        reason=search_execution.reason,
        search_text=getattr(result, "text", "") or "",
        source_hosts=tuple(getattr(result, "hosts", ()) or ()),
        source_urls=tuple(getattr(result, "sources", ()) or ()),
        source_quality_reader=source_quality_reader,
    )
    selected_urls = tuple(step.url for step in chain_plan.steps if step.operation == "webscraping" and step.url)
    selected_sources = _score_selected_sources(
        request_text=request_text,
        urls=selected_urls,
        domain=chain_plan.domain,
        warnings=chain_plan.warning_codes,
    )
    return SourceSelectionStageOutput(plan=plan, selected_urls=selected_urls, selected_sources=selected_sources)


def build_extraction_browser_stage(
    *,
    request_text: str,
    capability: str,
    reason: str | None,
    search_text: str,
    source_hosts: tuple[str, ...],
    source_urls: tuple[str, ...],
    extracts: tuple[tuple[str, str, str], ...] = (),
    source_quality_reader: ResearchSourceQualityReader | None = None,
) -> ExtractionBrowserStageOutput:
    """Build the extraction/browser contract from selected and checked sources."""
    plan = build_research_chain_plan(
        request_text=request_text,
        capability=capability,
        reason=reason,
        search_text=search_text,
        source_hosts=source_hosts,
        source_urls=source_urls,
        source_quality_reader=source_quality_reader,
    )
    attempted_urls = tuple(step.url for step in plan.steps if step.operation == "webscraping" and step.url)
    return ExtractionBrowserStageOutput(plan=plan, attempted_urls=attempted_urls, extracts=extracts)


def validate_research_evidence(
    *,
    request_text: str,
    search_execution: SearchExecutionStageOutput,
    extraction: ExtractionBrowserStageOutput | None = None,
) -> EvidenceValidationStageOutput:
    """Validate that final answer evidence is explicit checked evidence where required."""
    result = search_execution.result
    domain = classify_evidence_domain(request_text)
    search_text = getattr(result, "text", "") or ""
    search_hosts = tuple(getattr(result, "hosts", ()) or ())
    checked_extracts = tuple(extraction.extracts if extraction is not None else ())
    warnings: list[str] = []

    if checked_extracts:
        return EvidenceValidationStageOutput(
            domain=domain,
            status="checked_evidence_available",
            can_synthesize=True,
            search_text=search_text,
            search_hosts=search_hosts,
            checked_extracts=checked_extracts,
        )

    if extraction is not None:
        warnings = list(extraction.plan.warning_codes)
        status = "source_check_inconclusive" if extraction.attempted_urls else "snippet_only_result"
    else:
        status = "search_result_only"

    chain_required = should_chain_auto_research(
        request_text,
        capability=search_execution.capability,
        reason=search_execution.reason,
    )
    if chain_required:
        primary_warning = "snippet_only_result" if not (extraction and extraction.attempted_urls) else "source_check_inconclusive"
        warnings = [primary_warning, *(warning for warning in warnings if warning != primary_warning)]
        return EvidenceValidationStageOutput(
            domain=domain,
            status=status,
            can_synthesize=False,
            search_text=search_text,
            search_hosts=search_hosts,
            checked_extracts=(),
            warnings=tuple(dict.fromkeys(warnings)),
        )

    return EvidenceValidationStageOutput(
        domain=domain,
        status=status,
        can_synthesize=True,
        search_text=search_text,
        search_hosts=search_hosts,
        checked_extracts=(),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def synthesize_research_answer(
    *,
    validation: EvidenceValidationStageOutput,
    capability: str,
    locale: str,
    followup: bool = False,
) -> AnswerSynthesisStageOutput:
    """Produce the answer-synthesizer contract from validated evidence only."""
    if not validation.can_synthesize:
        response = format_domain_fail_closed_response(
            domain=validation.domain,
            locale=locale,
            warnings=tuple(dict.fromkeys((validation.status, *validation.warnings))),
        )
        return AnswerSynthesisStageOutput(user_response=response)
    if validation.checked_extracts:
        return AnswerSynthesisStageOutput(
            auto_note=_format_auto_research_chained_success_note(
                capability=capability,
                search_text=validation.search_text,
                search_hosts=validation.search_hosts,
                extracts=validation.checked_extracts,
                followup=followup,
                locale=locale,
            )
        )
    return AnswerSynthesisStageOutput(
        auto_note=_format_auto_research_success_note(
            capability=capability,
            text=validation.search_text,
            hosts=validation.search_hosts,
            locale=locale,
        )
    )


def should_attempt_browser_fallback(
    *,
    request_text: str,
    url: str,
    search_text: str,
    scrape_result: Any,
    scrape_quality: Any,
    static_failure_count: int,
) -> BrowserFallbackDecision:
    """Decide whether a failed static source check deserves one bounded browser attempt."""
    if getattr(scrape_result, "allowed", False) and getattr(scrape_quality, "usable", False):
        return BrowserFallbackDecision(False)
    quality_warnings = tuple(getattr(scrape_quality, "warning_codes", ()) or ())
    failure_reason = _chain_failure_reason(
        scrape_result,
        int(getattr(scrape_quality, "text_length", 0) or 0),
        quality_warnings=quality_warnings,
    )
    domain = classify_evidence_domain(request_text)
    if "extraction_js_placeholder" in quality_warnings:
        return BrowserFallbackDecision(True, "js_placeholder")
    if _has_dynamic_page_hint(request_text=request_text, search_text=search_text, urls=(url,), domain=domain):
        return BrowserFallbackDecision(True, "dynamic_page_hint")
    if failure_reason in {"empty_text", "empty_result", "too_short", "extraction_too_short"}:
        return BrowserFallbackDecision(True, "static_extraction_too_weak")
    if failure_reason in {"timeout", "provider_unavailable"} and static_failure_count >= 1:
        return BrowserFallbackDecision(True, "static_provider_failure")
    if failure_reason.startswith("http_error") and static_failure_count >= 1:
        if domain in {"stock", "sports", "news", "weather", "crypto"}:
            return BrowserFallbackDecision(True, "domain_source_blocked")
        return BrowserFallbackDecision(True, "static_source_blocked")
    return BrowserFallbackDecision(False)


def sanitize_auto_research_user_response(text: str) -> str:
    """Remove internal webtool pipeline terms from an auto-research answer."""
    cleaned = sanitize_webtool_user_facing_text(text)
    for pattern, replacement in _AUTO_RESEARCH_TECHNICAL_RESPONSE_REPLACEMENTS:
        cleaned = pattern.sub(replacement, cleaned)
    return cleaned


def _format_sports_result_unconfirmed_response(
    *,
    request_text: str,
    search_text: str,
    extracts: tuple[tuple[str, str, str], ...],
    locale: str,
) -> str:
    if not _is_concrete_sports_result_question(request_text):
        return ""
    filtered_search, filtered_extracts = _filter_sports_result_evidence(
        request_text=request_text,
        search_text=search_text,
        extracts=extracts,
    )
    evidence_text = "\n".join((filtered_search, *(text for _, _, text in filtered_extracts)))
    assessment = _assess_sports_result_evidence(evidence_text, request_text=request_text)
    if assessment.confirmed:
        return ""
    return format_domain_fail_closed_response(
        domain="sports",
        locale=locale,
        warnings=("sports_result_opponent_score_not_confirmed",),
    )


def _filter_sports_result_evidence(
    *,
    request_text: str,
    search_text: str,
    extracts: tuple[tuple[str, str, str], ...],
) -> tuple[str, tuple[tuple[str, str, str], ...]]:
    if not _is_concrete_sports_result_question(request_text):
        return search_text, extracts
    relevant_search = search_text if _sports_result_text_matches_scope(search_text, request_text=request_text) else ""
    relevant_extracts = tuple(
        (operation, host, text)
        for operation, host, text in extracts
        if _sports_result_text_matches_scope(text, request_text=request_text)
    )
    return relevant_search, relevant_extracts


def _is_concrete_sports_result_question(text: str) -> bool:
    terms = sports_query.query_terms(text)
    return (
        classify_evidence_domain(text) == "sports"
        and terms.get("need") == "sport_result"
        and bool(terms.get("competition"))
        and bool(terms.get("year"))
        and bool(sports_query.first_team(text))
    )


def _sports_result_text_matches_scope(text: str, *, request_text: str) -> bool:
    raw = text or ""
    if not raw.strip():
        return False
    team = sports_query.first_team(request_text)
    if team and not _contains_sports_scope_token(raw, team):
        return False
    terms = sports_query.query_terms(request_text)
    year = terms.get("year")
    if year and str(year) not in raw:
        return False
    competition = terms.get("competition")
    if competition and not _contains_sports_scope_token(raw, str(competition)):
        return False
    phase = terms.get("phase")
    if (
        phase
        and not _contains_sports_scope_token(raw, str(phase))
        and not _sports_result_has_opponent_score(raw, request_text=request_text)
    ):
        return False
    return True


def _contains_sports_scope_token(text: str, token: str) -> bool:
    normalized_text = sports_query.normalize_search_terms(text).casefold()
    normalized_token = sports_query.normalize_search_terms(token).casefold()
    return bool(normalized_token and normalized_token in normalized_text)


def _sports_result_has_opponent_score(text: str, *, request_text: str) -> bool:
    return _assess_sports_result_evidence(text, request_text=request_text).confirmed


def _assess_sports_result_evidence(text: str, *, request_text: str) -> SportsResultEvidenceAssessment:
    raw = text or ""
    if not raw.strip():
        return SportsResultEvidenceAssessment(False, 0.0, warnings=("empty_evidence",))
    if not _is_concrete_sports_result_question(request_text):
        return SportsResultEvidenceAssessment(False, 0.0, warnings=("not_concrete_sports_result_question",))
    matched_units: list[str] = []
    weak_units: list[str] = []
    for unit in _sports_result_evidence_units(raw):
        if not _sports_result_unit_matches_scope(unit, request_text=request_text):
            continue
        for match in _SPORTS_RESULT_SCORE_RE.finditer(unit):
            window = unit[max(0, match.start() - 120) : match.end() + 120]
            has_relation = bool(_SPORTS_RESULT_RELATION_RE.search(window))
            has_opponent = _sports_result_window_has_opponent(window, request_text=request_text)
            if has_relation and has_opponent:
                matched_units.append(unit)
                continue
            team = re.escape(sports_query.first_team(request_text) or "")
            has_team_score_opponent_shape = bool(team and re.search(
                rf"(?:\b{team}\b.{0,80}{_SPORTS_RESULT_SCORE_RE.pattern}.{0,80}\b[A-ZÄÖÜ][A-Za-zÄÖÜäöüß.-]{{2,}}\b|"
                rf"\b[A-ZÄÖÜ][A-Za-zÄÖÜäöüß.-]{{2,}}\b.{0,80}{_SPORTS_RESULT_SCORE_RE.pattern}.{0,80}\b{team}\b)",
                window,
            ))
            if has_team_score_opponent_shape and has_opponent:
                matched_units.append(unit)
            else:
                weak_units.append(unit)
    if matched_units:
        confidence = 0.92 if len(matched_units) > 1 else 0.82
        return SportsResultEvidenceAssessment(True, confidence, units=tuple(matched_units))
    warnings = ("sports_result_opponent_score_not_local",) if weak_units else ("sports_result_opponent_score_missing",)
    return SportsResultEvidenceAssessment(False, 0.35 if weak_units else 0.0, units=tuple(weak_units), warnings=warnings)


def _sports_result_evidence_units(text: str) -> tuple[str, ...]:
    return tuple(unit.strip() for unit in re.split(r"(?<=[.!?])\s+|\n+", text or "") if unit.strip())


def _sports_result_unit_matches_scope(unit: str, *, request_text: str) -> bool:
    if not _contains_sports_scope_token(unit, sports_query.first_team(request_text) or ""):
        return False
    terms = sports_query.query_terms(request_text)
    year = terms.get("year")
    if year and str(year) not in unit:
        return False
    competition = terms.get("competition")
    if competition and not _contains_sports_scope_token(unit, str(competition)):
        return False
    return True


def _sports_result_window_has_opponent(window: str, *, request_text: str) -> bool:
    requested_team = sports_query.first_team(request_text) or ""
    for team in sports_query.matching_teams(window):
        if team != requested_team:
            return True
    requested_parts = {part.casefold() for part in re.findall(r"[A-Za-zÄÖÜäöüß]+", requested_team)}
    for candidate in re.findall(r"\b[A-ZÄÖÜ][A-Za-zÄÖÜäöüß.-]{2,}\b", window or ""):
        normalized = candidate.strip(".-").casefold()
        if not normalized or normalized in requested_parts or normalized in _SPORTS_RESULT_OPPONENT_STOPWORDS:
            continue
        if normalized.isdigit():
            continue
        return True
    return False


def _format_domain_chain_unconfirmed_response(*, request_text: str, locale: str, reason: str) -> str:
    domain = classify_evidence_domain(request_text)
    if domain not in {"weather", "crypto", "stock", "sports", "news"}:
        return ""
    return format_domain_fail_closed_response(domain=domain, locale=locale, warnings=(reason,))


def _format_news_insufficient_sources_response(
    *,
    request_text: str,
    extract_hosts: tuple[str, ...],
    locale: str,
    source_quality: dict[str, Any] | None = None,
) -> str:
    if classify_evidence_domain(request_text) != "news":
        return ""
    usable_hosts = {host for host in extract_hosts if host}
    if source_quality:
        usable_hosts = set(source_quality.get("usable_hosts", usable_hosts))
    if len(usable_hosts) >= 2 and not (source_quality or {}).get("conflict_hosts"):
        return ""
    status = "fewer than two checked news sources in this attempt"
    if source_quality and source_quality.get("conflict_hosts"):
        status = "stored source observations indicate conflicting recent evidence for one or more checked sources"
    elif source_quality and source_quality.get("weak_hosts"):
        status = "stored source observations mark one or more checked sources as weak in this domain"
    if (locale or "").lower().startswith("en"):
        return (
            "I cannot reliably confirm the requested current news from multiple checked sources right now. "
            "I will not summarize news from snippets or a single uncorroborated source.\n"
            f"Source/status: {status}."
        )
    return (
        "Ich kann die angefragten aktuellen Nachrichten gerade nicht aus mehreren geprüften Quellen bestätigen. "
        "Ich fasse keine News aus Such-Snippets oder nur einer unbestätigten Quelle zusammen.\n"
        f"Quelle/Stand: {status}."
    )


def _format_news_corroboration_response(
    *,
    request_text: str,
    extracts: tuple[tuple[str, str, str], ...],
    locale: str,
) -> str:
    if classify_evidence_domain(request_text) != "news":
        return ""
    result = assess_news_corroboration(extracts)
    if result.corroborated:
        return ""
    return _format_news_uncorroborated_response(result=result, locale=locale)


def _format_news_uncorroborated_response(*, result: NewsCorroborationResult, locale: str) -> str:
    status = _news_corroboration_status_text(result)
    if (locale or "").lower().startswith("en"):
        return (
            "I cannot reliably confirm the requested current news at claim level right now. "
            "The checked sources did not provide enough independent, current corroboration from multiple checked sources, so I will not smooth over uncertainty or conflicts.\n"
            f"Source/status: {status}."
        )
    return (
        "Ich kann die angefragten aktuellen Nachrichten gerade nicht auf Aussage-Ebene belastbar bestätigen. "
        "Die geprüften Quellen liefern nicht genug unabhängige, aktuelle Bestätigung aus mehreren geprüften Quellen; Unsicherheiten oder Konflikte glätte ich deshalb nicht.\n"
        f"Quelle/Stand: {status}."
    )


def _news_corroboration_status_text(result: NewsCorroborationResult) -> str:
    if result.status == "conflicting_claims":
        hosts = ", ".join(result.conflict_hosts[:3]) or "checked sources"
        return f"conflicting checked claims across {hosts}"
    if result.status == "stale_sources":
        hosts = ", ".join(result.stale_hosts[:3]) or "checked sources"
        return f"only stale recognizable publication dates from {hosts}"
    if result.status == "weak_repeated_snippet":
        hosts = ", ".join(result.supporting_hosts[:3]) or "checked sources"
        return f"multiple hosts repeat the same weak snippet-like claim from {hosts}"
    if result.status == "no_corroborated_claim":
        stale = f"; stale hosts ignored: {', '.join(result.stale_hosts[:3])}" if result.stale_hosts else ""
        return f"no same claim confirmed by two current checked sources{stale}"
    return "no compact claim candidates found in checked source text"


def _assess_chain_source_quality(
    *,
    domain: str,
    extracts: tuple[tuple[str, str, str], ...],
    reader: ResearchSourceQualityReader | None,
) -> dict[str, Any] | None:
    if reader is None or domain == "generic":
        return None
    hosts = tuple(dict.fromkeys(host for _, host, _ in extracts if host))
    if not hosts:
        return None
    try:
        records = reader.assess_hosts(domain=domain, hosts=hosts)
    except Exception:
        logger.exception("research source quality read failed")
        return None
    usable_hosts: set[str] = set(hosts)
    weak_hosts: set[str] = set()
    conflict_hosts: set[str] = set()
    for record in records:
        host = str(getattr(record, "host", "") or "")
        if not host:
            continue
        conflict_count = int(getattr(record, "conflict_count", 0) or 0)
        failure_count = int(getattr(record, "failure_count", 0) or 0)
        success_count = int(getattr(record, "success_count", 0) or 0)
        if conflict_count > 0:
            conflict_hosts.add(host)
            usable_hosts.discard(host)
        elif failure_count > success_count:
            weak_hosts.add(host)
            usable_hosts.discard(host)
    return {
        "usable_hosts": tuple(sorted(usable_hosts)),
        "weak_hosts": tuple(sorted(weak_hosts)),
        "conflict_hosts": tuple(sorted(conflict_hosts)),
    }


def _assess_hosts_source_quality(
    *,
    domain: str,
    hosts: tuple[str, ...],
    reader: ResearchSourceQualityReader | None,
) -> dict[str, Any] | None:
    if reader is None or domain == "generic" or not hosts:
        return None
    return _assess_chain_source_quality(
        domain=domain,
        extracts=tuple(("websearch", host, "host-only source quality check") for host in hosts),
        reader=reader,
    )


def _normalize_source_hosts(*, source_hosts: tuple[str, ...], source_urls: tuple[str, ...]) -> tuple[str, ...]:
    hosts: list[str] = []
    for raw in (*source_hosts, *source_urls):
        host = _host_from_url(raw) if "://" in (raw or "") else (raw or "").strip().lower()
        host = host.removeprefix("www.")
        if host and host not in hosts:
            hosts.append(host)
    return tuple(hosts[:10])


def _build_planned_followup_query(*, request_text: str, domain: str, warnings: tuple[str, ...]) -> str:
    base = build_empty_result_retry_query(request_text)
    if not base:
        base = re.sub(r"https?://\S+", " ", request_text or "")
        base = re.sub(r"\s+", " ", base).strip()
    if not base:
        return ""
    suffixes = {
        "news": "latest confirmed multiple sources",
        "stock": "current official market data source",
        "sports": "current official standings results",
        "crypto": "current price official market data",
        "weather": "current forecast official weather source",
    }
    suffix = suffixes.get(domain, "current corroborating sources")
    if domain == "sports" and "sports_result_opponent_score_missing" in warnings:
        suffix = "current match result opponent score official sources"
    if any("conflict" in warning for warning in warnings):
        suffix = f"{suffix} corroboration"
    query = f"{base} {suffix}"
    query = re.sub(r"\s+", " ", query).strip()
    return query[:140].rstrip()


def _chain_plan_initial_warnings(
    *,
    domain: str,
    hosts: tuple[str, ...],
    urls: tuple[str, ...],
    search_quality: Any,
) -> tuple[str, ...]:
    warnings: list[str] = []
    if not urls:
        warnings.append("no_usable_source")
    if not hosts:
        warnings.append("no_source_hosts")
    elif len(hosts) < 2 and domain in {"news", "stock", "sports", "weather", "crypto"}:
        warnings.append("single_source_host")
    warning_codes = tuple(getattr(search_quality, "warning_codes", ()) or ())
    if "extraction_snippet_like" in warning_codes:
        warnings.append("snippet_only_result")
    if "extraction_too_short" in warning_codes:
        warnings.append("search_text_too_short")
    if "extraction_conflicting_or_unconfirmed" in warning_codes:
        warnings.append("search_text_conflict")
    return tuple(dict.fromkeys(warnings))


def _source_check_reason(*, url: str, domain: str) -> str:
    if domain in {"stock", "sports"}:
        return "dynamic_domain_source_check"
    if _url_has_dynamic_hint(url):
        return "dynamic_url_source_check"
    return "source_confirmation_check"


def _has_dynamic_page_hint(*, request_text: str, search_text: str, urls: tuple[str, ...], domain: str) -> bool:
    if domain in {"stock", "sports"}:
        return True
    haystack = " ".join((request_text or "", search_text or "")).lower()
    dynamic_terms = (
        "table",
        "tabelle",
        "standings",
        "spielplan",
        "fixtures",
        "results",
        "ergebnisse",
        "score",
        "live",
        "ticker",
        "chart",
        "quote",
        "market",
        "kurs",
        "preis",
        "price",
        "loading",
        "javascript",
    )
    if any(term in haystack for term in dynamic_terms):
        return True
    return any(_url_has_dynamic_hint(url) for url in urls)


def _url_has_dynamic_hint(url: str) -> bool:
    lowered = (url or "").lower()
    dynamic_parts = (
        "finance",
        "market",
        "quote",
        "ticker",
        "stock",
        "sport",
        "score",
        "standings",
        "table",
        "tabelle",
        "fixture",
        "result",
        "live",
        "chart",
    )
    return any(part in lowered for part in dynamic_parts)


def _merge_search_results(primary: Any, followup: Any) -> Any:
    primary_text = (getattr(primary, "text", "") or "").strip()
    followup_text = (getattr(followup, "text", "") or "").strip()
    text = "\n".join(part for part in (primary_text, followup_text) if part)
    sources = tuple(dict.fromkeys((*tuple(getattr(primary, "sources", ()) or ()), *tuple(getattr(followup, "sources", ()) or ()))))
    hosts = _normalize_source_hosts(
        source_hosts=tuple(dict.fromkeys((*tuple(getattr(primary, "hosts", ()) or ()), *tuple(getattr(followup, "hosts", ()) or ())))),
        source_urls=sources,
    )
    return SimpleNamespace(
        allowed=True,
        decision=getattr(followup, "decision", getattr(primary, "decision", "allow")),
        reason="search_completed_with_planned_followup",
        text=text,
        sources=sources,
        hosts=hosts,
        error=None,
    )


def _format_weather_unconfirmed_response(*, request_text: str, search_text: str, search_hosts: tuple[str, ...], locale: str) -> str:
    """Return a deterministic fallback when weather only has unverified search text."""
    if not _WEATHER_INTENT_RE.search(request_text or ""):
        return ""
    location = _weather_location_label(request_text)
    host_text = ", ".join(host for host in search_hosts[:3] if host) or "Websuche"
    if (locale or "").lower().startswith("en"):
        target = f" for {location}" if location else ""
        return (
            f"I cannot reliably confirm the current weather{target} right now. "
            "The web search returned only unverified result text, and the linked weather source could not be checked in this attempt. "
            "I am leaving out the unconfirmed weather values instead of presenting them as a forecast.\n"
            f"Source/status: {host_text}; current web search, source page not confirmed."
        )
    target = f" für {location}" if location else ""
    return (
        f"Ich kann das aktuelle Wetter{target} gerade nicht belastbar bestätigen. "
        "Die Websuche lieferte nur unbestätigten Ergebnistext; die verlinkte Wetterquelle konnte in diesem Versuch nicht geprüft werden. "
        "Ich lasse die unbestätigten Wetterwerte deshalb weg, statt sie als Vorhersage auszugeben.\n"
        f"Quelle/Stand: {host_text}; aktuelle Websuche, Detailquelle nicht bestätigt."
    )


def _format_weather_no_result_response(*, request_text: str, reason: str | None, locale: str) -> str:
    if not _WEATHER_INTENT_RE.search(request_text or ""):
        return ""
    location = _weather_location_label(request_text)
    reason_text = _AUTO_RESEARCH_NO_RESULT_TEXT.get((reason or "").strip(), "the attempt returned no usable live result")
    if (locale or "").lower().startswith("en"):
        target = f" for {location}" if location else ""
        return (
            f"I cannot reliably confirm the current weather{target} right now. "
            f"The live web lookup did not return a usable weather source ({reason_text}), so I will not guess from memory.\n"
            "Source/status: no confirmed live weather source in this attempt."
        )
    target = f" für {location}" if location else ""
    return (
        f"Ich kann das aktuelle Wetter{target} gerade nicht belastbar bestätigen. "
        "Die Live-Websuche hat in diesem Versuch keine verwertbare Wetterquelle geliefert; ich rate deshalb nicht aus Vorwissen.\n"
        "Quelle/Stand: keine bestätigte Live-Wetterquelle in diesem Versuch."
    )


def _weather_location_label(text: str) -> str:
    match = _WEATHER_LOCATION_RE.search(text or "")
    if not match:
        return ""
    location = re.sub(r"\s+", " ", match.group(1)).strip(" .,!?:;")
    location = re.sub(r"\b(?:heute|morgen|today|tomorrow|jetzt|aktuell)\b.*$", "", location, flags=re.IGNORECASE).strip()
    return location[:80]


def _host_from_url(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def _select_chain_urls(sources: tuple[str, ...] | list[str] | None, *, max_urls: int = _AUTO_RESEARCH_CHAIN_MAX_URLS) -> tuple[str, ...]:
    selected: list[str] = []
    seen_hosts: set[str] = set()
    candidates = sorted(
        ((source or "").strip() for source in sources or ()),
        key=lambda value: 0 if value.startswith("https://") else 1,
    )
    for raw in candidates:
        if not raw.startswith(("http://", "https://")):
            continue
        if _should_skip_chain_url(raw):
            continue
        host = _host_from_url(raw)
        dedupe_key = host or raw
        if dedupe_key in seen_hosts:
            continue
        selected.append(raw)
        seen_hosts.add(dedupe_key)
        if len(selected) >= max_urls:
            break
    return tuple(selected)


def _score_selected_sources(
    *,
    request_text: str,
    urls: tuple[str, ...],
    domain: str,
    warnings: tuple[str, ...],
) -> tuple[SelectedResearchSource, ...]:
    selected: list[SelectedResearchSource] = []
    lowered_request = (request_text or "").casefold()
    for url in urls:
        host = _host_from_url(url)
        lowered_url = url.casefold()
        score = 0.55
        reasons: list[str] = ["linked_search_source"]
        if url.startswith("https://"):
            score += 0.10
            reasons.append("https")
        if domain in {"stock", "sports"} and _url_has_dynamic_hint(url):
            score += 0.10
            reasons.append("dynamic_domain_hint")
        if domain != "generic" and domain in lowered_url:
            score += 0.08
            reasons.append("domain_term_match")
        if host and any(part and part in lowered_url for part in re.findall(r"[a-zäöüß0-9]{4,}", lowered_request)[:8]):
            score += 0.07
            reasons.append("query_term_overlap")
        if "single_source_host" in warnings:
            score -= 0.10
            reasons.append("single_source_penalty")
        selected.append(
            SelectedResearchSource(
                url=url,
                host=host,
                score=round(max(0.0, min(score, 1.0)), 2),
                reason=":".join(reasons),
            )
        )
    return tuple(selected)


def _should_skip_chain_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return True
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return True
    query = parse_qs(parsed.query, keep_blank_values=False)
    if any(
        key.lower() in {"url", "u", "target", "redirect", "redirect_url", "to"}
        and any(str(value).startswith(("http://", "https://")) for value in values)
        for key, values in query.items()
    ):
        return True
    path_parts = {part for part in parsed.path.lower().split("/") if part}
    query_keys = {key.lower() for key in query}
    if path_parts & {"search", "results"} and query_keys & {"q", "query", "p"}:
        return True
    return False


def _compact_chain_text(text: str, *, cap: int = _AUTO_RESEARCH_CHAIN_PER_PAGE_TEXT_CAP) -> str:
    compact = " ".join((text or "").split())
    if len(compact) > cap:
        compact = compact[:cap].rstrip() + " …"
    return compact


def _content_length_bucket(length: int) -> str:
    return extraction_length_bucket(length, min_chars=_AUTO_RESEARCH_CHAIN_MIN_EXTRACT_CHARS)


def _chain_failure_reason(result: Any, text_len: int, *, quality_warnings: tuple[str, ...] = ()) -> str:
    if result is None:
        return "missing_result"
    if not getattr(result, "allowed", False):
        reason = (getattr(result, "reason", "") or "denied").strip()
        if "timeout" in reason:
            return "timeout"
        if reason.startswith("http_error"):
            return "http_error"
        if "provider" in reason and "unavailable" in reason:
            return "provider_unavailable"
        if reason == "empty_result":
            return "empty_result"
        return reason[:64] or "denied"
    if text_len <= 0:
        return "empty_text"
    if text_len < _AUTO_RESEARCH_CHAIN_MIN_EXTRACT_CHARS:
        return "too_short"
    if quality_warnings:
        first = quality_warnings[0]
        return first.removeprefix("extraction_")[:64] or "low_quality_extraction"
    return "usable"


def _chain_diagnostic_snapshot(
    *,
    search_hosts: tuple[str, ...],
    chain_urls: tuple[str, ...],
    static_attempts: int,
    browser_attempts: int,
    chain_extracts: list[tuple[str, str, str]],
    reason_buckets: dict[str, int],
    content_length_buckets: dict[str, int],
    timeout_count: int,
    error_class_buckets: dict[str, int],
) -> dict[str, Any]:
    """Build metadata-only diagnostics for auto-research extraction chains."""
    selected_hosts = {_host_from_url(url) for url in chain_urls}
    selected_hosts.discard("")
    extraction_hosts = {host for _, host, _ in chain_extracts if host}
    return {
        "url_count": len(chain_urls),
        "attempted_url_count": len(chain_urls),
        "search_host_count": len(tuple(search_hosts or ())),
        "selected_url_host_count": len(selected_hosts),
        "extraction_host_count": len(extraction_hosts),
        "host_count": len(extraction_hosts),
        "static_attempt_count": static_attempts,
        "browser_attempt_count": browser_attempts,
        "browser_fallback_count": browser_attempts,
        "extract_count": len(chain_extracts),
        "skipped_url_count": max(0, len(chain_urls) - static_attempts),
        "failed_attempt_count": max(0, static_attempts + browser_attempts - len(chain_extracts)),
        "timeout_count": timeout_count,
        "reason_buckets": dict(sorted(reason_buckets.items())),
        "content_length_buckets": dict(sorted(content_length_buckets.items())),
        "error_class_buckets": dict(sorted(error_class_buckets.items())),
    }


def _target_answer_language_instruction(locale: str) -> str:
    if (locale or "").lower().startswith("en"):
        return (
            "Target answer language: English. Keep source names, team names, titles, "
            "and technical identifiers in their original wording when appropriate."
        )
    return (
        "Ziel-Antwortsprache: Deutsch. Übersetze oder verändere keine Quellennamen, "
        "Teamnamen, Titel, Zahlen, Datumsangaben oder technischen Bezeichner; übernimm "
        "sie im Original, wenn sie aus der Quelle stammen."
    )


def _format_auto_research_chained_success_note(
    *,
    capability: str,
    search_text: str,
    search_hosts: tuple[str, ...],
    extracts: tuple[tuple[str, str, str], ...],
    followup: bool = False,
    locale: str = "de",
) -> str:
    host_text = ", ".join(search_hosts[:5])
    evidence_lines: list[str] = []
    for operation, host, text in extracts:
        snippet = _compact_chain_text(text, cap=_AUTO_RESEARCH_CHAIN_SNIPPET_CAP)
        if snippet:
            evidence_lines.append(f"- {operation} host={host or 'unknown'}: {snippet}")
    evidence = "\n".join(evidence_lines)
    if len(evidence) > _AUTO_RESEARCH_CHAIN_FINAL_CAP:
        evidence = evidence[:_AUTO_RESEARCH_CHAIN_FINAL_CAP].rstrip() + " …"
    heading = "FOLLOW-UP AUTO-RESEARCH (LIVE WEB + SOURCE CHECK)" if followup else "AUTO-RESEARCH (LIVE WEB + SOURCE CHECK)"
    context_line = (
        "This is a bounded follow-up because user feedback requested more/different sources after a prior web/AI answer. "
        if followup else ""
    )
    return (
        f"{heading} — STRICT INSTRUCTION:\n"
        f"{_target_answer_language_instruction(locale)}\n"
        f"{context_line}A live {capability}/web tool result found candidate sources, and checked source text is available in this turn. Treat only the checked source evidence below as primary evidence for current facts.\n"
        "Do NOT claim or imply that the bot has no web tools, no live data capability, or cannot search the web.\n"
        "Use the checked source evidence; mention source hosts when relevant. The search-result snippet is discovery context only and must not be used as a factual source. Summarize compactly and do not reproduce raw page/tool output. Do NOT override it with stale memory/priors.\n"
        "Telegram formatting: use short paragraphs or bullet lists; do not use Markdown tables.\n"
        "User-facing wording: describe only the available web evidence and source certainty; do not expose retrieval pipeline details or diagnostic labels.\n"
        "Strict anti-hallucination: do NOT invent exact prices, rates, dates, levels, or news not supported by the supplied evidence. If exact values are absent, say the live evidence does not confirm the exact value.\n"
        f"Operation: {capability}\n"
        f"Search source hosts: {host_text}\n"
        f"Checked source evidence:\n{evidence}"
    )


def _format_auto_research_chain_no_confirmation_note(
    *,
    capability: str,
    search_text: str,
    search_hosts: tuple[str, ...],
    followup: bool = False,
    locale: str = "de",
) -> str:
    compact_search = compact_webtool_result_text(search_text, max_chars=_AUTO_RESEARCH_SEARCH_SUMMARY_CAP)
    host_text = ", ".join(search_hosts[:5])
    heading = "FOLLOW-UP AUTO-RESEARCH STATUS" if followup else "AUTO-RESEARCH STATUS"
    context_line = (
        "This was a bounded follow-up because user feedback requested more/different sources after a prior web/AI answer. "
        if followup else ""
    )
    return (
        f"{heading} — WEB SEARCH SUCCEEDED, SOURCE CHECK INCONCLUSIVE:\n"
        f"{_target_answer_language_instruction(locale)}\n"
        f"{context_line}A live {capability} succeeded in this turn, but checking the linked source pages produced no additional usable confirmation.\n"
        "Be transparent: use the web result text only as limited live context, and if evidence remains insufficient, say clearly that the available web results could not fully confirm the requested information.\n"
        "Summarize compactly and do not reproduce raw page/tool output.\n"
        "Telegram formatting: use short paragraphs or bullet lists; do not use Markdown tables.\n"
        "Do NOT say or imply that the bot has no web tools, no live data capability, or cannot search the web.\n"
        "User-facing wording: describe only the available web evidence and source certainty; do not expose retrieval pipeline details or diagnostic labels. For German answers, use natural wording like 'laut verfügbaren Web-Suchergebnissen' or 'eine zusätzliche Quellenbestätigung war diesmal nicht möglich'.\n"
        "Strict anti-hallucination: do NOT invent exact prices, rates, dates, levels, or news without reliable live confirmation from this turn.\n"
        f"Operation: {capability}\n"
        f"Web result text: {compact_search}\n"
        f"Search source hosts: {host_text}"
    )


def _format_auto_research_success_note(*, capability: str, text: str, hosts: tuple[str, ...], locale: str = "de") -> str:
    compact_text = compact_webtool_result_text(text, max_chars=_AUTO_RESEARCH_SEARCH_SUMMARY_CAP)
    host_text = ", ".join(hosts[:5])
    relevance_instruction = _sports_result_relevance_instruction(compact_text)
    return (
        "AUTO-RESEARCH (LIVE WEB) — STRICT INSTRUCTION:\n"
        f"{_target_answer_language_instruction(locale)}\n"
        f"A live {capability}/web tool result is available in this turn. Treat this fresh web context as primary evidence for current facts.\n"
        "Do NOT claim or imply that the bot has no web tools, no live data capability, or cannot search the web.\n"
        "Use the supplied web result text as primary evidence, cite or mention the source hosts when relevant, summarize compactly, do NOT reproduce raw tool output, and do NOT override it with stale memory/priors.\n"
        f"{relevance_instruction}"
        "Telegram formatting: use short paragraphs or bullet lists; do not use Markdown tables.\n"
        "User-facing wording: describe only the available web evidence and source certainty; do not expose retrieval pipeline details or diagnostic labels.\n"
        "Strict anti-hallucination: do NOT invent dates, prices, levels, or news not supported by the supplied live summary. "
        "If exact values are not in the supplied summary, state that the available live sources do not confirm that exact value; do not say no webtools.\n"
        "If sources conflict, say so transparently.\n"
        f"Operation: {capability}\n"
        f"Web result text: {compact_text}\n"
        f"Source hosts: {host_text}"
    )


def _sports_result_relevance_instruction(search_text: str) -> str:
    if classify_evidence_domain(search_text) != "sports":
        return ""
    if not sports_query.has_result_context(search_text):
        return ""
    return (
        "Sports result relevance: for a concrete team/competition/year result question, use only evidence that supports the requested team, current competition/year, and match/result intent. "
        "Ignore unrelated teams, other competitions, and historical tournament background. "
        "If no opponent plus score is supported by the supplied evidence, fail closed instead of filling gaps.\n"
    )


def _format_auto_research_no_result_note(*, capability: str, reason: str | None, locale: str = "de") -> str:
    normalized_reason = (reason or "").strip() or "no_usable_result"
    reason_text = _AUTO_RESEARCH_NO_RESULT_TEXT.get(normalized_reason, "the attempt returned no usable live result")
    return (
        "AUTO-RESEARCH STATUS — WEB ATTEMPTED, NO USABLE RESULT:\n"
        f"{_target_answer_language_instruction(locale)}\n"
        f"A live {capability} attempt was made in this turn, but {reason_text}. "
        f"Reason code: {normalized_reason}.\n"
        "Be transparent and precise: say the web search was attempted but this specific attempt produced no usable result, timed out, was limited, or the provider was unavailable. "
        "Do NOT say or imply that the bot has no web tools, no live data capability, or cannot search the web in general. "
        "Telegram formatting: use short paragraphs or bullet lists; do not use Markdown tables. "
        "If useful in German, say: 'Die Websuche wurde versucht, lieferte aber diesmal keine verwertbaren Treffer/keine Bestätigung.'\n"
        "Strict anti-hallucination: do NOT invent current facts, dates, prices, levels, or news without reliable live confirmation from this turn. "
        "Memory and model priors are not acceptable substitutes for live evidence when current external data was required."
    )


def _format_auto_research_retry_no_result_note(*, capability: str, reason: str | None, locale: str = "de") -> str:
    normalized_reason = (reason or "").strip() or "no_usable_result"
    reason_text = _AUTO_RESEARCH_NO_RESULT_TEXT.get(normalized_reason, "the retry returned no usable live result")
    return (
        "AUTO-RESEARCH STATUS — WEB ATTEMPTED, RETRY ALSO NO USABLE RESULT:\n"
        f"{_target_answer_language_instruction(locale)}\n"
        f"A live {capability} attempt was made in this turn, then exactly one simplified retry from the current user message was also attempted, but {reason_text}. "
        f"Final reason code: {normalized_reason}.\n"
        "Be transparent and precise: say live websearch was attempted but did not return usable results after retry, so no current value/fact could be confirmed from live sources in this turn. "
        "Do NOT say or imply that the bot has no web tools, no live data capability, or cannot search the web in general. "
        "Telegram formatting: use short paragraphs or bullet lists; do not use Markdown tables. "
        "If useful in German, say: 'Die Live-Websuche wurde versucht, lieferte aber auch nach einem vereinfachten Retry keine verwertbaren Treffer; ein aktueller Wert konnte nicht bestätigt werden.'\n"
        "Strict anti-hallucination: do NOT reuse old/stale prices, rates, dates, levels, news, or prior-answer context as an estimate. Do NOT provide an estimated current value when live confirmation is unavailable. "
        "Memory and model priors are not acceptable substitutes for live evidence when current external data was required."
    )
