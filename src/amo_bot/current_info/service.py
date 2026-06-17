from __future__ import annotations

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
from amo_bot.current_info.ports import (
    CurrentInfoFetchProvider,
    CurrentInfoQueryPlanner,
    CurrentInfoRetrievalProvider,
    CurrentInfoSearchProvider,
    CurrentInfoTaskPlanner,
)


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
    ) -> None:
        self._search_provider = search_provider
        self._fetch_provider = fetch_provider
        self._retrieval_provider = retrieval_provider or SnippetRetrievalProvider()
        self._task_planner = task_planner or DefaultCurrentInfoTaskPlanner()
        self._query_planner = query_planner or DefaultCurrentInfoQueryPlanner()

    def answer(self, request: CurrentInfoRequest) -> CurrentInfoAnswer:
        task = self._task_planner.plan_task(request)
        query_plan = self._query_planner.plan_queries(request=request, task=task)
        if not task.query or not query_plan.queries:
            return CurrentInfoAnswer(
                status="invalid_request",
                request=request,
                task=task,
                query_plan=query_plan,
                warnings=("empty_query",),
            )

        if self._search_provider is None:
            return CurrentInfoAnswer(
                status="provider_unavailable",
                request=request,
                task=task,
                query_plan=query_plan,
                warnings=("search_provider_not_configured",),
            )

        search_response = self._run_search_plan(query_plan, locale=task.locale)
        search_results = search_response.results
        search_bundle = SearchBundle(query_plan=query_plan, results=search_results, metrics=search_response.metrics)
        if not search_results:
            return CurrentInfoAnswer(
                status="empty_result",
                request=request,
                task=task,
                query_plan=query_plan,
                search_bundle=search_bundle,
                warnings=("empty_search_result",),
            )

        documents = self._fetch_documents(search_results, request=request)
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
        if not chunks:
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
            )
        if "snippet_only_evidence" in evidence.warnings:
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
                metadata={"reason": "current_facts_need_fetched_sources"},
            )

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
        )

    def _run_search_plan(self, query_plan: QueryPlan, *, locale: str) -> SearchProviderResponse:
        assert self._search_provider is not None
        collected: list[SearchResult] = []
        metrics: list[SearchProviderMetric] = []
        seen_urls: set[str] = set()
        for query in query_plan.queries:
            provider_response = self._search_provider.search(query=query, locale=locale, max_results=query_plan.max_results)
            if isinstance(provider_response, SearchProviderResponse):
                results = provider_response.results
                metrics.extend(provider_response.metrics)
            else:
                results = tuple(provider_response)
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
            document = self._fetch_provider.fetch(url=result.url, locale=request.locale)
            if document is None or not document.text.strip():
                continue
            documents.append(document)
            if len(documents) >= max_documents:
                break
        return tuple(documents)


def _format_answer_text(chunks: tuple[EvidenceChunk, ...]) -> str:
    lines: list[str] = []
    for chunk in chunks[:3]:
        text = " ".join(chunk.text.split())
        if text:
            lines.append(text)
    return "\n\n".join(lines)
