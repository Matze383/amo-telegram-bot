from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from amo_bot.current_info import (
    BraveSearchConfig,
    BraveSearchProvider,
    SOURCE_TYPE_COMMERCE,
    SOURCE_TYPE_DOCS,
    SOURCE_TYPE_FORUM,
    SOURCE_TYPE_NEWS,
    SOURCE_TYPE_OFFICIAL,
    SOURCE_TYPE_SOCIAL,
    SOURCE_TYPE_UNKNOWN,
    SearchBroker,
    SearchBrokerConfig,
    SearchProviderError,
    SearchProviderMetric,
    SearchProviderResponse,
    SearchProviderTimeout,
    SearchResult,
    SearxngSearchConfig,
    SearxngSearchProvider,
    canonicalize_url,
    classify_source_type,
    normalize_dedupe_and_rank_search_results,
)


@dataclass
class _ProviderCall:
    query: str
    locale: str
    max_results: int


class _FakeProvider:
    def __init__(
        self,
        name: str,
        response: SearchProviderResponse | None = None,
        error: SearchProviderError | None = None,
    ) -> None:
        self.name = name
        self.response = response or SearchProviderResponse()
        self.error = error
        self.calls: list[_ProviderCall] = []

    def search(self, *, query: str, locale: str, max_results: int) -> SearchProviderResponse:
        self.calls.append(_ProviderCall(query=query, locale=locale, max_results=max_results))
        if self.error is not None:
            raise self.error
        return self.response


class _FakeHttpClient:
    def __init__(self, factory: _FakeHttpClientFactory) -> None:
        self._factory = factory

    def __enter__(self) -> _FakeHttpClient:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    def get(self, url: str, *, params: dict[str, Any]) -> httpx.Response:
        self._factory.get_calls.append((url, params))
        if self._factory.error is not None:
            raise self._factory.error
        return self._factory.responses.pop(0)


class _FakeHttpClientFactory:
    def __init__(self, responses: list[httpx.Response], error: Exception | None = None) -> None:
        self.responses = responses
        self.error = error
        self.client_kwargs: list[dict[str, Any]] = []
        self.get_calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, **kwargs: Any) -> _FakeHttpClient:
        self.client_kwargs.append(kwargs)
        return _FakeHttpClient(self)


def _response(results: tuple[SearchResult, ...], *, provider: str = "searxng") -> SearchProviderResponse:
    return SearchProviderResponse(
        results=results,
        metrics=(
            SearchProviderMetric(
                provider=provider,
                hit_count=len(results),
                host_diversity=len({result.host for result in results if result.host}),
            ),
        ),
    )


def _result(url: str, *, provider: str = "searxng", rank: int = 1) -> SearchResult:
    host = httpx.URL(url).host or ""
    return SearchResult(title=f"Result {rank}", url=url, snippet="Snippet", provider=provider, rank=rank, host=host)


def test_searxng_success_parses_results_and_metrics_without_real_network() -> None:
    factory = _FakeHttpClientFactory(
        [
            httpx.Response(
                200,
                json={
                    "results": [
                        {"title": "One", "url": "https://one.example/news", "content": "Fresh one", "date": "2026-06-16"},
                        {"title": "Two", "url": "https://two.example/news", "content": "Fresh two"},
                    ]
                },
            )
        ]
    )
    provider = SearxngSearchProvider(
        SearxngSearchConfig(base_url="https://searx.example", timeout_seconds=1.5, max_results=5),
        http_client_factory=factory,
    )

    response = provider.search(query="latest status", locale="de", max_results=2)

    assert [result.title for result in response.results] == ["One", "Two"]
    assert response.results[0].host == "one.example"
    assert response.results[0].date == "2026-06-16"
    assert response.metrics[0].provider == "searxng"
    assert response.metrics[0].hit_count == 2
    assert response.metrics[0].host_diversity == 2
    assert factory.get_calls == [
        (
            "https://searx.example/search",
            {"q": "latest status", "format": "json", "language": "de-de"},
        )
    ]
    assert factory.client_kwargs[0]["timeout"] == 1.5


def test_canonicalize_url_removes_tracking_params_and_stabilizes_shape() -> None:
    assert (
        canonicalize_url("HTTPS://Example.COM:443/news/?utm_source=chat&b=2&a=1&fbclid=abc#section")
        == "https://example.com/news?a=1&b=2"
    )
    assert canonicalize_url("https://example.com/?utm_campaign=x") == "https://example.com/"


@pytest.mark.parametrize(
    ("result", "source_type"),
    [
        (_result("https://www.reuters.com/world/latest"), SOURCE_TYPE_NEWS),
        (_result("https://example.gov/alerts"), SOURCE_TYPE_OFFICIAL),
        (_result("https://docs.python.org/3/library/datetime.html"), SOURCE_TYPE_DOCS),
        (_result("https://x.com/example/status/1"), SOURCE_TYPE_SOCIAL),
        (_result("https://www.reddit.com/r/python/comments/1"), SOURCE_TYPE_FORUM),
        (_result("https://www.amazon.de/dp/example"), SOURCE_TYPE_COMMERCE),
        (_result("https://example.com/about"), SOURCE_TYPE_UNKNOWN),
    ],
)
def test_classify_source_type_uses_host_and_path(result: SearchResult, source_type: str) -> None:
    assert classify_source_type(result) == source_type


def test_normalize_dedupe_and_rank_search_results_across_providers() -> None:
    duplicate_low_value = _result(
        "https://Example.com/news?utm_source=searx&b=2&a=1",
        provider="searxng",
        rank=1,
    )
    duplicate_from_fallback = _result(
        "https://example.com/news/?a=1&b=2&fbclid=abc",
        provider="brave",
        rank=1,
    )
    official = _result("https://example.gov/status", provider="brave", rank=2)
    repeated_host = _result("https://example.com/other", provider="brave", rank=2)
    fresh_news = SearchResult(
        title="Fresh report",
        url="https://news.example/article",
        snippet="Latest news",
        provider="searxng",
        rank=3,
        host="news.example",
        date="2026-06-16",
    )

    results = normalize_dedupe_and_rank_search_results(
        (duplicate_low_value, duplicate_from_fallback, repeated_host, official, fresh_news),
        max_results=5,
    )

    assert [result.url for result in results] == [
        "https://example.com/news?a=1&b=2",
        "https://example.gov/status",
        "https://news.example/article",
        "https://example.com/other",
    ]
    assert results[0].metadata["source_type"] == SOURCE_TYPE_NEWS
    assert results[1].metadata["source_type"] == SOURCE_TYPE_OFFICIAL
    assert results[0].metadata["canonical_url"] == "https://example.com/news?a=1&b=2"


def test_normalize_rank_uses_source_observation_metadata_without_network() -> None:
    weak_observed = SearchResult(
        title="Weak observed",
        url="https://weak.example/news",
        snippet="Latest news",
        provider="searxng",
        rank=1,
        metadata={
            "source_observation_outcome": "confirmed",
            "source_observation_warning_codes": ("source_conflict",),
            "source_observation_warning_count": 1,
        },
    )
    confirmed_observed = SearchResult(
        title="Confirmed observed",
        url="https://confirmed.example/news",
        snippet="Latest news",
        provider="brave",
        rank=1,
        metadata={
            "source_observation_outcome": "confirmed",
            "source_observation_confidence": 0.9,
        },
    )

    results = normalize_dedupe_and_rank_search_results((weak_observed, confirmed_observed), max_results=2)

    assert [result.host for result in results] == ["confirmed.example", "weak.example"]


def test_broker_does_not_call_brave_when_searxng_succeeds() -> None:
    primary_result = _result("https://primary.example/a")
    primary = _FakeProvider("searxng", _response((primary_result,)))
    fallback = _FakeProvider("brave", _response((_result("https://brave.example/a", provider="brave"),), provider="brave"))
    broker = SearchBroker(
        primary_provider=primary,
        fallback_provider=fallback,
        config=SearchBrokerConfig(fallback_provider="brave"),
    )

    response = broker.search(query="latest", locale="en", max_results=3)

    assert response.results == (primary_result,)
    assert len(primary.calls) == 1
    assert fallback.calls == []
    assert response.metrics[0].fallback_reason == ""


def test_broker_falls_back_to_brave_on_searxng_empty_resultset() -> None:
    brave_result = _result("https://brave.example/a", provider="brave")
    primary = _FakeProvider("searxng", _response(()))
    fallback = _FakeProvider("brave", _response((brave_result,), provider="brave"))
    broker = SearchBroker(
        primary_provider=primary,
        fallback_provider=fallback,
        config=SearchBrokerConfig(fallback_provider="brave"),
    )

    response = broker.search(query="latest", locale="en", max_results=3)

    assert response.results == (brave_result,)
    assert len(fallback.calls) == 1
    assert [metric.provider for metric in response.metrics] == ["searxng", "brave"]
    assert [metric.fallback_reason for metric in response.metrics] == ["empty_resultset", "empty_resultset"]


@pytest.mark.parametrize(
    ("primary_error", "reason", "error_class"),
    [
        (SearchProviderTimeout("timed out"), "timeout", "timeout"),
        (SearchProviderError("failed"), "error", "error"),
    ],
)
def test_broker_falls_back_to_brave_on_searxng_timeout_or_error(
    primary_error: SearchProviderError,
    reason: str,
    error_class: str,
) -> None:
    brave_result = _result("https://brave.example/a", provider="brave")
    primary = _FakeProvider("searxng", error=primary_error)
    fallback = _FakeProvider("brave", _response((brave_result,), provider="brave"))
    broker = SearchBroker(
        primary_provider=primary,
        fallback_provider=fallback,
        config=SearchBrokerConfig(fallback_provider="brave"),
    )

    response = broker.search(query="latest", locale="en", max_results=3)

    assert response.results == (brave_result,)
    assert len(fallback.calls) == 1
    assert response.metrics[0].provider == "searxng"
    assert response.metrics[0].error_class == error_class
    assert [metric.fallback_reason for metric in response.metrics] == [reason, reason]


def test_broker_reports_brave_fallback_failure_without_losing_primary_metrics() -> None:
    primary = _FakeProvider("searxng", _response(()))
    fallback = _FakeProvider("brave", error=SearchProviderError("brave rejected"))
    broker = SearchBroker(
        primary_provider=primary,
        fallback_provider=fallback,
        config=SearchBrokerConfig(fallback_provider="brave"),
    )

    response = broker.search(query="latest", locale="en", max_results=3)

    assert response.results == ()
    assert [metric.provider for metric in response.metrics] == ["searxng", "brave"]
    assert [metric.fallback_reason for metric in response.metrics] == ["empty_resultset", "empty_resultset"]
    assert response.metrics[1].error_class == "error"


def test_broker_falls_back_when_host_diversity_is_below_minimum() -> None:
    primary_result = _result("https://same.example/a")
    brave_result = _result("https://brave.example/a", provider="brave")
    primary = _FakeProvider("searxng", _response((primary_result,)))
    fallback = _FakeProvider("brave", _response((brave_result,), provider="brave"))
    broker = SearchBroker(
        primary_provider=primary,
        fallback_provider=fallback,
        config=SearchBrokerConfig(fallback_provider="brave", min_host_diversity=2),
    )

    response = broker.search(query="latest", locale="en", max_results=3)

    assert response.results == (brave_result,)
    assert len(fallback.calls) == 1
    assert [metric.fallback_reason for metric in response.metrics] == [
        "low_host_diversity",
        "low_host_diversity",
    ]


def test_brave_provider_success_and_failure_use_injected_client_only() -> None:
    success_factory = _FakeHttpClientFactory(
        [
            httpx.Response(
                200,
                json={
                    "web": {
                        "results": [
                            {
                                "title": "Brave one",
                                "url": "https://brave-result.example/a",
                                "description": "Brave snippet",
                                "page_age": "2026-06-16",
                            }
                        ]
                    }
                },
            )
        ]
    )
    provider = BraveSearchProvider(
        BraveSearchConfig(api_key="test-key", timeout_seconds=2.0, max_results=5),
        http_client_factory=success_factory,
    )

    response = provider.search(query="latest", locale="de-DE", max_results=1)

    assert response.results[0].title == "Brave one"
    assert response.results[0].host == "brave-result.example"
    assert response.metrics[0].provider == "brave"
    assert success_factory.get_calls == [
        (
            "https://api.search.brave.com/res/v1/web/search",
            {"q": "latest", "count": 1, "search_lang": "de"},
        )
    ]
    assert success_factory.client_kwargs[0]["headers"]["X-Subscription-Token"] == "test-key"

    failure_factory = _FakeHttpClientFactory([httpx.Response(429, json={"error": "rate_limit"})])
    failing_provider = BraveSearchProvider(
        BraveSearchConfig(api_key="test-key"),
        http_client_factory=failure_factory,
    )
    with pytest.raises(SearchProviderError):
        failing_provider.search(query="latest", locale="en", max_results=1)
