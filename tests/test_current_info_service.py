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
        "Kann man SpaceX Aktien kaufen?",
        "Nasdaq Anthropic",
        "NYSE Anthropic",
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
        assert "finance_listing_requires_verified_sources" in answer.warnings
        assert [call.query for call in search_provider.calls] == [
            query,
            f"{query} official source listing derivative exchange",
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
    assert answer.sources == (url,)
    assert "finance_listing_requires_verified_sources" in answer.warnings
    assert fetch_provider.calls == [(url, "de")]
    assert [call.query for call in search_provider.calls] == [
        query,
        f"{query} official source listing derivative exchange",
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
    assert answer.sources == (url,)
    assert "stock_entity_not_identified" not in answer.warnings
    assert "finance_listing_requires_verified_sources" in answer.warnings
    assert fetch_provider.calls == [(url, "de")]


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
        f"{query} official source listing derivative exchange",
    ]


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
    assert "finance_listing_requires_verified_sources" in answer.warnings
    assert [call.query for call in search_provider.calls] == [
        query,
        f"{query} official source listing derivative exchange",
    ]


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


def test_current_info_service_requires_stronger_evidence_for_finance_listing_claims():
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
    assert search_provider.calls == [
        _SearchCall(
            query="Ist SpaceX börsennotiert oder ist SPCXUSDT auf Bybit ein Derivat?",
            locale="de",
            max_results=3,
        ),
        _SearchCall(
            query=(
                "Ist SpaceX börsennotiert oder ist SPCXUSDT auf Bybit ein Derivat? "
                "official source listing derivative exchange"
            ),
            locale="de",
            max_results=3,
        ),
    ]


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
