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
from amo_bot.telegram.update_parser import TelegramMessage
from amo_bot.telegram.webtool_auto_research import decide_auto_research
from amo_bot.telegram.webtool_chat_integration import (
    WebtoolChatTrigger,
    build_empty_result_retry_query,
    build_web_research_followup_query,
    build_webtool_request,
    compact_webtool_result_text,
    is_web_research_followup_feedback,
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
_AUTO_RESEARCH_CHAIN_MAX_BROWSER_FALLBACKS = 1
_AUTO_RESEARCH_CHAIN_PER_PAGE_TEXT_CAP = 1500
_AUTO_RESEARCH_CHAIN_FINAL_CAP = 1600
_AUTO_RESEARCH_SEARCH_SUMMARY_CAP = 900
_AUTO_RESEARCH_CHAIN_SNIPPET_CAP = 500
_AUTO_RESEARCH_CHAIN_MIN_EXTRACT_CHARS = 40
_AUTO_RESEARCH_PLAN_MAX_FOLLOWUP_SEARCHES = 1
_AUTO_RESEARCH_CHAIN_FRESHNESS_RE = re.compile(
    r"\b(?:"
    r"current|aktuell(?:e[nrms]?)?|jetzt|heute|live|realtime|real-time|right\s+now|"
    r"derzeit|stand|status|neueste(?:n)?|latest|news|nachrichten|release|version|"
    r"update|verf(?:ü|ue)gbar(?:keit)?|availability|weather|wetter|traffic|verkehr|"
    r"outage|st(?:ö|oe)rung|kurs|preis|price|rate|market|markt|exchange|fx|"
    r"wm|weltmeisterschaft|world\s+cup|em|europameisterschaft|champions\s+league|"
    r"bundesliga|vorrunde|gruppenphase|spielplan|tabelle|ergebnis(?:se)?|"
    r"aufstellung(?:en)?|qualifikation"
    r")\b",
    re.IGNORECASE,
)
_AUTO_RESEARCH_CHAIN_STRONG_FRESHNESS_RE = re.compile(
    r"\b(?:"
    r"jetzt|heute|live|realtime|real-time|right\s+now|derzeit|neueste(?:n)?|latest|"
    r"news|nachrichten|release|version|update|verf(?:ü|ue)gbar(?:keit)?|availability|"
    r"weather|wetter|traffic|verkehr|outage|st(?:ö|oe)rung|kurs|preis|price|rate|market|markt|"
    r"vorrunde|gruppenphase|spielplan|tabelle|ergebnis(?:se)?|aufstellung(?:en)?|qualifikation"
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
class BrowserFallbackDecision:
    enabled: bool
    reason: str = ""


class ResearchSourceQualityReader(Protocol):
    def assess_hosts(self, *, domain: str, hosts: tuple[str, ...]) -> tuple[Any, ...]:
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


class WebResearchOrchestrator:
    def __init__(
        self,
        *,
        webtool_dispatcher: Any,
        evidence_pipeline: WebEvidencePipeline | None = None,
        source_quality_reader: ResearchSourceQualityReader | None = None,
    ) -> None:
        self._webtool_dispatcher = webtool_dispatcher
        self._evidence_pipeline = evidence_pipeline
        self._source_quality_reader = source_quality_reader

    def execute(self, request: WebResearchOrchestratorRequest) -> WebResearchOrchestratorResult:
        if self._webtool_dispatcher is None:
            return WebResearchOrchestratorResult()

        decision_auto = decide_auto_research(request.normalized_text)
        is_followup_research = False
        if (
            not decision_auto.enabled
            and request.is_triggered_path
            and is_web_research_followup_feedback(request.normalized_text)
        ):
            followup_query = build_web_research_followup_query(
                feedback_text=request.normalized_text,
                context_text=request.reply_context_text,
            )
            if followup_query:
                decision_auto = type("_AutoFollowup", (), {
                    "enabled": True,
                    "capability": "websearch",
                    "reason": "user_feedback_followup",
                    "query": followup_query,
                    "url": "",
                })()
                is_followup_research = True

        if not decision_auto.enabled:
            return WebResearchOrchestratorResult()

        domain_evidence = self._evaluate_domain_evidence(request)
        if domain_evidence is not None:
            if domain_evidence.confirmed:
                return WebResearchOrchestratorResult(auto_note=format_domain_evidence_note(domain_evidence))
            if domain_evidence.domain in {"weather", "crypto", "stock", "sports"}:
                return WebResearchOrchestratorResult(
                    user_response=format_domain_fail_closed_response(
                        domain=domain_evidence.domain,
                        locale=request.locale,
                        warnings=domain_evidence.warnings,
                    )
                )

        tool_result = self._run_primary_search(request, decision_auto=decision_auto, is_followup_research=is_followup_research)
        retry_attempted = False
        if (
            decision_auto.capability == "websearch"
            and not (tool_result.allowed and (tool_result.text or "").strip())
            and (tool_result.reason or "") == "empty_result"
        ):
            retry_query = build_empty_result_retry_query(request.normalized_text)
            if retry_query and retry_query != (decision_auto.query or "").strip():
                retry_attempted = True
                tool_result = self._run_retry_search(
                    request,
                    decision_auto=decision_auto,
                    retry_query=retry_query,
                    is_followup_research=is_followup_research,
                )

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
                    )
                )
            return WebResearchOrchestratorResult(
                auto_note=_format_auto_research_no_result_note(
                    capability=decision_auto.capability,
                    reason=tool_result.reason,
                )
            )

        plan = build_research_plan(
            request_text=request.normalized_text,
            capability=decision_auto.capability,
            reason=decision_auto.reason,
            source_hosts=tuple(tool_result.hosts or ()),
            source_urls=tuple(tool_result.sources or ()),
            source_quality_reader=self._source_quality_reader,
        )
        if plan.should_followup_search:
            tool_result = self._run_planned_followup_search(
                request,
                current_result=tool_result,
                plan=plan,
                is_followup_research=is_followup_research,
            )

        chain_extracts: list[tuple[str, str, str]] = []
        chain_urls: tuple[str, ...] = ()
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

        if chain_extracts:
            news_corroboration_response = _format_news_corroboration_response(
                request_text=request.normalized_text,
                extracts=tuple(chain_extracts),
                locale=request.locale,
            )
            if news_corroboration_response:
                return WebResearchOrchestratorResult(user_response=news_corroboration_response)
            source_quality = _assess_chain_source_quality(
                domain=classify_evidence_domain(request.normalized_text),
                extracts=tuple(chain_extracts),
                reader=self._source_quality_reader,
            )
            news_gate_response = _format_news_insufficient_sources_response(
                request_text=request.normalized_text,
                extract_hosts=tuple(host for _, host, _ in chain_extracts),
                locale=request.locale,
                source_quality=source_quality,
            )
            if news_gate_response:
                return WebResearchOrchestratorResult(user_response=news_gate_response)
            auto_note = _format_auto_research_chained_success_note(
                capability=decision_auto.capability,
                search_text=tool_result.text,
                search_hosts=tuple(tool_result.hosts or ()),
                extracts=tuple(chain_extracts),
                followup=is_followup_research,
            )
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
            auto_note = _format_auto_research_chain_no_confirmation_note(
                capability=decision_auto.capability,
                search_text=tool_result.text,
                search_hosts=tuple(tool_result.hosts or ()),
                followup=is_followup_research,
            )
        else:
            domain_unconfirmed = _format_domain_chain_unconfirmed_response(
                request_text=request.normalized_text,
                locale=request.locale,
                reason="snippet_only_result",
            )
            if domain_unconfirmed:
                return WebResearchOrchestratorResult(user_response=domain_unconfirmed)
            user_response = _format_weather_unconfirmed_response(
                request_text=request.normalized_text,
                search_text=tool_result.text,
                search_hosts=tuple(tool_result.hosts or ()),
                locale=request.locale,
            )
            if user_response:
                return WebResearchOrchestratorResult(user_response=user_response)
            auto_note = _format_auto_research_success_note(
                capability=decision_auto.capability,
                text=tool_result.text,
                hosts=tuple(tool_result.hosts or ()),
            )
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
        )
        return self._webtool_dispatcher.execute(tool_request)

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
    has_freshness = bool(_AUTO_RESEARCH_CHAIN_FRESHNESS_RE.search(raw))
    if not has_freshness:
        return False
    has_strong_freshness = bool(_AUTO_RESEARCH_CHAIN_STRONG_FRESHNESS_RE.search(raw))
    if _AUTO_RESEARCH_CHAIN_TIMELESS_EDU_RE.search(raw) and not has_strong_freshness:
        return False
    return True


def build_research_plan(
    *,
    request_text: str,
    capability: str,
    reason: str | None,
    source_hosts: tuple[str, ...] = (),
    source_urls: tuple[str, ...] = (),
    source_quality_reader: ResearchSourceQualityReader | None = None,
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
    cleaned = text or ""
    for pattern, replacement in _AUTO_RESEARCH_TECHNICAL_RESPONSE_REPLACEMENTS:
        cleaned = pattern.sub(replacement, cleaned)
    return cleaned


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


def _format_auto_research_chained_success_note(
    *, capability: str, search_text: str, search_hosts: tuple[str, ...], extracts: tuple[tuple[str, str, str], ...], followup: bool = False
) -> str:
    compact_search = compact_webtool_result_text(search_text, max_chars=_AUTO_RESEARCH_SEARCH_SUMMARY_CAP)
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
        f"{context_line}A live {capability}/web tool result and checked source text are available in this turn. Treat this fresh web context as primary evidence for current facts.\n"
        "Do NOT claim or imply that the bot has no web tools, no live data capability, or cannot search the web.\n"
        "Use the supplied web result text and checked source evidence; mention source hosts when relevant. Summarize compactly and do not reproduce raw page/tool output. Do NOT override it with stale memory/priors.\n"
        "User-facing wording: describe only the available web evidence and source certainty; do not expose retrieval pipeline details or diagnostic labels.\n"
        "Strict anti-hallucination: do NOT invent exact prices, rates, dates, levels, or news not supported by the supplied evidence. If exact values are absent, say the live evidence does not confirm the exact value.\n"
        f"Operation: {capability}\n"
        f"Web result text: {compact_search}\n"
        f"Search source hosts: {host_text}\n"
        f"Checked source evidence:\n{evidence}"
    )


def _format_auto_research_chain_no_confirmation_note(*, capability: str, search_text: str, search_hosts: tuple[str, ...], followup: bool = False) -> str:
    compact_search = compact_webtool_result_text(search_text, max_chars=_AUTO_RESEARCH_SEARCH_SUMMARY_CAP)
    host_text = ", ".join(search_hosts[:5])
    heading = "FOLLOW-UP AUTO-RESEARCH STATUS" if followup else "AUTO-RESEARCH STATUS"
    context_line = (
        "This was a bounded follow-up because user feedback requested more/different sources after a prior web/AI answer. "
        if followup else ""
    )
    return (
        f"{heading} — WEB SEARCH SUCCEEDED, SOURCE CHECK INCONCLUSIVE:\n"
        f"{context_line}A live {capability} succeeded in this turn, but checking the linked source pages produced no additional usable confirmation.\n"
        "Be transparent: use the web result text only as limited live context, and if evidence remains insufficient, say clearly that the available web results could not fully confirm the requested information.\n"
        "Summarize compactly and do not reproduce raw page/tool output.\n"
        "Do NOT say or imply that the bot has no web tools, no live data capability, or cannot search the web.\n"
        "User-facing wording: describe only the available web evidence and source certainty; do not expose retrieval pipeline details or diagnostic labels. For German answers, use natural wording like 'laut verfügbaren Web-Suchergebnissen' or 'eine zusätzliche Quellenbestätigung war diesmal nicht möglich'.\n"
        "Strict anti-hallucination: do NOT invent exact prices, rates, dates, levels, or news without reliable live confirmation from this turn.\n"
        f"Operation: {capability}\n"
        f"Web result text: {compact_search}\n"
        f"Search source hosts: {host_text}"
    )


def _format_auto_research_success_note(*, capability: str, text: str, hosts: tuple[str, ...]) -> str:
    compact_text = compact_webtool_result_text(text, max_chars=_AUTO_RESEARCH_SEARCH_SUMMARY_CAP)
    host_text = ", ".join(hosts[:5])
    return (
        "AUTO-RESEARCH (LIVE WEB) — STRICT INSTRUCTION:\n"
        f"A live {capability}/web tool result is available in this turn. Treat this fresh web context as primary evidence for current facts.\n"
        "Do NOT claim or imply that the bot has no web tools, no live data capability, or cannot search the web.\n"
        "Use the supplied web result text as primary evidence, cite or mention the source hosts when relevant, summarize compactly, do NOT reproduce raw tool output, and do NOT override it with stale memory/priors.\n"
        "User-facing wording: describe only the available web evidence and source certainty; do not expose retrieval pipeline details or diagnostic labels.\n"
        "Strict anti-hallucination: do NOT invent dates, prices, levels, or news not supported by the supplied live summary. "
        "If exact values are not in the supplied summary, state that the available live sources do not confirm that exact value; do not say no webtools.\n"
        "If sources conflict, say so transparently.\n"
        f"Operation: {capability}\n"
        f"Web result text: {compact_text}\n"
        f"Source hosts: {host_text}"
    )


def _format_auto_research_no_result_note(*, capability: str, reason: str | None) -> str:
    normalized_reason = (reason or "").strip() or "no_usable_result"
    reason_text = _AUTO_RESEARCH_NO_RESULT_TEXT.get(normalized_reason, "the attempt returned no usable live result")
    return (
        "AUTO-RESEARCH STATUS — WEB ATTEMPTED, NO USABLE RESULT:\n"
        f"A live {capability} attempt was made in this turn, but {reason_text}. "
        f"Reason code: {normalized_reason}.\n"
        "Be transparent and precise: say the web search was attempted but this specific attempt produced no usable result, timed out, was limited, or the provider was unavailable. "
        "Do NOT say or imply that the bot has no web tools, no live data capability, or cannot search the web in general. "
        "If useful in German, say: 'Die Websuche wurde versucht, lieferte aber diesmal keine verwertbaren Treffer/keine Bestätigung.'\n"
        "Strict anti-hallucination: do NOT invent current facts, dates, prices, levels, or news without reliable live confirmation from this turn. "
        "Memory and model priors are not acceptable substitutes for live evidence when current external data was required."
    )


def _format_auto_research_retry_no_result_note(*, capability: str, reason: str | None) -> str:
    normalized_reason = (reason or "").strip() or "no_usable_result"
    reason_text = _AUTO_RESEARCH_NO_RESULT_TEXT.get(normalized_reason, "the retry returned no usable live result")
    return (
        "AUTO-RESEARCH STATUS — WEB ATTEMPTED, RETRY ALSO NO USABLE RESULT:\n"
        f"A live {capability} attempt was made in this turn, then exactly one simplified retry from the current user message was also attempted, but {reason_text}. "
        f"Final reason code: {normalized_reason}.\n"
        "Be transparent and precise: say live websearch was attempted but did not return usable results after retry, so no current value/fact could be confirmed from live sources in this turn. "
        "Do NOT say or imply that the bot has no web tools, no live data capability, or cannot search the web in general. "
        "If useful in German, say: 'Die Live-Websuche wurde versucht, lieferte aber auch nach einem vereinfachten Retry keine verwertbaren Treffer; ein aktueller Wert konnte nicht bestätigt werden.'\n"
        "Strict anti-hallucination: do NOT reuse old/stale prices, rates, dates, levels, news, or prior-answer context as an estimate. Do NOT provide an estimated current value when live confirmation is unavailable. "
        "Memory and model priors are not acceptable substitutes for live evidence when current external data was required."
    )
