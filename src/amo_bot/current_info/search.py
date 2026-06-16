from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx

from amo_bot.current_info.models import SearchProviderMetric, SearchProviderResponse, SearchResult


logger = logging.getLogger(__name__)


class SearchProvider(Protocol):
    name: str

    def search(self, *, query: str, locale: str, max_results: int) -> SearchProviderResponse:
        ...


class SearchProviderError(RuntimeError):
    error_class = "error"


class SearchProviderTimeout(SearchProviderError):
    error_class = "timeout"


class SearchProviderInvalidResponse(SearchProviderError):
    error_class = "invalid_response"


@dataclass(frozen=True, slots=True)
class SearxngSearchConfig:
    base_url: str
    timeout_seconds: float = 3.0
    max_results: int = 5
    language: str | None = None
    categories: str | None = None


@dataclass(frozen=True, slots=True)
class BraveSearchConfig:
    api_key: str
    base_url: str = "https://api.search.brave.com/res/v1/web/search"
    timeout_seconds: float = 3.0
    max_results: int = 5
    country: str | None = None
    search_lang: str | None = None


@dataclass(frozen=True, slots=True)
class SearchBrokerConfig:
    fallback_provider: str = ""
    min_host_diversity: int = 0


class SearxngSearchProvider:
    name = "searxng"

    def __init__(self, config: SearxngSearchConfig, *, http_client_factory: Any = None) -> None:
        self._config = config
        self._http_client_factory = http_client_factory or httpx.Client
        self._base_url = _validate_base_url(config.base_url)

    def search(self, *, query: str, locale: str, max_results: int) -> SearchProviderResponse:
        started = time.perf_counter()
        limit = _bounded_limit(max_results=max_results, configured_max_results=self._config.max_results)
        try:
            results = self._search(query=query, locale=locale, limit=limit)
        except SearchProviderError as exc:
            raise exc
        except httpx.TimeoutException as exc:
            raise SearchProviderTimeout("searxng timeout") from exc
        except httpx.HTTPError as exc:
            raise SearchProviderError("searxng request failed") from exc
        metric = _metric(
            provider=self.name,
            started=started,
            hit_count=len(results),
            host_diversity=_host_diversity(results),
        )
        return SearchProviderResponse(results=tuple(results), metrics=(metric,))

    def _search(self, *, query: str, locale: str, limit: int) -> tuple[SearchResult, ...]:
        params: dict[str, str | int] = {
            "q": query,
            "format": "json",
            "language": self._config.language or _normalize_locale(locale),
        }
        if self._config.categories:
            params["categories"] = self._config.categories

        endpoint = f"{self._base_url}/search"
        with self._http_client_factory(
            timeout=self._config.timeout_seconds,
            follow_redirects=False,
            headers={"Accept": "application/json", "User-Agent": _default_user_agent()},
        ) as client:
            response = client.get(endpoint, params=params)

        if response.status_code >= 500:
            raise SearchProviderError("searxng server error")
        if response.status_code >= 400:
            return ()

        payload = _json_payload(response, provider=self.name)
        items = payload.get("results")
        if not isinstance(items, list):
            raise SearchProviderInvalidResponse("searxng response missing results list")

        results: list[SearchResult] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            result = _result_from_mapping(
                item,
                provider=self.name,
                rank=len(results) + 1,
                snippet_keys=("content", "snippet"),
                date_keys=("publishedDate", "published_date", "date"),
            )
            if result is None:
                continue
            results.append(result)
            if len(results) >= limit:
                break
        return tuple(results)


class BraveSearchProvider:
    name = "brave"

    def __init__(self, config: BraveSearchConfig, *, http_client_factory: Any = None) -> None:
        self._config = config
        self._http_client_factory = http_client_factory or httpx.Client
        self._endpoint = _validate_endpoint_url(config.base_url)

    def search(self, *, query: str, locale: str, max_results: int) -> SearchProviderResponse:
        started = time.perf_counter()
        limit = _bounded_limit(max_results=max_results, configured_max_results=self._config.max_results)
        try:
            results = self._search(query=query, locale=locale, limit=limit)
        except SearchProviderError as exc:
            raise exc
        except httpx.TimeoutException as exc:
            raise SearchProviderTimeout("brave timeout") from exc
        except httpx.HTTPError as exc:
            raise SearchProviderError("brave request failed") from exc
        metric = _metric(
            provider=self.name,
            started=started,
            hit_count=len(results),
            host_diversity=_host_diversity(results),
        )
        return SearchProviderResponse(results=tuple(results), metrics=(metric,))

    def _search(self, *, query: str, locale: str, limit: int) -> tuple[SearchResult, ...]:
        params: dict[str, str | int] = {"q": query, "count": limit}
        search_lang = self._config.search_lang or _brave_search_lang(locale)
        if search_lang:
            params["search_lang"] = search_lang
        if self._config.country:
            params["country"] = self._config.country

        with self._http_client_factory(
            timeout=self._config.timeout_seconds,
            follow_redirects=False,
            headers={
                "Accept": "application/json",
                "User-Agent": _default_user_agent(),
                "X-Subscription-Token": self._config.api_key,
            },
        ) as client:
            response = client.get(self._endpoint, params=params)

        if response.status_code >= 500:
            raise SearchProviderError("brave server error")
        if response.status_code >= 400:
            raise SearchProviderError("brave request rejected")

        payload = _json_payload(response, provider=self.name)
        web = payload.get("web")
        if not isinstance(web, dict):
            raise SearchProviderInvalidResponse("brave response missing web object")
        items = web.get("results")
        if not isinstance(items, list):
            raise SearchProviderInvalidResponse("brave response missing results list")

        results: list[SearchResult] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            result = _result_from_mapping(
                item,
                provider=self.name,
                rank=len(results) + 1,
                snippet_keys=("description", "snippet"),
                date_keys=("page_age", "age", "date"),
            )
            if result is None:
                continue
            results.append(result)
            if len(results) >= limit:
                break
        return tuple(results)


class SearchBroker:
    """Current-info search provider chain with SearXNG primary and optional fallback."""

    def __init__(
        self,
        *,
        primary_provider: SearchProvider,
        fallback_provider: SearchProvider | None = None,
        config: SearchBrokerConfig | None = None,
    ) -> None:
        self._primary_provider = primary_provider
        self._fallback_provider = fallback_provider
        self._config = config or SearchBrokerConfig()

    def search(self, *, query: str, locale: str, max_results: int) -> SearchProviderResponse:
        started = time.perf_counter()
        try:
            primary = self._primary_provider.search(query=query, locale=locale, max_results=max_results)
        except SearchProviderTimeout as exc:
            return self._fallback_or_error(
                query=query,
                locale=locale,
                max_results=max_results,
                reason="timeout",
                exc=exc,
                started=started,
            )
        except SearchProviderError as exc:
            return self._fallback_or_error(
                query=query,
                locale=locale,
                max_results=max_results,
                reason="error",
                exc=exc,
                started=started,
            )

        reason = self._fallback_reason_for_results(primary.results)
        if reason:
            return self._fallback_or_primary(
                primary=primary,
                query=query,
                locale=locale,
                max_results=max_results,
                reason=reason,
            )
        return primary

    def _fallback_reason_for_results(self, results: tuple[SearchResult, ...]) -> str:
        if not results:
            return "empty_resultset"
        min_diversity = max(int(self._config.min_host_diversity), 0)
        if min_diversity and _host_diversity(results) < min_diversity:
            return "low_host_diversity"
        return ""

    def _fallback_or_error(
        self,
        *,
        query: str,
        locale: str,
        max_results: int,
        reason: str,
        exc: SearchProviderError,
        started: float,
    ) -> SearchProviderResponse:
        primary_metric = SearchProviderMetric(
            provider=getattr(self._primary_provider, "name", "primary"),
            latency_ms=(time.perf_counter() - started) * 1000.0,
            hit_count=0,
            error_class=getattr(exc, "error_class", exc.__class__.__name__),
            fallback_reason=reason,
        )
        return self._fallback_or_primary(
            primary=SearchProviderResponse(results=(), metrics=(primary_metric,)),
            query=query,
            locale=locale,
            max_results=max_results,
            reason=reason,
        )

    def _fallback_or_primary(
        self,
        *,
        primary: SearchProviderResponse,
        query: str,
        locale: str,
        max_results: int,
        reason: str,
    ) -> SearchProviderResponse:
        fallback = self._fallback_provider
        tagged_primary_metrics = _metrics_with_fallback_reason(primary.metrics, reason=reason)
        if fallback is None or self._config.fallback_provider.casefold() != fallback.name.casefold():
            return SearchProviderResponse(results=primary.results, metrics=tagged_primary_metrics)

        try:
            fallback_response = fallback.search(query=query, locale=locale, max_results=max_results)
        except SearchProviderError as exc:
            failed_metric = SearchProviderMetric(
                provider=getattr(fallback, "name", "fallback"),
                latency_ms=0.0,
                hit_count=0,
                error_class=getattr(exc, "error_class", exc.__class__.__name__),
                fallback_reason=reason,
            )
            logger.info(
                "current_info_search_fallback_failed",
                extra={"provider": fallback.name, "fallback_reason": reason, "error_class": failed_metric.error_class},
            )
            return SearchProviderResponse(results=primary.results, metrics=tagged_primary_metrics + (failed_metric,))

        tagged_metrics = _metrics_with_fallback_reason(fallback_response.metrics, reason=reason)
        logger.info(
            "current_info_search_fallback_used",
            extra={"provider": fallback.name, "fallback_reason": reason, "hit_count": len(fallback_response.results)},
        )
        return SearchProviderResponse(results=fallback_response.results, metrics=tagged_primary_metrics + tagged_metrics)


def build_search_broker_from_settings(settings: Any, *, http_client_factory: Any = None) -> SearchBroker | None:
    searxng_url = (getattr(settings, "amo_searxng_url", None) or "").strip()
    if not searxng_url:
        return None

    max_results = int(getattr(settings, "amo_search_max_results", 5))
    primary = SearxngSearchProvider(
        SearxngSearchConfig(
            base_url=searxng_url,
            timeout_seconds=float(getattr(settings, "amo_searxng_timeout_seconds", 3.0)),
            max_results=max_results,
        ),
        http_client_factory=http_client_factory,
    )

    fallback: SearchProvider | None = None
    brave_key = (getattr(settings, "amo_brave_search_api_key", None) or "").strip()
    if brave_key:
        fallback = BraveSearchProvider(
            BraveSearchConfig(
                api_key=brave_key,
                timeout_seconds=float(getattr(settings, "amo_brave_search_timeout_seconds", 3.0)),
                max_results=max_results,
            ),
            http_client_factory=http_client_factory,
        )

    return SearchBroker(
        primary_provider=primary,
        fallback_provider=fallback,
        config=SearchBrokerConfig(
            fallback_provider=str(getattr(settings, "amo_search_fallback_provider", "") or ""),
            min_host_diversity=int(getattr(settings, "amo_search_min_host_diversity", 0)),
        ),
    )


def _json_payload(response: httpx.Response, *, provider: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise SearchProviderInvalidResponse(f"{provider} response is not json") from exc
    if not isinstance(payload, dict):
        raise SearchProviderInvalidResponse(f"{provider} response is not an object")
    return payload


def _result_from_mapping(
    item: dict[str, Any],
    *,
    provider: str,
    rank: int,
    snippet_keys: tuple[str, ...],
    date_keys: tuple[str, ...],
) -> SearchResult | None:
    title = _bound_text(str(item.get("title") or "").strip(), 200)
    url = _bound_text(str(item.get("url") or "").strip(), 1000)
    if not title or not url:
        return None
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None

    snippet = ""
    for key in snippet_keys:
        snippet = _bound_text(str(item.get(key) or "").strip(), 500)
        if snippet:
            break
    date = ""
    for key in date_keys:
        date = _bound_text(str(item.get(key) or "").strip(), 80)
        if date:
            break
    host = (parsed.hostname or "").lower().rstrip(".")
    return SearchResult(title=title, url=url, snippet=snippet, provider=provider, rank=rank, host=host, date=date)


def _metric(
    *,
    provider: str,
    started: float,
    hit_count: int,
    host_diversity: int = 0,
    error_class: str = "",
    fallback_reason: str = "",
) -> SearchProviderMetric:
    return SearchProviderMetric(
        provider=provider,
        latency_ms=(time.perf_counter() - started) * 1000.0,
        hit_count=hit_count,
        error_class=error_class,
        fallback_reason=fallback_reason,
        host_diversity=host_diversity,
    )


def _metrics_with_fallback_reason(
    metrics: tuple[SearchProviderMetric, ...],
    *,
    reason: str,
) -> tuple[SearchProviderMetric, ...]:
    return tuple(
        SearchProviderMetric(
            provider=metric.provider,
            latency_ms=metric.latency_ms,
            hit_count=metric.hit_count,
            error_class=metric.error_class,
            fallback_reason=reason or metric.fallback_reason,
            host_diversity=metric.host_diversity,
        )
        for metric in metrics
    )


def _host_diversity(results: tuple[SearchResult, ...]) -> int:
    return len({result.host for result in results if result.host})


def _bounded_limit(*, max_results: int, configured_max_results: int) -> int:
    return min(max(int(max_results), 1), max(int(configured_max_results), 1), 10)


def _validate_base_url(value: str) -> str:
    parsed = urlparse((value or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("search base URL must be an HTTP(S) URL")
    if parsed.query or parsed.fragment:
        raise ValueError("search base URL must not include query or fragment")
    return parsed.geturl().rstrip("/")


def _validate_endpoint_url(value: str) -> str:
    parsed = urlparse((value or "").strip())
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("search endpoint URL must be an HTTPS URL")
    if parsed.query or parsed.fragment:
        raise ValueError("search endpoint URL must not include query or fragment")
    return parsed.geturl().rstrip("/")


def _normalize_locale(locale: str) -> str:
    candidate = (locale or "").strip().lower().replace("_", "-")
    if not candidate:
        return "en-us"
    safe_map = {"en": "en-us", "de": "de-de"}
    if candidate in safe_map:
        return safe_map[candidate]
    if re.fullmatch(r"[a-z]{2}(?:-[a-z]{2})?", candidate):
        return candidate
    return "en-us"


def _brave_search_lang(locale: str) -> str:
    normalized = _normalize_locale(locale)
    return normalized.split("-", 1)[0]


def _default_user_agent() -> str:
    return "AMO-Telegram-Bot/1.0"


def _bound_text(value: str, max_len: int) -> str:
    text = " ".join((value or "").split()).strip()
    if len(text) <= max_len:
        return text
    return text[:max_len].rstrip()
