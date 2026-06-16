from __future__ import annotations

from dataclasses import dataclass

from amo_bot.current_info import (
    CurrentInfoAnswer,
    CurrentInfoRequest,
    CurrentInfoService,
    EvidenceChunk,
    EvidencePackage,
    FetchedDocument,
    QueryPlan,
    SearchBundle,
    SearchResult,
    TaskSpec,
)
from amo_bot.current_info.legacy_webtool import LegacyWebtoolCurrentInfoService


@dataclass
class _SearchCall:
    query: str
    locale: str
    max_results: int


class _FakeSearchProvider:
    def __init__(self, results: tuple[SearchResult, ...]) -> None:
        self.results = results
        self.calls: list[_SearchCall] = []

    def search(self, *, query: str, locale: str, max_results: int) -> tuple[SearchResult, ...]:
        self.calls.append(_SearchCall(query=query, locale=locale, max_results=max_results))
        return self.results[:max_results]


class _FakeFetchProvider:
    def __init__(self, documents: dict[str, FetchedDocument]) -> None:
        self.documents = documents
        self.calls: list[tuple[str, str]] = []

    def fetch(self, *, url: str, locale: str) -> FetchedDocument | None:
        self.calls.append((url, locale))
        return self.documents.get(url)


class _FakeRetrievalProvider:
    def __init__(self, chunks: tuple[EvidenceChunk, ...]) -> None:
        self.chunks = chunks
        self.calls: list[tuple[CurrentInfoRequest, tuple[FetchedDocument, ...], tuple[SearchResult, ...]]] = []

    def retrieve(
        self,
        *,
        request: CurrentInfoRequest,
        documents: tuple[FetchedDocument, ...],
        search_results: tuple[SearchResult, ...],
    ) -> tuple[EvidenceChunk, ...]:
        self.calls.append((request, documents, search_results))
        return self.chunks


def test_current_info_models_roundtrip_dict_serialization():
    request = CurrentInfoRequest(
        query="latest AMO news",
        locale="de",
        domain_hint="news",
        max_results=2,
        max_documents=1,
        user_id=42,
        chat_id=-100,
        topic_id=7,
        role="owner",
        metadata={"origin": "unit-test"},
    )
    task = TaskSpec(task_type="current_info", query=request.query, locale="de", domain="news")
    plan = QueryPlan(task=task, queries=("latest AMO news",), max_results=2)
    result = SearchResult(
        title="AMO",
        url="https://example.com/amo",
        snippet="Fresh details",
        provider="fake",
        rank=1,
    )
    document = FetchedDocument(
        url=result.url,
        text="Full current information",
        title=result.title,
        fetched_at="2026-06-16T10:00:00+00:00",
        status_code=200,
        provider="fake_fetch",
    )
    chunk = EvidenceChunk(
        text="Full current information",
        source_url=result.url,
        source_title=result.title,
        relevance=0.9,
    )
    answer = CurrentInfoAnswer(
        status="answered",
        answer_text="Full current information",
        request=request,
        task=task,
        query_plan=plan,
        search_bundle=SearchBundle(query_plan=plan, results=(result,)),
        evidence=EvidencePackage(chunks=(chunk,), documents=(document,)),
        sources=(result.url,),
        warnings=("test_warning",),
        metadata={"provider_mode": "fake"},
    )

    restored = CurrentInfoAnswer.from_dict(answer.to_dict())

    assert restored == answer
    assert restored.answered is True
    assert restored.evidence is not None
    assert restored.evidence.documents[0].status_code == 200


def test_current_info_service_uses_fake_search_fetch_and_retrieval_ports():
    result = SearchResult(
        title="Status page",
        url="https://example.com/status",
        snippet="Fallback snippet",
        provider="fake_search",
        rank=1,
    )
    document = FetchedDocument(
        url=result.url,
        title=result.title,
        text="Current status is green.",
        provider="fake_fetch",
    )
    chunk = EvidenceChunk(
        text="Current status is green.",
        source_url=result.url,
        source_title=result.title,
        relevance=1.0,
    )
    search_provider = _FakeSearchProvider((result,))
    fetch_provider = _FakeFetchProvider({result.url: document})
    retrieval_provider = _FakeRetrievalProvider((chunk,))
    service = CurrentInfoService(
        search_provider=search_provider,
        fetch_provider=fetch_provider,
        retrieval_provider=retrieval_provider,
    )

    answer = service.answer(CurrentInfoRequest(query="current status", locale="de", max_results=3))

    assert answer.status == "answered"
    assert answer.answer_text == "Current status is green."
    assert answer.sources == (result.url,)
    assert search_provider.calls == [_SearchCall(query="current status", locale="de", max_results=3)]
    assert fetch_provider.calls == [(result.url, "de")]
    assert retrieval_provider.calls[0][1] == (document,)
    assert retrieval_provider.calls[0][2] == (result,)


def test_current_info_service_uses_snippets_when_no_fetch_provider_is_configured():
    result = SearchResult(
        title="Search result",
        url="https://example.com/search-only",
        snippet="Search snippets can support tests.",
        provider="fake_search",
        rank=1,
    )
    service = CurrentInfoService(search_provider=_FakeSearchProvider((result,)))

    answer = service.answer(CurrentInfoRequest(query="current info"))

    assert answer.status == "answered"
    assert answer.answer_text == "Search snippets can support tests."
    assert answer.evidence is not None
    assert answer.evidence.documents == ()
    assert answer.evidence.chunks[0].source_url == result.url


def test_current_info_service_fails_closed_without_search_provider():
    service = CurrentInfoService()

    answer = service.answer(CurrentInfoRequest(query="current info"))

    assert answer.status == "provider_unavailable"
    assert answer.warnings == ("search_provider_not_configured",)
    assert answer.search_bundle is None


def test_current_info_service_reports_empty_result_from_fake_provider():
    service = CurrentInfoService(search_provider=_FakeSearchProvider(()))

    answer = service.answer(CurrentInfoRequest(query="current info"))

    assert answer.status == "empty_result"
    assert answer.search_bundle is not None
    assert answer.search_bundle.results == ()
    assert answer.warnings == ("empty_search_result",)


def test_legacy_webtool_current_info_adapter_keeps_old_dispatcher_activatable():
    class _Dispatcher:
        def __init__(self) -> None:
            self.requests = []

        def execute(self, request):
            self.requests.append(request)

            class _Result:
                allowed = True
                decision = "allow"
                reason = "allow"
                text = "Legacy webtool answer"
                sources = ("https://example.com/legacy",)
                hosts = ("example.com",)
                result_type = "websearch_summary"

            return _Result()

    dispatcher = _Dispatcher()
    service = LegacyWebtoolCurrentInfoService(dispatcher=dispatcher)

    answer = service.answer(
        CurrentInfoRequest(
            query="latest status",
            locale="en",
            domain_hint="news",
            user_id=123,
            chat_id=-100,
            role="owner",
        )
    )

    assert answer.status == "answered"
    assert answer.answer_text == "Legacy webtool answer"
    assert answer.sources == ("https://example.com/legacy",)
    assert answer.metadata["legacy_webtool"] is True
    assert dispatcher.requests[0].capability == "websearch"
    assert dispatcher.requests[0].query == "latest status"
