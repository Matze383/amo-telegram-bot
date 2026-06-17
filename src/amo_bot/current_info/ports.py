from __future__ import annotations

from typing import Protocol

from amo_bot.current_info.models import (
    CurrentInfoRequest,
    EvidenceChunk,
    FetchedDocument,
    QueryPlan,
    SearchProviderResponse,
    SearchResult,
    TaskSpec,
)


class CurrentInfoTaskPlanner(Protocol):
    def plan_task(self, request: CurrentInfoRequest) -> TaskSpec:
        ...


class CurrentInfoQueryPlanner(Protocol):
    def plan_queries(self, *, request: CurrentInfoRequest, task: TaskSpec) -> QueryPlan:
        ...


class CurrentInfoSearchProvider(Protocol):
    def search(self, *, query: str, locale: str, max_results: int) -> tuple[SearchResult, ...] | SearchProviderResponse:
        ...


class CurrentInfoFetchProvider(Protocol):
    def fetch(self, *, url: str, locale: str) -> FetchedDocument | None:
        ...


class CurrentInfoRetrievalProvider(Protocol):
    def retrieve(
        self,
        *,
        request: CurrentInfoRequest,
        documents: tuple[FetchedDocument, ...],
        search_results: tuple[SearchResult, ...],
    ) -> tuple[EvidenceChunk, ...]:
        ...
