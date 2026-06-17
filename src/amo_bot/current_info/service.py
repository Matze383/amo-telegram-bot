from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from amo_bot.current_info.candidates import normalize_dedupe_and_rank_search_results
from amo_bot.current_info.evidence import assemble_evidence_package
from amo_bot.current_info.models import (
    CurrentInfoAnswer,
    CurrentInfoRequest,
    EvidenceChunk,
    FetchedDocument,
    QueryPlan,
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
)
from amo_bot.current_info.ports import (
    CurrentInfoFetchProvider,
    CurrentInfoQueryPlanner,
    CurrentInfoRetrievalProvider,
    CurrentInfoSearchProvider,
    CurrentInfoTaskPlanner,
)
from amo_bot.current_info.search import SearchProviderError


logger = logging.getLogger(__name__)


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
        return QueryPlan(
            task=task,
            queries=(query,) if query else (),
            max_results=request.max_results,
            strategy="search_first",
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
        safety_config: CurrentInfoSafetyConfig | None = None,
    ) -> None:
        self._search_provider = search_provider
        self._fetch_provider = fetch_provider
        self._retrieval_provider = retrieval_provider or SnippetRetrievalProvider()
        self._task_planner = task_planner or DefaultCurrentInfoTaskPlanner()
        self._query_planner = query_planner or DefaultCurrentInfoQueryPlanner()
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

        if self._search_provider is None:
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
            search_response = self._run_search_plan(query_plan, locale=task.locale, budget=budget, request=request)
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

        self._log_synthesis(request=request, task=task, started=started, status="answered", budget=budget)
        return CurrentInfoAnswer(
            status="answered",
            answer_text=_format_answer_text(chunks),
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
    ) -> SearchProviderResponse:
        assert self._search_provider is not None
        collected: list[SearchResult] = []
        metrics: list[SearchProviderMetric] = []
        seen_urls: set[str] = set()
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
        return SearchProviderResponse(
            results=normalize_dedupe_and_rank_search_results(tuple(collected), max_results=query_plan.max_results),
            metrics=tuple(metrics),
        )

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

        for result in search_results:
            if not result.url:
                continue
            try:
                budget.consume_fetch_run()
            except CurrentInfoBudgetExceeded as exc:
                budget.warnings.append(exc.reason_code)
                break
            fetch_started = time.perf_counter()
            document = self._fetch_provider.fetch(url=result.url, locale=request.locale)
            log_current_info_event(
                logger,
                event="current_info.FetchRun",
                stage="fetch",
                query=request.query,
                chat_id=request.chat_id,
                user_id=request.user_id,
                topic_id=request.topic_id,
                duration_ms=int((time.perf_counter() - fetch_started) * 1000),
                outcome="hit" if document is not None else "miss",
                extra={"host": result.host, "provider_kind": "fetch"},
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


def _format_answer_text(chunks: tuple[EvidenceChunk, ...]) -> str:
    lines: list[str] = []
    for chunk in chunks[:3]:
        text = " ".join(chunk.text.split())
        if text:
            lines.append(text)
    return "\n\n".join(lines)
