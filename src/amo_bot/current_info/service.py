from __future__ import annotations

import logging
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, replace
from urllib.parse import urlparse

from amo_bot.core.source_hosts import normalize_source_host
from amo_bot.current_info.candidates import (
    SOURCE_TYPE_DOCS,
    SOURCE_TYPE_MARKET_DATA,
    SOURCE_TYPE_NEWS,
    SOURCE_TYPE_OFFICIAL,
    classify_source_type,
    normalize_dedupe_and_rank_search_results,
)
from amo_bot.current_info.evidence import assemble_evidence_package
from amo_bot.evidence_intents import is_finance_listing_query
from amo_bot.current_info.models import (
    CurrentInfoAnswer,
    CurrentInfoRequest,
    EvidenceChunk,
    FetchedDocument,
    QueryPlan,
    ResearchPlan,
    ResearchPlanStep,
    SearchBundle,
    SearchProviderMetric,
    SearchProviderResponse,
    SearchResult,
    TaskSpec,
)
from amo_bot.current_info.observability import (
    CurrentInfoBudgetExceeded,
    CurrentInfoRunBudget,
    CurrentInfoSafetyConfig,
    log_current_info_event,
    safe_error_message,
)
from amo_bot.current_info.ports import (
    CurrentInfoFetchProvider,
    CurrentInfoQueryPlanner,
    CurrentInfoRetrievalProvider,
    CurrentInfoSearchProvider,
    CurrentInfoTaskPlanner,
)
from amo_bot.current_info.research import CurrentInfoResearchProvider
from amo_bot.current_info.search import SearchProviderError


logger = logging.getLogger(__name__)

_URL_RE = re.compile(r"https?://[^\s<>()\[\]{}\"']+", re.IGNORECASE)

@dataclass(frozen=True, slots=True)
class DefaultCurrentInfoTaskPlanner:
    def plan_task(self, request: CurrentInfoRequest) -> TaskSpec:
        return TaskSpec(
            task_type="current_info",
            query=request.query.strip(),
            locale=(request.locale or "en").strip().lower() or "en",
            domain=request.domain_hint.strip().lower(),
            constraints={
                "max_results": request.max_results,
                "max_documents": request.max_documents,
            },
        )


@dataclass(frozen=True, slots=True)
class DefaultCurrentInfoQueryPlanner:
    def plan_queries(self, *, request: CurrentInfoRequest, task: TaskSpec) -> QueryPlan:
        query = task.query or request.query.strip()
        direct_urls = _extract_direct_urls(query)
        add_verification = bool(direct_urls) or _needs_official_source_variant(query, task=task)
        queries = _general_query_variants(
            query,
            add_verification=add_verification,
        )
        if query and _is_finance_listing_query(request=request, task=task):
            followup = _finance_listing_followup_query(query)
            if followup and followup.casefold() != query.casefold():
                queries = tuple(dict.fromkeys((query, followup)))
        steps: list[ResearchPlanStep] = [
            ResearchPlanStep(
                operation="direct_url_fetch",
                reason="user_provided_url",
                url=url,
                source_role="direct_user_url",
            )
            for url in direct_urls
        ]
        steps.extend(
            ResearchPlanStep(
                operation="search",
                reason="original_query" if index == 0 else "official_source_verification",
                query=variant,
                source_role="corroborating_source" if index == 0 else "official_source_candidate",
            )
            for index, variant in enumerate(queries)
        )
        strategy = "direct_url_first" if direct_urls else "search_first"
        return QueryPlan(
            task=task,
            queries=queries,
            max_results=request.max_results,
            strategy=strategy,
            research_plan=ResearchPlan(
                strategy=strategy,
                steps=tuple(steps),
                direct_urls=direct_urls,
                query_variants=queries,
            ),
        )


@dataclass(frozen=True, slots=True)
class SnippetRetrievalProvider:
    def retrieve(
        self,
        *,
        request: CurrentInfoRequest,
        documents: tuple[FetchedDocument, ...],
        search_results: tuple[SearchResult, ...],
    ) -> tuple[EvidenceChunk, ...]:
        chunks: list[EvidenceChunk] = []
        for document in documents:
            text = " ".join(document.text.split())
            if text:
                chunks.append(
                    EvidenceChunk(
                        text=text[:1200],
                        source_url=document.url,
                        source_title=document.title,
                        relevance=1.0,
                    )
                )
        if chunks:
            return tuple(chunks)

        for result in search_results:
            snippet = " ".join(result.snippet.split())
            if snippet:
                chunks.append(
                    EvidenceChunk(
                        text=snippet[:500],
                        source_url=result.url,
                        source_title=result.title,
                        relevance=0.5,
                    )
                )
        return tuple(chunks)


class CurrentInfoService:
    """Provider-neutral service boundary for current-information answers."""

    def __init__(
        self,
        *,
        search_provider: CurrentInfoSearchProvider | None = None,
        fetch_provider: CurrentInfoFetchProvider | None = None,
        retrieval_provider: CurrentInfoRetrievalProvider | None = None,
        task_planner: CurrentInfoTaskPlanner | None = None,
        query_planner: CurrentInfoQueryPlanner | None = None,
        research_provider: CurrentInfoResearchProvider | None = None,
        source_preference_repository: object | None = None,
        safety_config: CurrentInfoSafetyConfig | None = None,
    ) -> None:
        self._search_provider = search_provider
        self._fetch_provider = fetch_provider
        self._retrieval_provider = retrieval_provider or SnippetRetrievalProvider()
        self._task_planner = task_planner or DefaultCurrentInfoTaskPlanner()
        self._query_planner = query_planner or DefaultCurrentInfoQueryPlanner()
        self._research_provider = research_provider
        self._source_preference_repository = source_preference_repository
        self._safety_config = safety_config or CurrentInfoSafetyConfig()

    def answer(self, request: CurrentInfoRequest) -> CurrentInfoAnswer:
        started = time.perf_counter()
        budget = CurrentInfoRunBudget(self._safety_config)
        task = self._task_planner.plan_task(request)
        query_plan = self._query_planner.plan_queries(request=request, task=task)
        log_current_info_event(
            logger,
            event="current_info.QueryRun",
            stage="query_plan",
            query=task.query,
            chat_id=request.chat_id,
            user_id=request.user_id,
            topic_id=request.topic_id,
            outcome="planned",
            extra={"query_count": len(query_plan.queries), "max_results": query_plan.max_results},
        )
        if not task.query or not query_plan.queries:
            self._log_synthesis(request=request, task=task, started=started, status="invalid_request", budget=budget)
            return CurrentInfoAnswer(
                status="invalid_request",
                request=request,
                task=task,
                query_plan=query_plan,
                warnings=("empty_query",),
                metadata=self._debug_metadata(budget),
            )

        if _requires_gpt_researcher(request):
            if self._research_provider is None:
                self._log_synthesis(
                    request=request,
                    task=task,
                    started=started,
                    status="provider_unavailable",
                    budget=budget,
                    reason_code="gpt_researcher_not_configured",
                )
                return CurrentInfoAnswer(
                    status="provider_unavailable",
                    request=request,
                    task=task,
                    query_plan=query_plan,
                    warnings=("gpt_researcher_not_configured",),
                    metadata={**self._debug_metadata(budget), "provider_mode": "gpt_researcher"},
                )

            research_answer = self._research_provider.answer(request=request, task=task, query_plan=query_plan)
            self._log_synthesis(
                request=request,
                task=task,
                started=started,
                status=research_answer.status,
                budget=budget,
                reason_code="gpt_researcher",
            )
            return research_answer

        if _is_webresearch_request(request) and self._research_provider is not None:
            research_answer = self._research_provider.answer(request=request, task=task, query_plan=query_plan)
            if research_answer.answered or research_answer.status in {"empty_evidence", "unverified_evidence"}:
                self._log_synthesis(
                    request=request,
                    task=task,
                    started=started,
                    status=research_answer.status,
                    budget=budget,
                    reason_code="gpt_researcher",
                )
                return research_answer
            log_current_info_event(
                logger,
                event="current_info.ResearchFallback",
                stage="research",
                query=task.query,
                chat_id=request.chat_id,
                user_id=request.user_id,
                topic_id=request.topic_id,
                outcome="fallback",
                reason_code=",".join(research_answer.warnings) if research_answer.warnings else research_answer.status,
                extra={"provider_mode": "gpt_researcher"},
            )

        direct_url_results = _direct_url_search_results(task.query)
        if self._search_provider is None and not direct_url_results:
            self._log_synthesis(
                request=request,
                task=task,
                started=started,
                status="provider_unavailable",
                budget=budget,
                reason_code="search_provider_not_configured",
            )
            return CurrentInfoAnswer(
                status="provider_unavailable",
                request=request,
                task=task,
                query_plan=query_plan,
                warnings=("search_provider_not_configured",),
                metadata=self._debug_metadata(budget),
            )

        try:
            if self._search_provider is None:
                annotated_direct_results = _annotate_authoritative_search_results(
                    direct_url_results,
                    request=request,
                    task=query_plan.task,
                )
                search_response = SearchProviderResponse(
                    results=_label_search_results(
                        normalize_dedupe_and_rank_search_results(
                            annotated_direct_results,
                            max_results=query_plan.max_results,
                            source_preferences=self._source_preferences_for_results(
                                annotated_direct_results,
                                request=request,
                                domain=task.domain,
                            ),
                        )
                    )
                )
            else:
                search_response = self._run_search_plan(
                    query_plan,
                    locale=task.locale,
                    budget=budget,
                    request=request,
                    direct_url_results=direct_url_results,
                )
        except CurrentInfoBudgetExceeded as exc:
            self._log_synthesis(
                request=request,
                task=task,
                started=started,
                status="budget_exceeded",
                budget=budget,
                reason_code=exc.reason_code,
            )
            return CurrentInfoAnswer(
                status="provider_unavailable",
                request=request,
                task=task,
                query_plan=query_plan,
                warnings=(exc.reason_code,),
                metadata=self._debug_metadata(budget),
            )
        except SearchProviderError as exc:
            reason_code = getattr(exc, "error_class", None) or "search_provider_error"
            self._log_synthesis(
                request=request,
                task=task,
                started=started,
                status="provider_unavailable",
                budget=budget,
                reason_code=reason_code,
            )
            return CurrentInfoAnswer(
                status="provider_unavailable",
                request=request,
                task=task,
                query_plan=query_plan,
                warnings=(reason_code,),
                metadata=self._debug_metadata(budget),
            )
        search_results = search_response.results
        search_bundle = SearchBundle(query_plan=query_plan, results=search_results, metrics=search_response.metrics)
        if not search_results:
            self._log_synthesis(
                request=request,
                task=task,
                started=started,
                status="empty_result",
                budget=budget,
                reason_code="empty_search_result",
            )
            return CurrentInfoAnswer(
                status="empty_result",
                request=request,
                task=task,
                query_plan=query_plan,
                search_bundle=search_bundle,
                warnings=("empty_search_result",),
                metadata=self._debug_metadata(budget),
            )

        documents = self._fetch_documents(search_results, request=request, budget=budget)
        chunks = self._retrieval_provider.retrieve(
            request=request,
            documents=documents,
            search_results=search_results,
        )
        chunks = _prepare_evidence_chunks_for_request(
            chunks,
            documents=documents,
            request=request,
            task=task,
        )
        evidence = assemble_evidence_package(
            request=request,
            task=task,
            chunks=chunks,
            documents=documents,
            search_results=search_results,
        )
        log_current_info_event(
            logger,
            event="current_info.EvidenceDecision",
            stage="evidence",
            query=task.query,
            chat_id=request.chat_id,
            user_id=request.user_id,
            topic_id=request.topic_id,
            outcome="assembled",
            reason_code=",".join(evidence.warnings) if evidence.warnings else None,
            extra={
                "chunk_count": len(chunks),
                "document_count": len(documents),
                "source_count": len(evidence.sources),
                "confidence": evidence.confidence,
                "freshness": evidence.freshness,
            },
        )
        if not chunks:
            self._log_synthesis(
                request=request,
                task=task,
                started=started,
                status="empty_evidence",
                budget=budget,
                reason_code="empty_evidence",
            )
            return CurrentInfoAnswer(
                status="empty_evidence",
                request=request,
                task=task,
                query_plan=query_plan,
                search_bundle=search_bundle,
                evidence=evidence,
                sources=tuple(result.url for result in search_results if result.url),
                warnings=evidence.warnings or ("empty_evidence",),
                confidence=evidence.confidence,
                metadata=self._debug_metadata(budget),
            )
        if "snippet_only_evidence" in evidence.warnings:
            self._log_synthesis(
                request=request,
                task=task,
                started=started,
                status="unverified_evidence",
                budget=budget,
                reason_code="snippet_only_evidence",
            )
            return CurrentInfoAnswer(
                status="unverified_evidence",
                request=request,
                task=task,
                query_plan=query_plan,
                search_bundle=search_bundle,
                evidence=evidence,
                sources=tuple(dict.fromkeys(chunk.source_url for chunk in chunks if chunk.source_url)),
                warnings=evidence.warnings,
                confidence=evidence.confidence,
                metadata=self._debug_metadata(budget, reason="current_facts_need_fetched_sources"),
            )
        if _needs_stronger_evidence(request=request, task=task, warnings=evidence.warnings):
            reason_code = _stronger_evidence_reason(evidence.warnings)
            self._log_synthesis(
                request=request,
                task=task,
                started=started,
                status="unverified_evidence",
                budget=budget,
                reason_code=reason_code,
            )
            return CurrentInfoAnswer(
                status="unverified_evidence",
                request=request,
                task=task,
                query_plan=query_plan,
                search_bundle=search_bundle,
                evidence=evidence,
                sources=tuple(dict.fromkeys(chunk.source_url for chunk in chunks if chunk.source_url)),
                warnings=evidence.warnings,
                confidence=evidence.confidence,
                metadata=self._debug_metadata(budget, reason=reason_code),
            )

        self._log_synthesis(request=request, task=task, started=started, status="answered", budget=budget)
        return CurrentInfoAnswer(
            status="answered",
            answer_text=_format_answer_text(chunks, request=request, task=task),
            confidence=evidence.confidence,
            request=request,
            task=task,
            query_plan=query_plan,
            search_bundle=search_bundle,
            evidence=evidence,
            sources=tuple(dict.fromkeys(chunk.source_url for chunk in chunks if chunk.source_url)),
            warnings=evidence.warnings,
            metadata=self._debug_metadata(budget),
        )

    def _run_search_plan(
        self,
        query_plan: QueryPlan,
        *,
        locale: str,
        budget: CurrentInfoRunBudget,
        request: CurrentInfoRequest,
        direct_url_results: tuple[SearchResult, ...] = (),
    ) -> SearchProviderResponse:
        assert self._search_provider is not None
        collected: list[SearchResult] = list(_annotate_authoritative_search_results(
            direct_url_results,
            request=request,
            task=query_plan.task,
        ))
        metrics: list[SearchProviderMetric] = []
        seen_urls: set[str] = {item.url for item in direct_url_results if item.url}
        for query in query_plan.queries:
            budget.consume_search_provider_run()
            provider_started = time.perf_counter()
            provider_response = self._search_provider.search(query=query, locale=locale, max_results=query_plan.max_results)
            if isinstance(provider_response, SearchProviderResponse):
                results = provider_response.results
                metrics.extend(provider_response.metrics)
            else:
                results = tuple(provider_response)
            log_current_info_event(
                logger,
                event="current_info.ProviderRun",
                stage="search",
                query=query,
                chat_id=request.chat_id,
                user_id=request.user_id,
                topic_id=request.topic_id,
                duration_ms=int((time.perf_counter() - provider_started) * 1000),
                outcome="ok",
                extra={"hit_count": len(results), "provider_kind": "search"},
            )
            for item in results:
                if item.url in seen_urls:
                    continue
                seen_urls.add(item.url)
                collected.append(item)
        annotated = _annotate_authoritative_search_results(
            tuple(collected),
            request=request,
            task=query_plan.task,
        )
        return SearchProviderResponse(
            results=_label_search_results(
                normalize_dedupe_and_rank_search_results(
                    annotated,
                    max_results=query_plan.max_results,
                    source_preferences=self._source_preferences_for_results(
                        annotated,
                        request=request,
                        domain=query_plan.task.domain,
                    ),
                )
            ),
            metrics=tuple(metrics),
        )

    def _source_preferences_for_results(
        self,
        results: tuple[SearchResult, ...],
        *,
        request: CurrentInfoRequest,
        domain: str,
    ) -> dict[str, Mapping[str, object]]:
        hosts = tuple(dict.fromkeys(_host_from_search_result(result) for result in results if _host_from_search_result(result)))
        if not hosts:
            return {}
        mapped: dict[str, Mapping[str, object]] = _builtin_source_preferences_for_results(
            results,
            request=request,
            domain=domain,
        )
        if self._source_preference_repository is None:
            return mapped
        list_for_hosts = getattr(self._source_preference_repository, "list_for_hosts", None)
        if list_for_hosts is None:
            return mapped
        try:
            preferences = list_for_hosts(
                source_hosts=hosts,
                domain=domain or request.domain_hint or "generic",
                chat_id=request.chat_id,
                topic_id=request.topic_id,
                user_id=request.user_id,
            )
        except Exception as exc:
            log_current_info_event(
                logger,
                event="current_info.SourcePreference",
                stage="search",
                query="",
                chat_id=request.chat_id,
                user_id=request.user_id,
                topic_id=request.topic_id,
                outcome="error",
                reason_code="source_preference_lookup_failed",
                extra={"error_class": exc.__class__.__name__},
            )
            return mapped
        if not isinstance(preferences, Mapping):
            return mapped
        for host, preference in preferences.items():
            host_key = normalize_source_host(str(host))
            if not host_key:
                continue
            metadata = getattr(preference, "metadata", None)
            if isinstance(metadata, Mapping):
                mapped[host_key] = metadata
            elif isinstance(preference, Mapping):
                mapped[host_key] = preference
        return mapped

    def _fetch_documents(
        self,
        search_results: tuple[SearchResult, ...],
        *,
        request: CurrentInfoRequest,
        budget: CurrentInfoRunBudget,
    ) -> tuple[FetchedDocument, ...]:
        if self._fetch_provider is None:
            return ()

        documents: list[FetchedDocument] = []
        max_documents = max(request.max_documents, 0)
        if max_documents == 0:
            return ()

        for result in _fetch_priority_results(search_results):
            if not result.url:
                continue
            try:
                budget.consume_fetch_run()
            except CurrentInfoBudgetExceeded as exc:
                budget.warnings.append(exc.reason_code)
                break
            fetch_started = time.perf_counter()
            document = None
            fetch_error: Exception | None = None
            for candidate_url in _fetch_url_candidates(result):
                try:
                    document = self._fetch_provider.fetch(url=candidate_url, locale=request.locale)
                except Exception as exc:
                    fetch_error = exc
                    log_current_info_event(
                        logger,
                        event="current_info.FetchRun",
                        stage="fetch",
                        query=request.query,
                        chat_id=request.chat_id,
                        user_id=request.user_id,
                        topic_id=request.topic_id,
                        duration_ms=int((time.perf_counter() - fetch_started) * 1000),
                        outcome="error",
                        reason_code="fetch_provider_error",
                        extra={
                            "host": result.host,
                            "provider_kind": "fetch",
                            "error_class": exc.__class__.__name__,
                            "error_message": safe_error_message(exc, max_chars=200),
                        },
                    )
                    continue
                if document is not None:
                    break
            log_current_info_event(
                logger,
                event="current_info.FetchRun",
                stage="fetch",
                query=request.query,
                chat_id=request.chat_id,
                user_id=request.user_id,
                topic_id=request.topic_id,
                duration_ms=int((time.perf_counter() - fetch_started) * 1000),
                outcome="hit" if document is not None else "error" if fetch_error is not None else "miss",
                reason_code="fetch_provider_error" if document is None and fetch_error is not None else None,
                extra={
                    "host": result.host,
                    "provider_kind": "fetch",
                    **(
                        {
                            "error_class": fetch_error.__class__.__name__,
                            "error_message": safe_error_message(fetch_error, max_chars=200),
                        }
                        if document is None and fetch_error is not None
                        else {}
                    ),
                },
            )
            if document is None or not document.text.strip():
                continue
            documents.append(document)
            if len(documents) >= max_documents:
                break
        return tuple(documents)

    def _debug_metadata(self, budget: CurrentInfoRunBudget, **extra: object) -> JsonDict:
        metadata: JsonDict = dict(extra)
        if self._safety_config.debug_enabled:
            metadata["debug"] = {"budgets": budget.to_debug_dict()}
        return metadata

    def _log_synthesis(
        self,
        *,
        request: CurrentInfoRequest,
        task: TaskSpec,
        started: float,
        status: str,
        budget: CurrentInfoRunBudget,
        reason_code: str | None = None,
    ) -> None:
        log_current_info_event(
            logger,
            event="current_info.AnswerSynthesis",
            stage="synthesis",
            query=task.query,
            chat_id=request.chat_id,
            user_id=request.user_id,
            topic_id=request.topic_id,
            duration_ms=int((time.perf_counter() - started) * 1000),
            outcome=status,
            reason_code=reason_code,
            extra=budget.to_debug_dict(),
        )


def _format_answer_text(
    chunks: tuple[EvidenceChunk, ...],
    *,
    request: CurrentInfoRequest | None = None,
    task: TaskSpec | None = None,
) -> str:
    listing_answer = _format_finance_listing_answer_text(chunks, request=request, task=task)
    if listing_answer:
        return listing_answer
    lines: list[str] = []
    for chunk in chunks[:3]:
        text = " ".join(chunk.text.split())
        if text:
            lines.append(text)
    return "\n\n".join(lines)


def _format_finance_listing_answer_text(
    chunks: tuple[EvidenceChunk, ...],
    *,
    request: CurrentInfoRequest | None,
    task: TaskSpec | None,
) -> str:
    if request is None or task is None or not _is_finance_listing_query(request=request, task=task):
        return ""
    indicators = _finance_listing_indicators(chunks)
    if not indicators:
        return ""
    target = _finance_listing_answer_subject(request.query) or _finance_listing_answer_subject(task.query)
    values = {
        str(chunk.metadata.get("claim_value") or chunk.metadata.get("fact_value") or "").casefold()
        for chunk in chunks
    }
    not_listed = any(value in {"not_listed", "not_publicly_listed", "private", "privately_held"} for value in values)
    if not_listed:
        return ""
    if (request.locale or task.locale or "en").lower().startswith("en"):
        subject = target or "the company"
        return f"Yes, checked sources indicate {subject} is publicly listed: {', '.join(indicators)}."
    subject = target or "das Unternehmen"
    return f"Ja, die geprüften Quellen zeigen {subject} als börsennotiert: {', '.join(indicators)}."


def _finance_listing_answer_subject(text: str) -> str:
    compact = re.sub(r"https?://\S+", " ", text or "")
    compact = re.sub(r"\s+", " ", compact).strip()
    match = re.search(
        r"\b(?:ist|is|kann\s+man|can\s+i|can\s+you|are)\s+([A-ZÄÖÜ][\wÄÖÜäöüß&.-]*(?:\s+[A-ZÄÖÜ][\wÄÖÜäöüß&.-]*){0,3})",
        compact,
        re.IGNORECASE,
    )
    if not match:
        return ""
    subject = match.group(1).strip(" ?.,;:")
    subject = re.sub(
        r"\s+(?:an|börsennotiert|boersennotiert|listed|publicly|stock|aktien?|shares?|kaufen|buy).*$",
        "",
        subject,
        flags=re.IGNORECASE,
    ).strip(" ?.,;:")
    return subject


_FINANCE_LISTING_INDICATOR_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bAktie\b", re.IGNORECASE), "Aktie"),
    (re.compile(r"\bWKN[:\s]+([A-Z0-9]{3,12})\b", re.IGNORECASE), "WKN {value}"),
    (re.compile(r"\bISIN[:\s]+([A-Z]{2}[A-Z0-9]{9}[0-9])\b", re.IGNORECASE), "ISIN {value}"),
    (re.compile(r"\b(?:ticker|symbol)[:\s]+([A-Z0-9.:-]{1,12})\b", re.IGNORECASE), "Ticker {value}"),
    (
        re.compile(r"\b(?:börsennotiert|boersennotiert|publicly\s+listed|publicly\s+traded|listed)\b", re.IGNORECASE),
        "Listing-Hinweis",
    ),
    (
        re.compile(r"\b(?:investor relations|stocks?/bonds?/rating|shareholder|aktieninformationen)\b", re.IGNORECASE),
        "Investor-Relations-Aktienbereich",
    ),
)


def _finance_listing_indicators(chunks: tuple[EvidenceChunk, ...]) -> list[str]:
    indicators: list[str] = []
    for chunk in chunks:
        text = " ".join(" ".join((chunk.source_title or "", chunk.text)).split())
        for pattern, label in _FINANCE_LISTING_INDICATOR_PATTERNS:
            match = pattern.search(text)
            if not match:
                continue
            value = match.group(1).upper() if match.groups() else ""
            indicator = label.format(value=value) if value else label
            if indicator not in indicators:
                indicators.append(indicator)
        if len(indicators) >= 4:
            break
    return indicators[:4]


def _direct_url_search_results(query: str) -> tuple[SearchResult, ...]:
    results: list[SearchResult] = []
    for url in _extract_direct_urls(query):
        parsed = urlparse(url)
        host = normalize_source_host(parsed.hostname)
        results.append(
            SearchResult(
                title=host or url,
                url=url,
                snippet="User-provided source URL.",
                provider="direct_user_url",
                rank=0,
                host=host,
                metadata={
                    "source_type": "user_provided",
                    "source_role": "direct_user_url",
                    "quality_label": "direct_user_url",
                },
            )
        )
    return tuple(results)


def _host_from_search_result(result: SearchResult) -> str:
    return normalize_source_host(result.host or urlparse(result.url).hostname)


def _extract_direct_urls(query: str) -> tuple[str, ...]:
    urls: list[str] = []
    seen: set[str] = set()
    for match in _URL_RE.finditer(query or ""):
        url = match.group(0).rstrip(".,;:!?)]}'\"")
        if not url or url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return tuple(urls)


def _is_webresearch_request(request: CurrentInfoRequest) -> bool:
    metadata = dict(request.metadata or {})
    return str(
        metadata.get("capability")
        or metadata.get("auto_research_capability")
        or metadata.get("research_capability")
        or ""
    ).casefold() == "webresearch"


def _requires_gpt_researcher(request: CurrentInfoRequest) -> bool:
    metadata = dict(request.metadata or {})
    return bool(metadata.get("require_gpt_researcher"))


def _general_query_variants(query: str, *, add_verification: bool = False) -> tuple[str, ...]:
    compact = " ".join((query or "").split()).strip()
    if not compact:
        return ()
    without_urls = _URL_RE.sub(" ", compact)
    base = " ".join(without_urls.split()).strip() or compact
    if not add_verification:
        return (base,)
    verification = f"{base} official source latest verification"
    return tuple(dict.fromkeys(item for item in (base, verification) if item))


def _needs_official_source_variant(query: str, *, task: TaskSpec) -> bool:
    domain = (task.domain or "").strip().lower()
    if domain in {"stock", "crypto"}:
        return False
    return bool(
        re.search(
            r"\b(?:"
            r"aktuell|heute|jetzt|neueste|stand|"
            r"current|latest|now|today|status|"
            r"release|version|changelog|downloads?|docs?|documentation|official"
            r")\b",
            query or "",
            re.IGNORECASE,
        )
    )


def _label_search_results(results: tuple[SearchResult, ...]) -> tuple[SearchResult, ...]:
    labeled: list[SearchResult] = []
    for result in results:
        metadata = dict(result.metadata)
        role = _source_role(result)
        quality = _quality_label(result, role=role)
        metadata["source_role"] = role
        metadata["quality_label"] = quality
        labeled.append(replace(result, metadata=metadata))
    return tuple(labeled)



_AUTHORITATIVE_SOURCE_TYPES = {SOURCE_TYPE_OFFICIAL, SOURCE_TYPE_DOCS}
_AUTHORITATIVE_PREFERENCE_WEIGHT = -2.0
_AUTHORITATIVE_QUERY_TERMS = {
    "api",
    "apis",
    "changelog",
    "docs",
    "documentation",
    "official",
    "primary",
    "reference",
    "release",
    "releases",
    "source",
    "version",
    "versions",
    "bewertung",
    "valuation",
    "fundamental",
    "fundamentals",
    "report",
    "bericht",
    "geschaeftsbericht",
    "geschäftsbericht",
    "investor",
    "relations",
}
_FINANCE_MARKET_DATA_HOST_PARTS = (
    "boerse",
    "börse",
    "deutsche-boerse",
    "finanznachrichten",
    "finanzen.",
    "finance.yahoo",
    "investing.",
    "marketbeat",
    "markets.businessinsider",
    "marketscreener",
    "marketwatch",
    "nasdaq",
    "nyse",
    "tradingview",
)
_FINANCE_WEAK_CONTEXT_HOST_PARTS = (
    "glassdoor",
    "kununu",
    "statista",
)
_HOST_TOKEN_STOPWORDS = {
    "api",
    "app",
    "blog",
    "cloud",
    "com",
    "core",
    "dev",
    "developer",
    "developers",
    "docs",
    "help",
    "io",
    "net",
    "org",
    "platform",
    "support",
    "www",
}
_QUERY_STOPWORDS = {
    "a",
    "about",
    "an",
    "and",
    "are",
    "aktuell",
    "aktuelle",
    "aktuellen",
    "aktueller",
    "as",
    "auf",
    "by",
    "der",
    "die",
    "das",
    "do",
    "does",
    "for",
    "from",
    "has",
    "heute",
    "have",
    "how",
    "i",
    "in",
    "is",
    "ist",
    "latest",
    "new",
    "neueste",
    "jetzt",
    "of",
    "official",
    "on",
    "please",
    "show",
    "stand",
    "the",
    "to",
    "und",
    "was",
    "what",
    "when",
    "with",
    "zur",
}


def _annotate_authoritative_search_results(
    results: tuple[SearchResult, ...],
    *,
    request: CurrentInfoRequest,
    task: TaskSpec,
) -> tuple[SearchResult, ...]:
    if not _needs_authoritative_primary_sources(request=request, task=task):
        return results
    annotated: list[SearchResult] = []
    for result in results:
        if not _is_authoritative_primary_result(result, request=request, task=task):
            annotated.append(result)
            continue
        metadata = dict(result.metadata)
        metadata.setdefault("source_type", _classified_source_type(result))
        metadata.setdefault("source_role", "official_source_candidate")
        metadata.setdefault("quality_label", "official_source_candidate")
        if _is_preferred_authoritative_primary_result(result, request=request, task=task):
            metadata.setdefault("source_observation_outcome", "confirmed")
            metadata.setdefault("source_observation_confidence", 1.0)
            metadata.setdefault("source_observation_penalty", -1.0)
            metadata.setdefault("source_preference_signal", "trusted")
            metadata.setdefault("source_preference_weight", _AUTHORITATIVE_PREFERENCE_WEIGHT)
            metadata.setdefault("source_preference_scope", "authoritative_primary_docs")
            metadata.setdefault("source_preference_source", "inferred_authoritative_primary")
        annotated.append(replace(result, metadata=metadata))
    return tuple(annotated)


def _builtin_source_preferences_for_results(
    results: tuple[SearchResult, ...],
    *,
    request: CurrentInfoRequest,
    domain: str,
) -> dict[str, Mapping[str, object]]:
    task = TaskSpec(task_type="", query=request.query, domain=domain or request.domain_hint)
    preferences: dict[str, Mapping[str, object]] = {}
    if (domain or request.domain_hint or "").strip().lower() in {"stock", "crypto"}:
        for result in results:
            host = _host_from_search_result(result)
            if not host:
                continue
            finance_preference = _finance_source_preference(result, request=request, task=task)
            if finance_preference:
                preferences[host] = finance_preference
    if not _needs_authoritative_primary_sources(request=request, task=task):
        return preferences
    for result in results:
        if not _is_preferred_authoritative_primary_result(result, request=request, task=task):
            continue
        host = _host_from_search_result(result)
        if not host:
            continue
        preferences[host] = {
            "source_preference_signal": "trusted",
            "source_preference_weight": _AUTHORITATIVE_PREFERENCE_WEIGHT,
            "source_preference_scope": "authoritative_primary_docs",
            "source_preference_source": "inferred_authoritative_primary",
        }
    return preferences


def _finance_source_preference(
    result: SearchResult,
    *,
    request: CurrentInfoRequest,
    task: TaskSpec,
) -> Mapping[str, object] | None:
    host = _host_from_search_result(result)
    if not host:
        return None
    if any(part in host for part in _FINANCE_WEAK_CONTEXT_HOST_PARTS):
        return {
            "source_preference_signal": "low_quality",
            "source_preference_weight": 1.25,
            "source_preference_scope": "finance_context_source",
            "source_preference_source": "inferred_weak_finance_context",
        }
    if any(part in host for part in _FINANCE_MARKET_DATA_HOST_PARTS):
        return {
            "source_preference_signal": "preferred",
            "source_preference_weight": -1.5,
            "source_preference_scope": "finance_market_data",
            "source_preference_source": "inferred_market_data_source",
        }
    if _host_matches_query_terms(result, request=request, task=task) and _looks_like_finance_primary_source(result):
        return {
            "source_preference_signal": "trusted",
            "source_preference_weight": -1.75,
            "source_preference_scope": "finance_primary_source",
            "source_preference_source": "inferred_finance_primary_source",
        }
    return None


def _needs_authoritative_primary_sources(*, request: CurrentInfoRequest, task: TaskSpec) -> bool:
    text = " ".join((request.query or "", task.query or "", task.domain or "", request.domain_hint or "")).casefold()
    if not text.strip():
        return False
    return any(term in _query_terms(text) for term in _AUTHORITATIVE_QUERY_TERMS)


def _is_authoritative_primary_result(result: SearchResult, *, request: CurrentInfoRequest, task: TaskSpec) -> bool:
    provider = (result.provider or "").strip().lower()
    existing_role = str(result.metadata.get("source_role") or "").strip().lower()
    if provider == "direct_user_url" or existing_role == "direct_user_url":
        return True
    source_type = _classified_source_type(result)
    if source_type in _AUTHORITATIVE_SOURCE_TYPES:
        return True
    if _looks_like_primary_source_for_query(result, request=request, task=task):
        return True
    return _looks_like_official_source(result)


def _is_preferred_authoritative_primary_result(
    result: SearchResult,
    *,
    request: CurrentInfoRequest,
    task: TaskSpec,
) -> bool:
    provider = (result.provider or "").strip().lower()
    existing_role = str(result.metadata.get("source_role") or "").strip().lower()
    if provider == "direct_user_url" or existing_role == "direct_user_url":
        return True
    if not _host_matches_query_terms(result, request=request, task=task):
        return False
    source_type = _classified_source_type(result)
    return (
        source_type in _AUTHORITATIVE_SOURCE_TYPES
        or _looks_like_primary_source_for_query(result, request=request, task=task)
        or _looks_like_official_source(result)
    )


def _looks_like_primary_source_for_query(
    result: SearchResult,
    *,
    request: CurrentInfoRequest,
    task: TaskSpec,
) -> bool:
    if not _host_matches_query_terms(result, request=request, task=task):
        return False
    text = f"{result.title} {result.snippet} {urlparse(result.url or '').path}".casefold()
    return bool(
        re.search(
            r"\b(?:api|changelog|docs?|documentation|reference|release|platform|developer|business|mini|paid|"
            r"investor|relations|annual|report|bericht|geschäftsbericht|geschaeftsbericht|"
            r"bewertung|valuation|fundamental|finanzbericht)\b",
            text,
            re.IGNORECASE,
        )
    )


def _looks_like_finance_primary_source(result: SearchResult) -> bool:
    text = f"{result.title} {result.snippet} {urlparse(result.url or '').path}".casefold()
    return bool(
        re.search(
            r"\b(?:investor|relations|annual|report|bericht|geschäftsbericht|geschaeftsbericht|"
            r"finanzbericht|financial|results|earnings|rating|stocks?|bonds?)\b",
            text,
            re.IGNORECASE,
        )
    )


def _host_matches_query_terms(
    result: SearchResult,
    *,
    request: CurrentInfoRequest,
    task: TaskSpec,
) -> bool:
    host = _host_from_search_result(result)
    if not host:
        return False
    host_tokens = {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9-]{2,}", host.casefold())
        if token not in _HOST_TOKEN_STOPWORDS
    }
    if not host_tokens:
        return False
    query_terms = set(_query_terms(" ".join((request.query or "", task.query or ""))))
    if not host_tokens.intersection(query_terms):
        return False
    return True


def _classified_source_type(result: SearchResult) -> str:
    existing = str(result.metadata.get("source_type") or "")
    if existing:
        return existing
    return classify_source_type(result)


def _is_fetched_authoritative_primary_document(
    document: FetchedDocument,
    *,
    request: CurrentInfoRequest,
    task: TaskSpec,
) -> bool:
    result = SearchResult(title=document.title, url=document.url, host=_host_from_url(document.url), metadata=document.metadata)
    return _is_authoritative_primary_result(result, request=request, task=task)


def _prepare_evidence_chunks_for_request(
    chunks: tuple[EvidenceChunk, ...],
    *,
    documents: tuple[FetchedDocument, ...],
    request: CurrentInfoRequest,
    task: TaskSpec,
) -> tuple[EvidenceChunk, ...]:
    if not _needs_authoritative_primary_sources(request=request, task=task):
        return chunks
    prepared = list(chunks)
    existing_urls = {chunk.source_url for chunk in prepared if chunk.source_url}
    for document in documents:
        if (
            not document.url
            or document.url in existing_urls
            or not _is_fetched_authoritative_primary_document(document, request=request, task=task)
        ):
            continue
        text = " ".join(document.text.split())
        if not text:
            continue
        source_type = _classified_source_type(
            SearchResult(title=document.title, url=document.url, host=_host_from_url(document.url), metadata=document.metadata)
        )
        prepared.append(
            EvidenceChunk(
                text=text[:1200],
                source_url=document.url,
                source_title=document.title,
                relevance=1.0,
                metadata={
                    "source_type": source_type,
                    "source_role": "official_source_candidate",
                    "quality_label": "official_source_candidate",
                    "retrieval": "fetched_document",
                },
            )
        )
    fetched_authoritative_urls = {
        document.url
        for document in documents
        if document.url and _is_fetched_authoritative_primary_document(document, request=request, task=task)
    }
    if not fetched_authoritative_urls:
        return chunks
    rescored = [
        _mark_irrelevant_authoritative_chunk(
            chunk,
            request=request,
            task=task,
            fetched_authoritative_urls=fetched_authoritative_urls,
        )
        for chunk in prepared
    ]
    return tuple(
        sorted(
            rescored,
            key=lambda chunk: _evidence_chunk_priority(chunk, fetched_authoritative_urls=fetched_authoritative_urls),
        )
    )


def _mark_irrelevant_authoritative_chunk(
    chunk: EvidenceChunk,
    *,
    request: CurrentInfoRequest,
    task: TaskSpec,
    fetched_authoritative_urls: set[str],
) -> EvidenceChunk:
    if chunk.source_url not in fetched_authoritative_urls:
        return chunk
    if _chunk_relevant_to_query(chunk, request=request, task=task):
        return chunk
    metadata = dict(chunk.metadata)
    metadata["warning_codes"] = tuple(dict.fromkeys((*_metadata_warning_codes(metadata), "irrelevant_source")))
    return replace(chunk, metadata=metadata)


def _evidence_chunk_priority(chunk: EvidenceChunk, *, fetched_authoritative_urls: set[str]) -> tuple[int, int, float]:
    fetched_authoritative = chunk.source_url in fetched_authoritative_urls
    warning_codes = _metadata_warning_codes(chunk.metadata)
    irrelevant = "irrelevant_source" in warning_codes
    source_type = str(chunk.metadata.get("source_type") or "")
    return (
        0 if fetched_authoritative and not irrelevant else 1,
        0 if source_type in _AUTHORITATIVE_SOURCE_TYPES else 1,
        -float(chunk.relevance or 0.0),
    )


def _chunk_relevant_to_query(chunk: EvidenceChunk, *, request: CurrentInfoRequest, task: TaskSpec) -> bool:
    haystack = " ".join((chunk.source_title or "", chunk.text or "")).casefold()
    if not haystack.strip():
        return False
    query_terms = tuple(term for term in _query_terms(" ".join((request.query or "", task.query or ""))) if term not in _AUTHORITATIVE_QUERY_TERMS)
    if not query_terms:
        query_terms = _query_terms(" ".join((request.query or "", task.query or "")))
    if not query_terms:
        return True
    matches = sum(1 for term in dict.fromkeys(query_terms) if term in haystack)
    required = 1 if len(set(query_terms)) <= 2 else 2
    return matches >= required


def _metadata_warning_codes(metadata: Mapping[str, object]) -> tuple[str, ...]:
    warnings = metadata.get("warning_codes") or metadata.get("warnings") or ()
    if isinstance(warnings, str):
        return tuple(item for item in warnings.replace(",", " ").split() if item)
    if isinstance(warnings, (tuple, list, set)):
        return tuple(str(item) for item in warnings if str(item).strip())
    return ()


def _query_terms(text: str) -> tuple[str, ...]:
    terms: list[str] = []
    for term in re.findall(r"[a-zA-Z0-9][a-zA-Z0-9_.+-]{1,}", (text or "").casefold()):
        normalized = term.strip("._-+")
        if len(normalized) < 2 or normalized in _QUERY_STOPWORDS:
            continue
        terms.append(normalized)
    return tuple(dict.fromkeys(terms))


def _source_role(result: SearchResult) -> str:
    provider = (result.provider or "").strip().lower()
    existing = str(result.metadata.get("source_role") or "").strip()
    if provider == "direct_user_url" or existing == "direct_user_url":
        return "direct_user_url"
    source_type = str(result.metadata.get("source_type") or "")
    if str(result.metadata.get("source_preference_source") or "") == "inferred_authoritative_primary":
        return "official_source_candidate"
    if source_type in {SOURCE_TYPE_OFFICIAL, SOURCE_TYPE_DOCS}:
        return "official_source_candidate"
    if source_type == SOURCE_TYPE_MARKET_DATA:
        return "corroborating_source"
    if _looks_like_official_source(result):
        return "official_source_candidate"
    if source_type == SOURCE_TYPE_NEWS:
        return "corroborating_source"
    if result.snippet and not result.metadata.get("canonical_url"):
        return "snippet_only"
    return "corroborating_source"


def _quality_label(result: SearchResult, *, role: str) -> str:
    if role in {"direct_user_url", "official_source_candidate"}:
        return role
    if result.date and str(result.metadata.get("stale") or "").lower() in {"1", "true", "yes", "stale"}:
        return "stale"
    if role == "snippet_only":
        return "snippet_only"
    source_type = str(result.metadata.get("source_type") or "")
    if source_type in {"Forum", "Social", "Commerce", "Unknown"} and result.snippet:
        return "weak_source"
    return "corroborating_source"


def _looks_like_official_source(result: SearchResult) -> bool:
    host = (result.host or urlparse(result.url).hostname or "").lower().rstrip(".")
    title = (result.title or "").casefold()
    if host.startswith("docs.") or ".docs." in host:
        return True
    if host.endswith(".gov") or ".gov." in host:
        return True
    return "official" in title or "documentation" in title or "docs" in title


def _fetch_priority_results(search_results: tuple[SearchResult, ...]) -> tuple[SearchResult, ...]:
    return tuple(
        sorted(
            search_results,
            key=lambda result: (
                0 if (result.provider or "").strip().lower() == "direct_user_url" else 1,
                _fetch_priority_source_score(result),
                result.rank,
            ),
        )
    )


def _fetch_priority_source_score(result: SearchResult) -> int:
    if str(result.metadata.get("source_preference_source") or "") == "inferred_authoritative_primary":
        return 0
    if str(result.metadata.get("source_role") or "") == "official_source_candidate":
        return 1
    if str(result.metadata.get("source_type") or "") in {SOURCE_TYPE_OFFICIAL, SOURCE_TYPE_DOCS, SOURCE_TYPE_MARKET_DATA}:
        return 1
    return 2


def _host_from_url(url: str) -> str:
    return normalize_source_host(urlparse(url).hostname)


def _fetch_url_candidates(result: SearchResult) -> tuple[str, ...]:
    urls: list[str] = []
    for raw in (result.url, str(result.metadata.get("original_url") or ""), str(result.metadata.get("final_url") or "")):
        if raw and raw not in urls:
            urls.append(raw)
    parsed = urlparse(result.url)
    path = parsed.path or ""
    if result.url and path and not path.endswith("/") and "." not in path.rsplit("/", 1)[-1]:
        slash_url = f"{result.url}/"
        if slash_url not in urls:
            urls.append(slash_url)
    return tuple(urls)


def _is_finance_listing_query(*, request: CurrentInfoRequest, task: TaskSpec) -> bool:
    domain = (task.domain or request.domain_hint or "").strip().lower()
    text = " ".join((request.query, task.query))
    return domain in {"stock", "crypto"} and is_finance_listing_query(text)


def _finance_listing_followup_query(query: str) -> str:
    return f"{query.strip()} public listing ticker exchange derivative sources"


def _needs_stronger_evidence(
    *,
    request: CurrentInfoRequest,
    task: TaskSpec,
    warnings: tuple[str, ...],
) -> bool:
    if any(
        warning in warnings
        for warning in (
            "irrelevant_source",
            "weak_source",
            "low_quality_source",
            "stale_source",
        )
    ):
        return True
    if _is_finance_listing_query(request=request, task=task):
        return any(
            warning in warnings
            for warning in (
                "needs_independent_source",
                "source_conflict",
                "stale_source",
                "finance_listing_requires_verified_sources",
            )
        )
    return False


def _stronger_evidence_reason(warnings: tuple[str, ...]) -> str:
    for warning in (
        "source_conflict",
        "stale_source",
        "irrelevant_source",
        "weak_source",
        "low_quality_source",
        "needs_independent_source",
        "finance_listing_requires_verified_sources",
    ):
        if warning in warnings:
            return warning
    return "insufficient_verified_evidence"
