from __future__ import annotations

import ast
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from amo_bot.current_info import (
    CurrentInfoAnswer,
    CurrentInfoRequest,
    CurrentInfoService,
    CurrentInfoSafetyConfig,
    EvidenceChunk,
    EvidencePackage,
    EvidencePackageSource,
    FetchedDocument,
    QueryPlan,
    ResearchPlan,
    ResearchPlanStep,
    SearchBundle,
    SearchResult,
    TaskSpec,
)
from amo_bot.current_info.observability import (
    CurrentInfoBudgetExceeded,
    HostConcurrencyLimiter,
    InMemoryRateLimiter,
    query_hash,
    safe_error_message,
)
from amo_bot.current_info.legacy_webtool import LegacyWebtoolCurrentInfoService
from amo_bot.current_info.search import SearchProviderRateLimited
from amo_bot.telegram.webtool_evidence import classify_evidence_domain


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


class _RateLimitedSearchProvider:
    def search(self, *, query: str, locale: str, max_results: int) -> tuple[SearchResult, ...]:
        del query, locale, max_results
        raise SearchProviderRateLimited("provider_rate_limited")


class _FakeFetchProvider:
    def __init__(self, documents: dict[str, FetchedDocument]) -> None:
        self.documents = documents
        self.calls: list[tuple[str, str]] = []

    def fetch(self, *, url: str, locale: str) -> FetchedDocument | None:
        self.calls.append((url, locale))
        return self.documents.get(url)


class _FailingThenSuccessfulFetchProvider:
    def __init__(self, *, failing_url: str, successful_url: str, document: FetchedDocument) -> None:
        self.failing_url = failing_url
        self.successful_url = successful_url
        self.document = document
        self.calls: list[tuple[str, str]] = []

    def fetch(self, *, url: str, locale: str) -> FetchedDocument | None:
        self.calls.append((url, locale))
        if url == self.failing_url:
            raise RuntimeError(
                "Stream is already consumed for "
                "https://upstream.example/path?token=leaky-token-123&safe=1 "
                "Authorization=Bearer live-fetch-token"
            )
        if url == self.successful_url:
            return self.document
        return None


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


class _FakeResearchProvider:
    def __init__(self, answer: CurrentInfoAnswer) -> None:
        self.answer_value = answer
        self.calls: list[tuple[CurrentInfoRequest, TaskSpec, QueryPlan]] = []

    def answer(self, *, request: CurrentInfoRequest, task: TaskSpec, query_plan: QueryPlan) -> CurrentInfoAnswer:
        self.calls.append((request, task, query_plan))
        return self.answer_value


class _PreferenceRepo:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def list_for_hosts(self, **kwargs) -> dict[str, dict[str, object]]:
        self.calls.append(dict(kwargs))
        return {
            "trusted.example": {"source_preference_signal": "trusted"},
            "bad.example": {"source_preference_signal": "rejected"},
        }


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
    plan = QueryPlan(
        task=task,
        queries=("latest AMO news",),
        max_results=2,
        research_plan=ResearchPlan(
            strategy="search_first",
            steps=(ResearchPlanStep(operation="search", reason="original_query", query="latest AMO news"),),
            query_variants=("latest AMO news",),
        ),
    )
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


def test_current_info_evidence_package_exposes_sources_freshness_confidence_and_warnings():
    package = EvidencePackage(
        sources=(
            EvidencePackageSource(
                url="https://example.com/status",
                title="Status",
                host="example.com",
                source_type="Official",
                source_role="official_source_candidate",
                quality_label="official_source_candidate",
                fetched=True,
                fetched_at="2026-06-16T10:00:00+00:00",
            ),
        ),
        freshness="fresh",
        confidence=0.72,
        warnings=("single_source",),
    )

    restored = EvidencePackage.from_dict(package.to_dict())

    assert restored == package
    assert restored.sources[0].fetched is True


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
    assert answer.confidence == 0.72
    assert answer.sources == (result.url,)
    assert answer.warnings == ()
    assert answer.evidence is not None
    assert answer.evidence.freshness == "fetched_unknown_age"
    assert answer.evidence.sources[0].fetched is True
    assert [call.query for call in search_provider.calls] == [
        "current status",
        "current status official source latest verification",
    ]
    assert fetch_provider.calls == [(result.url, "de")]
    assert retrieval_provider.calls[0][1] == (document,)
    assert retrieval_provider.calls[0][2][0].url == result.url
    assert retrieval_provider.calls[0][2][0].metadata["canonical_url"] == result.url
    assert answer.query_plan is not None
    assert answer.query_plan.research_plan is not None
    assert [step.operation for step in answer.query_plan.research_plan.steps] == ["search", "search"]
    assert answer.evidence.sources[0].source_role == "corroborating_source"


def test_current_info_service_rejects_snippet_only_evidence_for_current_facts():
    result = SearchResult(
        title="Search result",
        url="https://example.com/search-only",
        snippet="Search snippets can support tests.",
        provider="fake_search",
        rank=1,
    )
    service = CurrentInfoService(search_provider=_FakeSearchProvider((result,)))

    answer = service.answer(CurrentInfoRequest(query="current info"))

    assert answer.status == "unverified_evidence"
    assert answer.answer_text == ""
    assert answer.confidence == 0.0
    assert answer.evidence is not None
    assert answer.evidence.documents == ()
    assert answer.evidence.freshness == "snippet_only"
    assert answer.evidence.chunks[0].source_url == result.url
    assert answer.warnings == ("snippet_only_evidence",)


def test_current_info_service_treats_single_fetch_exception_as_non_fatal(caplog):
    first = SearchResult(
        title="Broken result",
        url="https://broken.example/status",
        provider="fake_search",
        rank=1,
        host="broken.example",
    )
    second = SearchResult(
        title="Working result",
        url="https://working.example/status",
        provider="fake_search",
        rank=2,
        host="working.example",
    )
    document = FetchedDocument(url=second.url, title=second.title, text="Current status is green.")
    chunk = EvidenceChunk(text=document.text, source_url=second.url, source_title=second.title)
    fetch_provider = _FailingThenSuccessfulFetchProvider(
        failing_url=first.url,
        successful_url=second.url,
        document=document,
    )
    service = CurrentInfoService(
        search_provider=_FakeSearchProvider((first, second)),
        fetch_provider=fetch_provider,
        retrieval_provider=_FakeRetrievalProvider((chunk,)),
    )

    with caplog.at_level(logging.INFO, logger="amo_bot.current_info.service"):
        answer = service.answer(CurrentInfoRequest(query="current status", max_results=2, max_documents=1))

    assert answer.status == "answered"
    assert answer.sources == (second.url,)
    assert fetch_provider.calls == [(first.url, "en"), (f"{first.url}/", "en"), (second.url, "en")]
    payloads = [ast.literal_eval(record.getMessage()) for record in caplog.records]
    fetch_errors = [
        payload
        for payload in payloads
        if payload["event"] == "current_info.FetchRun" and payload["outcome"] == "error"
    ]
    assert fetch_errors
    assert fetch_errors[-1]["reason_code"] == "fetch_provider_error"
    assert fetch_errors[-1]["error_class"] == "RuntimeError"
    assert "leaky-token-123" not in fetch_errors[-1]["error_message"]
    assert "live-fetch-token" not in fetch_errors[-1]["error_message"]
    assert "token=***REDACTED***&safe=1" in fetch_errors[-1]["error_message"]
    assert "Authorization=Bearer ***REDACTED***" in fetch_errors[-1]["error_message"]


def test_current_info_safe_error_message_redacts_common_secret_patterns() -> None:
    message = safe_error_message(
        RuntimeError(
            "failed with password=hunter2 secret:abc123 key=public-but-sensitive "
            "https://api.telegram.org/bot123456:ABCDEF/getMe?api_key=sk-secret123456&ok=1"
        )
    )

    assert "hunter2" not in message
    assert "abc123" not in message
    assert "public-but-sensitive" not in message
    assert "123456:ABCDEF" not in message
    assert "sk-secret123456" not in message
    assert "password=***REDACTED***" in message
    assert "secret:***REDACTED***" in message
    assert "key=***REDACTED***" in message
    assert "/bot***REDACTED***/getMe?api_key=***REDACTED***&ok=1" in message


def test_current_info_service_rejects_chunks_without_fetched_source_even_when_other_docs_fetched():
    fetched = SearchResult(
        title="Fetched",
        url="https://example.com/fetched",
        provider="fake_search",
        rank=1,
    )
    snippet_only = SearchResult(
        title="Snippet only",
        url="https://example.net/snippet",
        snippet="Unfetched snippet claim.",
        provider="fake_search",
        rank=2,
    )
    chunk = EvidenceChunk(
        text="Unfetched snippet claim.",
        source_url=snippet_only.url,
        source_title=snippet_only.title,
    )
    service = CurrentInfoService(
        search_provider=_FakeSearchProvider((fetched, snippet_only)),
        fetch_provider=_FakeFetchProvider(
            {fetched.url: FetchedDocument(url=fetched.url, title=fetched.title, text="Fetched background.")}
        ),
        retrieval_provider=_FakeRetrievalProvider((chunk,)),
    )

    answer = service.answer(CurrentInfoRequest(query="current info", max_results=2))

    assert answer.status == "unverified_evidence"
    assert answer.confidence == 0.0
    assert answer.warnings == ("snippet_only_evidence", "unfetched_chunk_evidence")


def test_current_info_service_routes_finance_listing_queries_to_listing_evidence_path():
    result = SearchResult(
        title="SPCXUSDT Contract | Bybit",
        url="https://www.bybit.com/en/trade/usdt/SPCXUSDT",
        snippet="Trade SPCXUSDT on Bybit.",
        provider="fake_search",
        rank=1,
        host="www.bybit.com",
    )
    document = FetchedDocument(
        url=result.url,
        title=result.title,
        text="Bybit lists SPCXUSDT as a pre-market perpetual contract.",
        provider="fake_fetch",
    )
    chunk = EvidenceChunk(
        text="Bybit lists SPCXUSDT as a pre-market perpetual contract.",
        source_url=result.url,
        source_title=result.title,
        relevance=0.9,
    )

    for query in (
        "Ist SpaceX an der Börse?",
        "Ist Anthropic an der Börse?",
        "Ist Siemens an der Börse?",
        "Ist Adidas börsennotiert?",
        "Ist Quarvex Labs an der Börse?",
        "Ist AcmeBlubBla an der Börse?",
        "Ist FooBarBaz AG an der Börse?",
        "Kann man SpaceX Aktien kaufen?",
        "Kann man Anthropic Aktien kaufen?",
        "Kann man Siemens Aktien kaufen?",
        "Kann man Adidas Aktien kaufen?",
        "Kann man Quarvex Labs Aktien kaufen?",
        "Kann man AcmeBlubBla Aktien kaufen?",
        "Kann man FooBarBaz AG Aktien kaufen?",
        "Nasdaq Anthropic",
        "Nasdaq Quarvex Labs",
        "Nasdaq AcmeBlubBla",
        "NYSE Anthropic",
        "NYSE FooBarBaz AG",
    ):
        search_provider = _FakeSearchProvider((result,))
        service = CurrentInfoService(
            search_provider=search_provider,
            fetch_provider=_FakeFetchProvider({result.url: document}),
            retrieval_provider=_FakeRetrievalProvider((chunk,)),
        )

        answer = service.answer(
            CurrentInfoRequest(
                query=query,
                locale="de",
                domain_hint=classify_evidence_domain(query),
                max_results=3,
            )
        )

        assert answer.task is not None
        assert answer.task.domain == "stock"
        assert answer.status == "unverified_evidence"
        assert answer.answer_text == ""
        assert "needs_independent_source" in answer.warnings
        assert "finance_listing_requires_verified_sources" in answer.warnings
        assert [call.query for call in search_provider.calls] == [
            query,
            f"{query} public listing ticker exchange derivative sources",
        ]


def test_current_info_service_fetches_user_provided_url_for_spacex_ipo_question():
    url = (
        "https://www.reutersconnect.com/item/"
        "spacexs-initial-public-offering-ipo-at-the-nasdaq-marketsite-in-new-york-city/"
        "dGFnOnJldXRlcnMuY29tLDIwMjY6bmV3c21sX1JDMktTTEFSWE05Vw"
    )
    document = FetchedDocument(
        url=url,
        title="SpaceX IPO at Nasdaq MarketSite",
        text="Reuters Connect page text about SpaceX's initial public offering at the Nasdaq MarketSite.",
        provider="fake_fetch",
    )
    chunk = EvidenceChunk(
        text="Reuters Connect page text about SpaceX's initial public offering at the Nasdaq MarketSite.",
        source_url=url,
        source_title=document.title,
        relevance=0.9,
    )
    search_provider = _FakeSearchProvider(())
    fetch_provider = _FakeFetchProvider({url: document})
    service = CurrentInfoService(
        search_provider=search_provider,
        fetch_provider=fetch_provider,
        retrieval_provider=_FakeRetrievalProvider((chunk,)),
    )

    query = f"Ist SpaceX an der Börse? Quelle: {url}"
    answer = service.answer(
        CurrentInfoRequest(
            query=query,
            locale="de",
            domain_hint=classify_evidence_domain(query),
            max_results=3,
            max_documents=2,
        )
    )

    assert answer.task is not None
    assert answer.task.domain == "stock"
    assert answer.status == "unverified_evidence"
    assert answer.answer_text == ""
    assert answer.sources == (url,)
    assert "needs_independent_source" in answer.warnings
    assert "finance_listing_requires_verified_sources" in answer.warnings
    assert answer.metadata["reason"] == "needs_independent_source"
    assert fetch_provider.calls == [(url, "de")]
    assert [call.query for call in search_provider.calls] == [
        query,
        f"{query} public listing ticker exchange derivative sources",
    ]


def test_current_info_service_fetches_user_provided_url_for_general_listing_question():
    url = "https://www.reutersconnect.com/item/anthropic-ipo-at-the-nasdaq-marketsite/example"
    document = FetchedDocument(
        url=url,
        title="Anthropic IPO at Nasdaq MarketSite",
        text="Reuters Connect page text about Anthropic's initial public offering at the Nasdaq MarketSite.",
        provider="fake_fetch",
    )
    chunk = EvidenceChunk(
        text="Reuters Connect page text about Anthropic's initial public offering at the Nasdaq MarketSite.",
        source_url=url,
        source_title=document.title,
        relevance=0.9,
    )
    search_provider = _FakeSearchProvider(())
    fetch_provider = _FakeFetchProvider({url: document})
    service = CurrentInfoService(
        search_provider=search_provider,
        fetch_provider=fetch_provider,
        retrieval_provider=_FakeRetrievalProvider((chunk,)),
    )

    query = f"Ist Anthropic an der Nasdaq? Quelle: {url}"
    answer = service.answer(
        CurrentInfoRequest(
            query=query,
            locale="de",
            domain_hint=classify_evidence_domain(query),
            max_results=3,
            max_documents=2,
        )
    )

    assert answer.task is not None
    assert answer.task.domain == "stock"
    assert answer.status == "unverified_evidence"
    assert answer.answer_text == ""
    assert answer.sources == (url,)
    assert "stock_entity_not_identified" not in answer.warnings
    assert "needs_independent_source" in answer.warnings
    assert "finance_listing_requires_verified_sources" in answer.warnings
    assert answer.metadata["reason"] == "needs_independent_source"
    assert fetch_provider.calls == [(url, "de")]


def test_current_info_service_blocks_single_source_anthropic_listing_from_fetched_web_evidence():
    result = SearchResult(
        title="Anthropic company profile",
        url="https://company.example/anthropic-profile",
        snippet="Anthropic is privately held.",
        provider="fake_search",
        rank=1,
        host="company.example",
    )
    document = FetchedDocument(
        url=result.url,
        title=result.title,
        text="Anthropic is a privately held company and does not have a public stock ticker.",
        provider="fake_fetch",
    )
    chunk = EvidenceChunk(
        text="Anthropic is a privately held company and does not have a public stock ticker.",
        source_url=result.url,
        source_title=result.title,
        relevance=0.9,
    )
    search_provider = _FakeSearchProvider((result,))
    service = CurrentInfoService(
        search_provider=search_provider,
        fetch_provider=_FakeFetchProvider({result.url: document}),
        retrieval_provider=_FakeRetrievalProvider((chunk,)),
    )

    query = "Ist Anthropic an der Börse?"
    answer = service.answer(
        CurrentInfoRequest(
            query=query,
            locale="de",
            domain_hint=classify_evidence_domain(query),
            max_results=3,
        )
    )

    assert answer.task is not None
    assert answer.task.domain == "stock"
    assert answer.status == "unverified_evidence"
    assert answer.answer_text == ""
    assert answer.sources == (result.url,)
    assert "needs_independent_source" in answer.warnings
    assert "finance_listing_requires_verified_sources" in answer.warnings
    assert answer.metadata["reason"] == "needs_independent_source"
    assert [call.query for call in search_provider.calls] == [
        query,
        f"{query} public listing ticker exchange derivative sources",
    ]


def test_current_info_service_fetches_non_finance_direct_url_as_first_evidence():
    url = "https://docs.python.org/3/whatsnew/3.13.html"
    document = FetchedDocument(
        url=url,
        title="What is new in Python 3.13",
        text="Python 3.13 includes the updated interactive interpreter.",
        provider="fake_fetch",
    )
    fetch_provider = _FakeFetchProvider({url: document})
    service = CurrentInfoService(search_provider=_FakeSearchProvider(()), fetch_provider=fetch_provider)

    answer = service.answer(CurrentInfoRequest(query=f"Bewerte diese Quelle: {url}", locale="de", max_documents=2))

    assert answer.status == "answered"
    assert answer.sources == (url,)
    assert fetch_provider.calls[0] == (url, "de")
    assert answer.query_plan is not None
    assert answer.query_plan.strategy == "direct_url_first"
    assert answer.query_plan.research_plan is not None
    assert answer.query_plan.research_plan.steps[0].operation == "direct_url_fetch"
    assert answer.evidence is not None
    assert answer.evidence.sources[0].source_role == "direct_user_url"
    assert answer.evidence.sources[0].quality_label == "direct_user_url"


def test_current_info_service_uses_direct_url_and_independent_search_for_source_assessment():
    user_url = "https://example.org/blog/release-note"
    corroborating = SearchResult(
        title="Release docs",
        url="https://official.example/docs/release",
        snippet="Official release docs.",
        provider="fake_search",
        rank=1,
        host="official.example",
    )
    documents = {
        user_url: FetchedDocument(url=user_url, title="Blog", text="The project released version 4.2."),
        corroborating.url: FetchedDocument(
            url=corroborating.url,
            title=corroborating.title,
            text="The official release documentation confirms version 4.2.",
        ),
    }
    search_provider = _FakeSearchProvider((corroborating,))
    fetch_provider = _FakeFetchProvider(documents)
    service = CurrentInfoService(search_provider=search_provider, fetch_provider=fetch_provider)

    query = f"Kann ich dieser Einschätzung trauen? {user_url}"
    answer = service.answer(CurrentInfoRequest(query=query, locale="de", max_results=3, max_documents=3))

    assert answer.status == "answered"
    assert fetch_provider.calls[0] == (user_url, "de")
    assert corroborating.url in [url for url, _locale in fetch_provider.calls]
    assert [call.query for call in search_provider.calls] == [
        "Kann ich dieser Einschätzung trauen?",
        "Kann ich dieser Einschätzung trauen? official source latest verification",
    ]
    assert answer.evidence is not None
    assert {source.source_role for source in answer.evidence.sources} >= {
        "direct_user_url",
        "official_source_candidate",
    }


def test_current_info_service_searches_official_source_variant_for_general_topic():
    official = SearchResult(
        title="Python Release Schedule",
        url="https://docs.python.org/3/",
        snippet="Official Python downloads.",
        provider="fake_search",
        rank=1,
        host="docs.python.org",
    )
    search_provider = _FakeSearchProvider((official,))
    service = CurrentInfoService(
        search_provider=search_provider,
        fetch_provider=_FakeFetchProvider(
            {official.url: FetchedDocument(url=official.url, title=official.title, text="Python 3.13.5 is available.")}
        ),
    )

    answer = service.answer(CurrentInfoRequest(query="aktueller Python Release heute", locale="de", max_results=3))

    assert answer.status == "answered"
    assert [call.query for call in search_provider.calls] == [
        "aktueller Python Release heute",
        "aktueller Python Release heute official source latest verification",
    ]
    assert answer.query_plan is not None
    assert answer.query_plan.research_plan is not None
    assert answer.query_plan.research_plan.query_variants == tuple(call.query for call in search_provider.calls)
    assert answer.evidence is not None
    assert answer.evidence.sources[0].source_role == "official_source_candidate"


def test_current_info_service_answers_spacex_not_public_with_two_verified_sources():
    company = SearchResult(
        title="SpaceX Company Profile",
        url="https://www.spacex.com/company",
        snippet="",
        provider="fake_search",
        rank=1,
        host="www.spacex.com",
        metadata={"source_type": "Official"},
    )
    sec = SearchResult(
        title="SEC Company Tickers",
        url="https://www.sec.gov/files/company_tickers.json",
        snippet="",
        provider="fake_search",
        rank=2,
        host="www.sec.gov",
        metadata={"source_type": "Official"},
    )
    documents = {
        company.url: FetchedDocument(
            url=company.url,
            title=company.title,
            text="SpaceX company information describes SpaceX as a privately held company.",
            provider="fake_fetch",
        ),
        sec.url: FetchedDocument(
            url=sec.url,
            title=sec.title,
            text="SEC company ticker data has no SpaceX public ticker entry.",
            provider="fake_fetch",
        ),
    }
    chunks = (
        EvidenceChunk(
            text="SpaceX company information describes SpaceX as a privately held company.",
            source_url=company.url,
            source_title=company.title,
            relevance=0.95,
            metadata={"claim_key": "spacex_public_listing", "claim_value": "not_publicly_listed"},
        ),
        EvidenceChunk(
            text="SEC company ticker data has no SpaceX public ticker entry.",
            source_url=sec.url,
            source_title=sec.title,
            relevance=0.9,
            metadata={"claim_key": "spacex_public_listing", "claim_value": "not_publicly_listed"},
        ),
    )
    search_provider = _FakeSearchProvider((company, sec))
    service = CurrentInfoService(
        search_provider=search_provider,
        fetch_provider=_FakeFetchProvider(documents),
        retrieval_provider=_FakeRetrievalProvider(chunks),
    )

    query = "Ist SpaceX an der Börse?"
    answer = service.answer(
        CurrentInfoRequest(
            query=query,
            locale="de",
            domain_hint=classify_evidence_domain(query),
            max_results=3,
        )
    )

    assert answer.status == "answered"
    assert answer.confidence == 0.9
    assert answer.sources == (company.url, sec.url)
    assert "finance_listing_requires_verified_sources" not in answer.warnings
    assert "privately held company" in answer.answer_text
    assert "no SpaceX public ticker entry" in answer.answer_text
    assert [call.query for call in search_provider.calls] == [
        query,
        f"{query} public listing ticker exchange derivative sources",
    ]


def test_current_info_service_answers_siemens_listing_from_wkn_and_ticker_without_price():
    boerse = SearchResult(
        title="Siemens-Aktie - WKN 723610, Ticker SIE",
        url="https://www.boerse.example/aktien/Siemens-Aktie/DE0007236101",
        provider="fake_search",
        rank=1,
        host="www.boerse.example",
    )
    investor_relations = SearchResult(
        title="Siemens AG Investor Relations - Stocks/Bonds/Rating",
        url="https://www.siemens.com/global/en/company/investor-relations.html",
        provider="fake_search",
        rank=2,
        host="www.siemens.com",
        metadata={"source_type": "Official"},
    )
    documents = {
        boerse.url: FetchedDocument(
            url=boerse.url,
            title=boerse.title,
            text="Die Seite nennt die Siemens-Aktie mit WKN 723610 und Ticker SIE; aktuelle Kurse stehen nicht im Fragment.",
        ),
        investor_relations.url: FetchedDocument(
            url=investor_relations.url,
            title=investor_relations.title,
            text="Siemens AG Investor Relations bietet Bereiche zu Stocks/Bonds/Rating und Finanzberichten.",
        ),
    }
    chunks = (
        EvidenceChunk(
            text=documents[boerse.url].text,
            source_url=boerse.url,
            source_title=boerse.title,
            metadata={"claim_key": "siemens_ag_listing", "claim_value": "listed"},
        ),
        EvidenceChunk(
            text=documents[investor_relations.url].text,
            source_url=investor_relations.url,
            source_title=investor_relations.title,
            metadata={"claim_key": "siemens_ag_listing", "claim_value": "listed"},
        ),
    )
    service = CurrentInfoService(
        search_provider=_FakeSearchProvider((boerse, investor_relations)),
        fetch_provider=_FakeFetchProvider(documents),
        retrieval_provider=_FakeRetrievalProvider(chunks),
    )

    query = "Ist Siemens an der Börse?"
    answer = service.answer(CurrentInfoRequest(query=query, locale="de", domain_hint="stock", max_results=3))

    assert answer.status == "answered"
    assert "finance_listing_requires_verified_sources" not in answer.warnings
    assert answer.sources == (boerse.url, investor_relations.url)
    assert answer.answer_text.startswith("Ja,")
    assert "Siemens" in answer.answer_text
    assert "WKN 723610" in answer.answer_text
    assert "Ticker SIE" in answer.answer_text
    assert "aktuelle Kurse" not in answer.answer_text


def test_current_info_service_answers_finance_research_with_mixed_strong_and_weak_sources():
    marketscreener = SearchResult(
        title="Siemens AG Bewertung",
        url="https://de.marketscreener.example/kurs/aktie/SIEMENS-AG-56358595/bewertung",
        snippet="Bewertung, Kennzahlen und Analystenschätzungen zur Siemens AG.",
        provider="fake_search",
        rank=3,
        host="de.marketscreener.example",
    )
    siemens_report = SearchResult(
        title="Siemens Bericht",
        url="https://www.siemens.com/siemensbericht",
        snippet="Offizieller Geschäftsbericht der Siemens AG.",
        provider="fake_search",
        rank=4,
        host="www.siemens.com",
        metadata={"source_type": "Official"},
    )
    kununu = SearchResult(
        title="Siemens als Arbeitgeber",
        url="https://www.kununu.example/de/siemens",
        snippet="Arbeitgeberbewertungen zu Siemens.",
        provider="fake_search",
        rank=1,
        host="www.kununu.example",
    )
    statista = SearchResult(
        title="Statistiken zur Siemens AG",
        url="https://de.statista.example/themen/229/siemens-ag",
        snippet="Statistikportal mit Dossier zur Siemens AG.",
        provider="fake_search",
        rank=2,
        host="de.statista.example",
    )
    documents = {
        marketscreener.url: FetchedDocument(
            url=marketscreener.url,
            title=marketscreener.title,
            text="Siemens AG Bewertung: KGV, Marktkapitalisierung und Analystenschätzungen werden ausgewiesen.",
        ),
        siemens_report.url: FetchedDocument(
            url=siemens_report.url,
            title=siemens_report.title,
            text="Der Siemens Geschäftsbericht enthält Umsatz, Ergebnis, Ausblick und Segmentinformationen.",
        ),
        kununu.url: FetchedDocument(
            url=kununu.url,
            title=kununu.title,
            text="Kununu sammelt Arbeitgeberbewertungen zu Siemens.",
        ),
        statista.url: FetchedDocument(
            url=statista.url,
            title=statista.title,
            text="Statista bietet allgemeine Dossier-Statistiken zur Siemens AG.",
        ),
    }
    chunks = (
        EvidenceChunk(
            text=documents[marketscreener.url].text,
            source_url=marketscreener.url,
            source_title=marketscreener.title,
            relevance=0.95,
            metadata={"claim_key": "siemens_finance_research", "claim_value": "sufficient_context"},
        ),
        EvidenceChunk(
            text=documents[siemens_report.url].text,
            source_url=siemens_report.url,
            source_title=siemens_report.title,
            relevance=0.9,
            metadata={"claim_key": "siemens_finance_research", "claim_value": "sufficient_context"},
        ),
        EvidenceChunk(
            text=documents[kununu.url].text,
            source_url=kununu.url,
            source_title=kununu.title,
            relevance=0.4,
            metadata={"warning_codes": ("weak_source",)},
        ),
        EvidenceChunk(
            text=documents[statista.url].text,
            source_url=statista.url,
            source_title=statista.title,
            relevance=0.4,
            metadata={"warning_codes": ("weak_source",)},
        ),
    )
    service = CurrentInfoService(
        search_provider=_FakeSearchProvider((kununu, statista, marketscreener, siemens_report)),
        fetch_provider=_FakeFetchProvider(documents),
        retrieval_provider=_FakeRetrievalProvider(chunks),
    )

    query = "Bewertung Siemens Aktie"
    answer = service.answer(CurrentInfoRequest(query=query, locale="de", domain_hint="stock", max_results=5))

    assert answer.status == "answered"
    assert "weak_source" not in answer.warnings
    assert "irrelevant_source" not in answer.warnings
    assert answer.search_bundle is not None
    assert {result.host for result in answer.search_bundle.results[:2]} == {
        "de.marketscreener.example",
        "siemens.com",
    }
    assert set(answer.sources[:2]) == {marketscreener.url, siemens_report.url}
    assert "KGV" in answer.answer_text
    assert "Geschäftsbericht" in answer.answer_text


def test_current_info_service_fails_closed_for_finance_research_with_only_weak_context_sources():
    kununu = SearchResult(
        title="Siemens als Arbeitgeber",
        url="https://www.kununu.example/de/siemens",
        snippet="Arbeitgeberbewertungen zu Siemens.",
        provider="fake_search",
        rank=1,
        host="www.kununu.example",
    )
    statista = SearchResult(
        title="Statistiken zur Siemens AG",
        url="https://de.statista.example/themen/229/siemens-ag",
        snippet="Statistikportal mit Dossier zur Siemens AG.",
        provider="fake_search",
        rank=2,
        host="de.statista.example",
    )
    documents = {
        kununu.url: FetchedDocument(url=kununu.url, title=kununu.title, text="Kununu sammelt Arbeitgeberbewertungen."),
        statista.url: FetchedDocument(url=statista.url, title=statista.title, text="Statista sammelt allgemeine Dossiers."),
    }
    chunks = (
        EvidenceChunk(
            text=documents[kununu.url].text,
            source_url=kununu.url,
            source_title=kununu.title,
            metadata={"warning_codes": ("weak_source",)},
        ),
        EvidenceChunk(
            text=documents[statista.url].text,
            source_url=statista.url,
            source_title=statista.title,
            metadata={"warning_codes": ("weak_source",)},
        ),
    )
    service = CurrentInfoService(
        search_provider=_FakeSearchProvider((kununu, statista)),
        fetch_provider=_FakeFetchProvider(documents),
        retrieval_provider=_FakeRetrievalProvider(chunks),
    )

    answer = service.answer(CurrentInfoRequest(query="Bewertung Siemens Aktie", locale="de", domain_hint="stock"))

    assert answer.status == "unverified_evidence"
    assert answer.answer_text == ""
    assert "weak_source" in answer.warnings
    assert answer.metadata["reason"] == "weak_source"


def test_current_info_service_does_not_use_siemens_energy_as_siemens_ag_listing_proof():
    boerse = SearchResult(
        title="Siemens Energy AG Aktie",
        url="https://www.boerse.example/aktien/Siemens-Energy-Aktie/DE000ENER6Y0",
        provider="fake_search",
        rank=1,
        host="www.boerse.example",
    )
    exchange = SearchResult(
        title="Siemens Energy AG stock listing",
        url="https://live.deutsche-boerse.example/instrument/siemens-energy-ag",
        provider="fake_search",
        rank=2,
        host="live.deutsche-boerse.example",
    )
    documents = {
        boerse.url: FetchedDocument(
            url=boerse.url,
            title=boerse.title,
            text="Siemens Energy AG Aktie mit WKN ENER6Y und Ticker ENR.",
        ),
        exchange.url: FetchedDocument(
            url=exchange.url,
            title=exchange.title,
            text="Siemens Energy AG is listed; ticker ENR.",
        ),
    }
    chunks = (
        EvidenceChunk(
            text=documents[boerse.url].text,
            source_url=boerse.url,
            source_title=boerse.title,
            metadata={"claim_key": "siemens_energy_listing", "claim_value": "listed"},
        ),
        EvidenceChunk(
            text=documents[exchange.url].text,
            source_url=exchange.url,
            source_title=exchange.title,
            metadata={"claim_key": "siemens_energy_listing", "claim_value": "listed"},
        ),
    )
    service = CurrentInfoService(
        search_provider=_FakeSearchProvider((boerse, exchange)),
        fetch_provider=_FakeFetchProvider(documents),
        retrieval_provider=_FakeRetrievalProvider(chunks),
    )

    answer = service.answer(CurrentInfoRequest(query="Ist Siemens an der Börse?", locale="de", domain_hint="stock"))

    assert answer.status == "unverified_evidence"
    assert answer.answer_text == ""
    assert "irrelevant_source" in answer.warnings
    assert answer.metadata["reason"] == "irrelevant_source"


def test_current_info_service_routes_derivative_exchange_queries_as_crypto_listing_evidence():
    result = SearchResult(
        title="ACMEUSDT Contract | Bybit",
        url="https://www.bybit.com/en/trade/usdt/ACMEUSDT",
        snippet="Trade ACMEUSDT on Bybit.",
        provider="fake_search",
        rank=1,
        host="www.bybit.com",
    )
    document = FetchedDocument(
        url=result.url,
        title=result.title,
        text="Bybit lists ACMEUSDT as a tokenized pre-market perpetual contract.",
        provider="fake_fetch",
    )
    chunk = EvidenceChunk(
        text="Bybit lists ACMEUSDT as a tokenized pre-market perpetual contract.",
        source_url=result.url,
        source_title=result.title,
        relevance=0.9,
    )
    search_provider = _FakeSearchProvider((result,))
    service = CurrentInfoService(
        search_provider=search_provider,
        fetch_provider=_FakeFetchProvider({result.url: document}),
        retrieval_provider=_FakeRetrievalProvider((chunk,)),
    )

    query = "Was ist ACMEUSDT auf Bybit?"
    answer = service.answer(
        CurrentInfoRequest(
            query=query,
            locale="de",
            domain_hint=classify_evidence_domain(query),
            max_results=3,
        )
    )

    assert answer.task is not None
    assert answer.task.domain == "crypto"
    assert answer.status == "unverified_evidence"
    assert answer.answer_text == ""
    assert "needs_independent_source" in answer.warnings
    assert "finance_listing_requires_verified_sources" in answer.warnings
    assert answer.metadata["reason"] == "needs_independent_source"
    assert [call.query for call in search_provider.calls] == [
        query,
        f"{query} public listing ticker exchange derivative sources",
    ]


def test_current_info_service_searches_broad_crypto_quotes_without_known_symbol_profile():
    result = SearchResult(
        title="Solana price live",
        url="https://crypto.example/solana",
        snippet="Solana live market data.",
        provider="fake_search",
        rank=1,
        host="crypto.example",
    )
    document = FetchedDocument(
        url=result.url,
        title=result.title,
        text="Solana live market data is available from this checked source.",
        provider="fake_fetch",
    )
    chunk = EvidenceChunk(
        text="Solana live market data is available from this checked source.",
        source_url=result.url,
        source_title=result.title,
        relevance=0.9,
    )
    search_provider = _FakeSearchProvider((result,))
    service = CurrentInfoService(
        search_provider=search_provider,
        fetch_provider=_FakeFetchProvider({result.url: document}),
        retrieval_provider=_FakeRetrievalProvider((chunk,)),
    )

    query = "Was macht Solana?"
    answer = service.answer(
        CurrentInfoRequest(
            query=query,
            locale="de",
            domain_hint=classify_evidence_domain(query),
            max_results=3,
        )
    )

    assert answer.task is not None
    assert answer.task.domain == "crypto"
    assert answer.status == "answered"
    assert answer.sources == (result.url,)
    assert [call.query for call in search_provider.calls] == [query]


def test_current_info_service_keeps_unknown_crypto_snippets_unverified_after_search():
    result = SearchResult(
        title="BlorpCoin token price",
        url="https://search.example/blorpcoin",
        snippet="Search snippet claims BlorpCoin is trading at 42 USD.",
        provider="fake_search",
        rank=1,
        host="search.example",
    )
    search_provider = _FakeSearchProvider((result,))
    service = CurrentInfoService(search_provider=search_provider)

    query = "BlorpCoin token price now"
    answer = service.answer(
        CurrentInfoRequest(
            query=query,
            locale="en",
            domain_hint=classify_evidence_domain(query),
            max_results=3,
        )
    )

    assert answer.task is not None
    assert answer.task.domain == "crypto"
    assert answer.status == "unverified_evidence"
    assert answer.answer_text == ""
    assert answer.evidence is not None
    assert answer.evidence.freshness == "snippet_only"
    assert "snippet_only_evidence" in answer.warnings
    assert answer.metadata["reason"] == "current_facts_need_fetched_sources"
    assert [call.query for call in search_provider.calls] == [query]


def test_current_info_service_does_not_treat_plain_stock_price_as_listing_query():
    result = SearchResult(
        title="NVDA Stock Quote",
        url="https://finance.example/nvda",
        snippet="NVDA stock price.",
        provider="fake_search",
        rank=1,
        host="finance.example",
    )
    search_provider = _FakeSearchProvider((result,))
    service = CurrentInfoService(
        search_provider=search_provider,
        fetch_provider=_FakeFetchProvider(
            {result.url: FetchedDocument(url=result.url, title=result.title, text="NVDA stock price is 1.23.")}
        ),
        retrieval_provider=_FakeRetrievalProvider(
            (EvidenceChunk(text="NVDA stock price is 1.23.", source_url=result.url, source_title=result.title),)
        ),
    )

    answer = service.answer(CurrentInfoRequest(query="NVDA stock price now", locale="en", domain_hint="stock"))

    assert answer.status == "answered"
    assert [call.query for call in search_provider.calls] == ["NVDA stock price now"]


def test_current_info_service_blocks_single_source_finance_listing_claims_with_warning():
    result = SearchResult(
        title="SPCXUSDT Contract | Bybit",
        url="https://www.bybit.com/en/trade/usdt/SPCXUSDT",
        snippet="Trade SPCXUSDT on Bybit.",
        provider="fake_search",
        rank=1,
        host="www.bybit.com",
    )
    document = FetchedDocument(
        url=result.url,
        title=result.title,
        text="Bybit lists SPCXUSDT as a pre-market perpetual contract.",
        provider="fake_fetch",
    )
    chunk = EvidenceChunk(
        text="Bybit lists SPCXUSDT as a pre-market perpetual contract.",
        source_url=result.url,
        source_title=result.title,
        relevance=0.9,
    )
    search_provider = _FakeSearchProvider((result,))
    service = CurrentInfoService(
        search_provider=search_provider,
        fetch_provider=_FakeFetchProvider({result.url: document}),
        retrieval_provider=_FakeRetrievalProvider((chunk,)),
    )

    answer = service.answer(
        CurrentInfoRequest(
            query="Ist SpaceX börsennotiert oder ist SPCXUSDT auf Bybit ein Derivat?",
            locale="de",
            domain_hint="stock",
            max_results=3,
        )
    )

    assert answer.status == "unverified_evidence"
    assert answer.answer_text == ""
    assert answer.confidence == 0.58
    assert answer.sources == (result.url,)
    assert "needs_independent_source" in answer.warnings
    assert "finance_listing_requires_verified_sources" in answer.warnings
    assert answer.metadata["reason"] == "needs_independent_source"
    assert search_provider.calls == [
        _SearchCall(
            query="Ist SpaceX börsennotiert oder ist SPCXUSDT auf Bybit ein Derivat?",
            locale="de",
            max_results=3,
        ),
        _SearchCall(
            query=(
                "Ist SpaceX börsennotiert oder ist SPCXUSDT auf Bybit ein Derivat? "
                "public listing ticker exchange derivative sources"
            ),
            locale="de",
            max_results=3,
        ),
    ]


def test_current_info_service_still_blocks_weak_finance_listing_source():
    result = SearchResult(
        title="Anthropic rumors",
        url="https://forum.example/anthropic-stock",
        snippet="Someone says Anthropic has a ticker.",
        provider="fake_search",
        rank=1,
        host="forum.example",
        metadata={"source_type": "Forum"},
    )
    document = FetchedDocument(
        url=result.url,
        title=result.title,
        text="A forum post speculates about an Anthropic ticker.",
        provider="fake_fetch",
    )
    chunk = EvidenceChunk(
        text="A forum post speculates about an Anthropic ticker.",
        source_url=result.url,
        source_title=result.title,
        relevance=0.9,
        metadata={"warning_codes": ("weak_source",)},
    )
    service = CurrentInfoService(
        search_provider=_FakeSearchProvider((result,)),
        fetch_provider=_FakeFetchProvider({result.url: document}),
        retrieval_provider=_FakeRetrievalProvider((chunk,)),
    )

    answer = service.answer(CurrentInfoRequest(query="Ist Anthropic an der Börse?", locale="de", domain_hint="stock"))

    assert answer.status == "unverified_evidence"
    assert answer.answer_text == ""
    assert "weak_source" in answer.warnings


def test_current_info_service_blocks_conflicting_finance_listing_sources():
    first = SearchResult(title="Exchange profile", url="https://exchange.example/acmeblubbla", provider="fake", rank=1)
    second = SearchResult(title="Filings profile", url="https://filings.example/acmeblubbla", provider="fake", rank=2)
    chunks = (
        EvidenceChunk(
            text="AcmeBlubBla is publicly listed with ticker ACBL.",
            source_url=first.url,
            metadata={"claim_key": "acmeblubbla_listing", "claim_value": "listed"},
        ),
        EvidenceChunk(
            text="AcmeBlubBla has no public listing or ticker.",
            source_url=second.url,
            metadata={"claim_key": "acmeblubbla_listing", "claim_value": "not_listed"},
        ),
    )
    service = CurrentInfoService(
        search_provider=_FakeSearchProvider((first, second)),
        fetch_provider=_FakeFetchProvider(
            {
                first.url: FetchedDocument(url=first.url, title=first.title, text=chunks[0].text),
                second.url: FetchedDocument(url=second.url, title=second.title, text=chunks[1].text),
            }
        ),
        retrieval_provider=_FakeRetrievalProvider(chunks),
    )

    answer = service.answer(
        CurrentInfoRequest(query="Ist AcmeBlubBla an der Börse?", locale="de", domain_hint="stock", max_results=2)
    )

    assert answer.status == "unverified_evidence"
    assert answer.answer_text == ""
    assert "source_conflict" in answer.warnings
    assert answer.metadata["reason"] == "source_conflict"


def test_current_info_service_blocks_stale_finance_listing_source():
    fetched_at = (datetime.now(UTC) - timedelta(days=10)).replace(microsecond=0).isoformat()
    result = SearchResult(title="FooBarBaz AG listing", url="https://exchange.example/foobarbaz", provider="fake", rank=1)
    document = FetchedDocument(
        url=result.url,
        title=result.title,
        text="FooBarBaz AG is publicly listed with ticker FBB.",
        fetched_at=fetched_at,
    )
    chunk = EvidenceChunk(
        text="FooBarBaz AG is publicly listed with ticker FBB.",
        source_url=result.url,
        source_title=result.title,
    )
    service = CurrentInfoService(
        search_provider=_FakeSearchProvider((result,)),
        fetch_provider=_FakeFetchProvider({result.url: document}),
        retrieval_provider=_FakeRetrievalProvider((chunk,)),
    )

    answer = service.answer(
        CurrentInfoRequest(query="Ist FooBarBaz AG an der Börse?", locale="de", domain_hint="stock", max_results=1)
    )

    assert answer.status == "unverified_evidence"
    assert answer.answer_text == ""
    assert "stale_source" in answer.warnings
    assert answer.metadata["reason"] == "stale_source"


def test_current_info_service_rewards_two_independent_news_sources_that_agree():
    first = SearchResult(title="News A", url="https://news-a.example/article", snippet="", provider="fake", rank=1)
    second = SearchResult(title="News B", url="https://news-b.example/article", snippet="", provider="fake", rank=2)
    documents = {
        first.url: FetchedDocument(url=first.url, title=first.title, text="Release version 2.0 is available."),
        second.url: FetchedDocument(url=second.url, title=second.title, text="Release version 2.0 is available."),
    }
    chunks = (
        EvidenceChunk(
            text="Release version 2.0 is available.",
            source_url=first.url,
            source_title=first.title,
            metadata={"claim_key": "release", "claim_value": "2.0"},
        ),
        EvidenceChunk(
            text="Release version 2.0 is available.",
            source_url=second.url,
            source_title=second.title,
            metadata={"claim_key": "release", "claim_value": "2.0"},
        ),
    )
    service = CurrentInfoService(
        search_provider=_FakeSearchProvider((first, second)),
        fetch_provider=_FakeFetchProvider(documents),
        retrieval_provider=_FakeRetrievalProvider(chunks),
    )

    answer = service.answer(CurrentInfoRequest(query="latest AMO news", domain_hint="news", max_results=2))

    assert answer.status == "answered"
    assert answer.confidence == 0.9
    assert answer.warnings == ()
    assert answer.evidence is not None
    assert {source.host for source in answer.evidence.sources if source.fetched} == {"news-a.example", "news-b.example"}


def test_current_info_service_warns_for_conflicting_sources_instead_of_overconfidence():
    first = SearchResult(title="Source A", url="https://a.example/status", provider="fake", rank=1)
    second = SearchResult(title="Source B", url="https://b.example/status", provider="fake", rank=2)
    chunks = (
        EvidenceChunk(
            text="The status is green.",
            source_url=first.url,
            metadata={"claim_key": "status", "claim_value": "green"},
        ),
        EvidenceChunk(
            text="The status is red.",
            source_url=second.url,
            metadata={"claim_key": "status", "claim_value": "red"},
        ),
    )
    service = CurrentInfoService(
        search_provider=_FakeSearchProvider((first, second)),
        fetch_provider=_FakeFetchProvider(
            {
                first.url: FetchedDocument(url=first.url, text="The status is green."),
                second.url: FetchedDocument(url=second.url, text="The status is red."),
            }
        ),
        retrieval_provider=_FakeRetrievalProvider(chunks),
    )

    answer = service.answer(CurrentInfoRequest(query="current status", max_results=2))

    assert answer.status == "answered"
    assert "source_conflict" in answer.warnings
    assert answer.confidence == 0.45


def test_current_info_service_fails_closed_for_stale_source():
    fetched_at = (datetime.now(UTC) - timedelta(days=10)).replace(microsecond=0).isoformat()
    result = SearchResult(title="Old status", url="https://example.com/old", provider="fake", rank=1)
    document = FetchedDocument(url=result.url, title=result.title, text="Old current status.", fetched_at=fetched_at)
    chunk = EvidenceChunk(text="Old current status.", source_url=result.url, source_title=result.title)
    service = CurrentInfoService(
        search_provider=_FakeSearchProvider((result,)),
        fetch_provider=_FakeFetchProvider({result.url: document}),
        retrieval_provider=_FakeRetrievalProvider((chunk,)),
    )

    answer = service.answer(CurrentInfoRequest(query="current status"))

    assert answer.status == "unverified_evidence"
    assert answer.answer_text == ""
    assert answer.confidence == 0.55
    assert answer.warnings == ("stale_source",)
    assert answer.evidence is not None
    assert answer.evidence.freshness == "stale"
    assert answer.evidence.sources[0].stale is True


def test_current_info_service_fails_closed_for_irrelevant_checked_source():
    result = SearchResult(title="Unrelated", url="https://example.com/unrelated", provider="fake", rank=1)
    document = FetchedDocument(url=result.url, title=result.title, text="This page is about an unrelated project.")
    chunk = EvidenceChunk(
        text="This page is about an unrelated project.",
        source_url=result.url,
        source_title=result.title,
        metadata={"warning_codes": ("irrelevant_source",)},
    )
    service = CurrentInfoService(
        search_provider=_FakeSearchProvider((result,)),
        fetch_provider=_FakeFetchProvider({result.url: document}),
        retrieval_provider=_FakeRetrievalProvider((chunk,)),
    )

    answer = service.answer(CurrentInfoRequest(query="current status"))

    assert answer.status == "unverified_evidence"
    assert answer.answer_text == ""
    assert answer.evidence is not None
    assert answer.warnings == ("irrelevant_source",)
    assert answer.metadata["reason"] == "irrelevant_source"


def test_current_info_service_normalizes_ranks_and_dedupes_search_candidates():
    tracked = SearchResult(
        title="Tracked",
        url="https://Example.com/news/?utm_source=test&id=1",
        snippet="Tracked result",
        provider="searxng",
        rank=1,
    )
    duplicate = SearchResult(
        title="Duplicate",
        url="https://example.com/news?id=1&fbclid=abc",
        snippet="Duplicate result",
        provider="brave",
        rank=1,
    )
    official = SearchResult(
        title="Official",
        url="https://example.gov/status",
        snippet="Official result",
        provider="brave",
        rank=2,
    )
    search_provider = _FakeSearchProvider((tracked, duplicate, official))
    service = CurrentInfoService(search_provider=search_provider)

    answer = service.answer(CurrentInfoRequest(query="current info", max_results=3))

    assert answer.status == "unverified_evidence"
    assert answer.search_bundle is not None
    assert [result.url for result in answer.search_bundle.results] == [
        "https://example.com/news?id=1",
        "https://example.gov/status",
    ]
    assert answer.search_bundle.results[0].metadata["source_type"] == "News"
    assert answer.search_bundle.results[1].metadata["source_type"] == "Official"
    assert answer.sources == ("https://example.com/news?id=1", "https://example.gov/status")
    assert answer.warnings == ("snippet_only_evidence", "needs_independent_source")


def test_current_info_service_uses_source_preference_repository_for_ranking():
    trusted = SearchResult(
        title="Trusted",
        url="https://www.trusted.example/status",
        snippet="Trusted result",
        provider="fake",
        rank=3,
    )
    rejected = SearchResult(
        title="Rejected",
        url="https://bad.example/status",
        snippet="Rejected result",
        provider="fake",
        rank=1,
    )
    preferences = _PreferenceRepo()
    service = CurrentInfoService(
        search_provider=_FakeSearchProvider((rejected, trusted)),
        source_preference_repository=preferences,
    )

    answer = service.answer(
        CurrentInfoRequest(
            query="current info",
            domain_hint="news",
            chat_id=-100,
            topic_id=7,
            user_id=42,
            max_results=2,
        )
    )

    assert answer.search_bundle is not None
    assert [result.host for result in answer.search_bundle.results] == ["trusted.example", "bad.example"]
    assert preferences.calls == [
        {
            "source_hosts": ("bad.example", "trusted.example"),
            "domain": "news",
            "chat_id": -100,
            "topic_id": 7,
            "user_id": 42,
        }
    ]
    assert answer.search_bundle.results[0].metadata["source_preference_signal"] == "trusted"
    assert answer.search_bundle.results[1].metadata["source_preference_signal"] == "rejected"


def test_current_info_service_prefers_authoritative_api_docs_for_docs_queries():
    third_party = SearchResult(
        title="Acme API changelog summary",
        url="https://blog.example/acme-api-changelog",
        snippet="A third-party summary of Acme API updates.",
        provider="fake",
        rank=1,
        host="blog.example",
    )
    official = SearchResult(
        title="Acme API changelog",
        url="https://docs.acme.example/api/changelog",
        snippet="Official Acme API changelog and reference.",
        provider="fake",
        rank=4,
        host="docs.acme.example",
    )
    service = CurrentInfoService(
        search_provider=_FakeSearchProvider((third_party, official)),
        fetch_provider=_FakeFetchProvider(
            {
                official.url: FetchedDocument(
                    url=official.url,
                    title=official.title,
                    text="Acme API changelog: webhook retries changed in version 2026-06.",
                ),
                third_party.url: FetchedDocument(
                    url=third_party.url,
                    title=third_party.title,
                    text="Blog post repeating an older Acme API changelog summary.",
                ),
            }
        ),
    )

    answer = service.answer(
        CurrentInfoRequest(query="Acme API changelog webhook retries", locale="en", max_results=2, max_documents=1)
    )

    assert answer.status == "answered"
    assert answer.search_bundle is not None
    assert [result.host for result in answer.search_bundle.results] == ["docs.acme.example", "blog.example"]
    assert answer.search_bundle.results[0].metadata["source_preference_source"] == "inferred_authoritative_primary"
    assert answer.sources == (official.url,)


def test_current_info_service_prefers_query_matching_docs_over_unrelated_docs_with_tight_fetch_budget():
    unrelated_docs = SearchResult(
        title="Other Platform API changelog",
        url="https://docs.other.example/api/changelog",
        snippet="Official docs for a different product.",
        provider="fake",
        rank=1,
        host="docs.other.example",
    )
    primary_docs = SearchResult(
        title="ExampleProduct API webhook retries changelog",
        url="https://docs.exampleproduct.example/api/webhooks/changelog",
        snippet="Official ExampleProduct API changelog for webhook retries.",
        provider="fake",
        rank=2,
        host="docs.exampleproduct.example",
    )
    fetch_provider = _FakeFetchProvider(
        {
            unrelated_docs.url: FetchedDocument(
                url=unrelated_docs.url,
                title=unrelated_docs.title,
                text="Other Platform API changelog describes authentication pagination only.",
            ),
            primary_docs.url: FetchedDocument(
                url=primary_docs.url,
                title=primary_docs.title,
                text="ExampleProduct API changelog: webhook retries now use exponential backoff.",
            ),
        }
    )
    service = CurrentInfoService(
        search_provider=_FakeSearchProvider((unrelated_docs, primary_docs)),
        fetch_provider=fetch_provider,
    )

    answer = service.answer(
        CurrentInfoRequest(
            query="ExampleProduct API webhook retries changelog",
            locale="en",
            max_results=2,
            max_documents=1,
        )
    )

    assert answer.status == "answered"
    assert answer.search_bundle is not None
    assert [result.host for result in answer.search_bundle.results] == [
        "docs.exampleproduct.example",
        "docs.other.example",
    ]
    assert answer.search_bundle.results[0].metadata["source_preference_source"] == "inferred_authoritative_primary"
    assert "source_preference_source" not in answer.search_bundle.results[1].metadata
    assert fetch_provider.calls == [(primary_docs.url, "en")]
    assert answer.sources == (primary_docs.url,)
    assert "irrelevant_source" not in answer.warnings


def test_current_info_service_keeps_fresh_authoritative_docs_ahead_of_unfetched_cached_chunks():
    stale_cached = SearchResult(
        title="Old ad-hoc Acme API summary",
        url="https://cache.example/acme-api-webhooks",
        snippet="Old third-party Acme API notes.",
        provider="fake",
        rank=1,
        host="cache.example",
    )
    official = SearchResult(
        title="Acme API webhook reference",
        url="https://docs.acme.example/api/webhooks",
        snippet="Official Acme API documentation.",
        provider="fake",
        rank=2,
        host="docs.acme.example",
    )
    official_doc = FetchedDocument(
        url=official.url,
        title=official.title,
        text="Acme API webhook reference documents retry limits and delivery status fields.",
        fetched_at="2026-06-25T10:00:00+00:00",
    )
    stale_chunk = EvidenceChunk(
        text="Old cached third-party notes claim webhook retry limits are unknown.",
        source_url=stale_cached.url,
        source_title=stale_cached.title,
        relevance=0.99,
        metadata={"fetched_at": "2025-01-01T00:00:00+00:00", "source_type": "Unknown"},
    )
    service = CurrentInfoService(
        search_provider=_FakeSearchProvider((stale_cached, official)),
        fetch_provider=_FakeFetchProvider({official.url: official_doc}),
        retrieval_provider=_FakeRetrievalProvider((stale_chunk,)),
    )

    answer = service.answer(
        CurrentInfoRequest(
            query="Acme API webhook retry limits",
            locale="en",
            max_results=2,
            max_documents=1,
            metadata={"now": "2026-06-25T12:00:00+00:00"},
        )
    )

    assert answer.status == "answered"
    assert answer.sources[0] == official.url
    assert stale_cached.url in answer.sources
    assert answer.evidence is not None
    assert answer.evidence.chunks[0].source_url == official.url
    assert "unfetched_chunk_evidence" in answer.warnings


def test_current_info_service_regression_prefers_fresh_telegram_docs_without_telegram_special_case():
    stale_cached = SearchResult(
        title="Old ad-hoc Telegram summary",
        url="https://ad-hoc-news.example/telegram-bot-api",
        snippet="Old third-party Bot API notes.",
        provider="fake",
        rank=1,
        host="ad-hoc-news.example",
    )
    official = SearchResult(
        title="Telegram Bot API",
        url="https://core.telegram.org/bots/api",
        snippet="Official Telegram Bot API documentation.",
        provider="fake",
        rank=2,
        host="core.telegram.org",
    )
    official_doc = FetchedDocument(
        url=official.url,
        title=official.title,
        text="Telegram Bot API documentation describes paid media methods for bots.",
        fetched_at="2026-06-25T10:00:00+00:00",
    )
    stale_chunk = EvidenceChunk(
        text="Old cached third-party notes claim paid media details are unknown.",
        source_url=stale_cached.url,
        source_title=stale_cached.title,
        relevance=0.99,
        metadata={"fetched_at": "2025-01-01T00:00:00+00:00", "source_type": "Unknown"},
    )
    service = CurrentInfoService(
        search_provider=_FakeSearchProvider((stale_cached, official)),
        fetch_provider=_FakeFetchProvider({official.url: official_doc}),
        retrieval_provider=_FakeRetrievalProvider((stale_chunk,)),
    )

    answer = service.answer(
        CurrentInfoRequest(
            query="Telegram Bot API paid media",
            locale="en",
            max_results=2,
            max_documents=1,
            metadata={"now": "2026-06-25T12:00:00+00:00"},
        )
    )

    assert answer.status == "answered"
    assert answer.sources[0] == official.url
    assert answer.evidence is not None
    assert answer.evidence.chunks[0].source_url == official.url
    assert answer.search_bundle is not None
    assert answer.search_bundle.results[0].metadata["source_preference_source"] == "inferred_authoritative_primary"


def test_current_info_service_fails_closed_for_irrelevant_authoritative_doc():
    official = SearchResult(
        title="Acme API overview",
        url="https://docs.acme.example/api/overview",
        snippet="Official Acme API documentation.",
        provider="fake",
        rank=1,
        host="docs.acme.example",
    )
    document = FetchedDocument(
        url=official.url,
        title=official.title,
        text="Acme API overview explains authentication and pagination basics.",
    )
    service = CurrentInfoService(
        search_provider=_FakeSearchProvider((official,)),
        fetch_provider=_FakeFetchProvider({official.url: document}),
    )

    answer = service.answer(CurrentInfoRequest(query="Acme API webhook retry limits", locale="en", max_results=1))

    assert answer.status == "unverified_evidence"
    assert answer.answer_text == ""
    assert "irrelevant_source" in answer.warnings
    assert answer.metadata["reason"] == "irrelevant_source"


def test_current_info_service_fails_closed_without_search_provider():
    service = CurrentInfoService()

    answer = service.answer(CurrentInfoRequest(query="current info"))

    assert answer.status == "provider_unavailable"
    assert answer.warnings == ("search_provider_not_configured",)
    assert answer.search_bundle is None


def test_current_info_service_gpt_researcher_required_uses_research_provider_only():
    search_provider = _FakeSearchProvider(
        (SearchResult(title="Search-only", url="https://search.example", snippet="Do not use"),)
    )
    fetch_provider = _FakeFetchProvider(
        {"https://search.example": FetchedDocument(url="https://search.example", text="Do not fetch")}
    )
    research_answer = CurrentInfoAnswer(
        status="answered",
        answer_text="GPT-Researcher answer",
        sources=("https://research.example/source",),
        metadata={"provider_mode": "gpt_researcher"},
    )
    research_provider = _FakeResearchProvider(research_answer)
    service = CurrentInfoService(
        search_provider=search_provider,
        fetch_provider=fetch_provider,
        research_provider=research_provider,
    )

    answer = service.answer(
        CurrentInfoRequest(
            query="latest OpenAI API version",
            metadata={"require_gpt_researcher": True, "capability": "webresearch"},
        )
    )

    assert answer.answer_text == "GPT-Researcher answer"
    assert len(research_provider.calls) == 1
    assert search_provider.calls == []
    assert fetch_provider.calls == []


def test_current_info_service_gpt_researcher_required_fails_closed_without_provider():
    search_provider = _FakeSearchProvider(
        (SearchResult(title="Search-only", url="https://search.example", snippet="Do not use"),)
    )
    service = CurrentInfoService(search_provider=search_provider)

    answer = service.answer(
        CurrentInfoRequest(
            query="current Stripe API changes",
            metadata={"require_gpt_researcher": True, "capability": "webresearch"},
        )
    )

    assert answer.status == "provider_unavailable"
    assert answer.warnings == ("gpt_researcher_not_configured",)
    assert answer.search_bundle is None
    assert answer.metadata["provider_mode"] == "gpt_researcher"
    assert search_provider.calls == []


def test_current_info_service_gpt_researcher_required_does_not_fallback_after_research_failure():
    search_provider = _FakeSearchProvider(
        (SearchResult(title="Search-only", url="https://search.example", snippet="Do not use"),)
    )
    research_provider = _FakeResearchProvider(
        CurrentInfoAnswer(
            status="provider_unavailable",
            warnings=("gpt_researcher_timeout",),
            metadata={"provider_mode": "gpt_researcher"},
        )
    )
    service = CurrentInfoService(search_provider=search_provider, research_provider=research_provider)

    answer = service.answer(
        CurrentInfoRequest(
            query="latest Tesla news",
            metadata={"require_gpt_researcher": True, "capability": "webresearch"},
        )
    )

    assert answer.status == "provider_unavailable"
    assert answer.warnings == ("gpt_researcher_timeout",)
    assert len(research_provider.calls) == 1
    assert search_provider.calls == []


def test_current_info_service_reports_empty_result_from_fake_provider():
    service = CurrentInfoService(search_provider=_FakeSearchProvider(()))

    answer = service.answer(CurrentInfoRequest(query="current info"))

    assert answer.status == "empty_result"
    assert answer.search_bundle is not None
    assert answer.search_bundle.results == ()
    assert answer.warnings == ("empty_search_result",)


def test_current_info_service_fails_closed_when_search_provider_is_rate_limited():
    service = CurrentInfoService(search_provider=_RateLimitedSearchProvider())

    answer = service.answer(CurrentInfoRequest(query="current status"))

    assert answer.status == "provider_unavailable"
    assert answer.warnings == ("rate_limited",)
    assert answer.search_bundle is None


def test_current_info_observability_logs_privacy_safe_pipeline_events(caplog):
    sensitive_query = "latest private user query about Matze"
    result = SearchResult(
        title="Status page",
        url="https://example.com/status",
        snippet="Fallback snippet",
        provider="fake_search",
        rank=1,
    )
    document = FetchedDocument(url=result.url, title=result.title, text="Current status is green.")
    chunk = EvidenceChunk(text="Current status is green.", source_url=result.url, source_title=result.title)
    service = CurrentInfoService(
        search_provider=_FakeSearchProvider((result,)),
        fetch_provider=_FakeFetchProvider({result.url: document}),
        retrieval_provider=_FakeRetrievalProvider((chunk,)),
    )

    with caplog.at_level(logging.INFO, logger="amo_bot.current_info.service"):
        answer = service.answer(CurrentInfoRequest(query=sensitive_query, chat_id=-100123456, user_id=123456789))

    assert answer.status == "answered"
    rendered_logs = "\n".join(record.getMessage() for record in caplog.records)
    assert sensitive_query not in rendered_logs
    assert query_hash(sensitive_query) in rendered_logs
    payloads = [ast.literal_eval(record.getMessage()) for record in caplog.records]
    assert {
        "current_info.QueryRun",
        "current_info.ProviderRun",
        "current_info.FetchRun",
        "current_info.EvidenceDecision",
        "current_info.AnswerSynthesis",
    }.issubset({payload["event"] for payload in payloads})


def test_current_info_service_enforces_fetch_budget_and_exposes_operator_debug():
    first = SearchResult(title="A", url="https://a.example/status", provider="fake", rank=1, host="a.example")
    second = SearchResult(title="B", url="https://b.example/status", provider="fake", rank=2, host="b.example")
    service = CurrentInfoService(
        search_provider=_FakeSearchProvider((first, second)),
        fetch_provider=_FakeFetchProvider(
            {
                first.url: FetchedDocument(url=first.url, text="The status is green."),
                second.url: FetchedDocument(url=second.url, text="The status is green."),
            }
        ),
        safety_config=CurrentInfoSafetyConfig(max_fetch_runs_per_response=1, debug_enabled=True),
    )

    answer = service.answer(CurrentInfoRequest(query="current status", max_results=2, max_documents=2))

    assert answer.status == "answered"
    assert answer.metadata["debug"]["budgets"]["fetch_runs"] == 1
    assert "fetch_budget_exceeded" in answer.metadata["debug"]["budgets"]["warnings"]


def test_current_info_rate_limiter_blocks_after_configured_window_budget():
    limiter = InMemoryRateLimiter()

    assert limiter.allow("brave", limit=1, window_seconds=60.0) is True
    assert limiter.allow("brave", limit=1, window_seconds=60.0) is False
    assert limiter.allow("searxng", limit=1, window_seconds=60.0) is True


def test_current_info_host_concurrency_limiter_blocks_same_host_only():
    limiter = HostConcurrencyLimiter()

    with limiter.acquire("https://example.com/a", limit=1):
        try:
            with limiter.acquire("https://example.com/b", limit=1):
                raise AssertionError("same-host concurrency should be blocked")
        except CurrentInfoBudgetExceeded as exc:
            assert exc.reason_code == "host_concurrency_limit"

        with limiter.acquire("https://other.example/b", limit=1):
            pass


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
