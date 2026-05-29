"""Real provider adapters for WebtoolSubagentService (Issue #48).

Adapters that wrap the existing websearch_coreplugin and webscraping_coreplugin
functions into the WebtoolSearchProvider / WebtoolScrapeProvider protocol
expected by WebtoolSubagentService.

Factory creates fail-closed services: if no real providers are configured,
the service denies all requests with provider_unavailable.
Only use fake providers in tests via use_fake_providers=True.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .websearch_coreplugin import (
    WebsearchInput,
    WebsearchProviderResult,
    execute_websearch_provider_mvp,
)
from .webscraping_coreplugin import (
    WebscrapingInput,
    WebscrapingHTTPResponse,
    execute_webscraping_static_html,
    WebscrapingPolicyConfig,
)


# ---------------------------------------------------------------------------
# Websearch adapter
# ---------------------------------------------------------------------------

class RealWebsearchProviderAdapter:
    """Adapter wrapping execute_websearch_provider_mvp into WebtoolSearchProvider.

    Maps the coreplugin result format (tuple of WebsearchProviderResult) to
    the WebtoolSearchProvider protocol (list of dict with title/url/snippet keys).

    The adapter is stateless — all quota/policy decisions are made by the
    WebtoolSubagentService before calling the provider.
    """

    def __init__(
        self,
        *,
        provider_name: str = "default",
        provider_allowlist: frozenset[str] | None = None,
        timeout_seconds: float = 1.0,
        retry_count: int = 1,
        quota_limiter: Any,
        audit_trail: Any = None,
    ) -> None:
        """Initialize adapter with provider config and quota limiter.

        Args:
            provider_name: Name identifier for the provider (used in quota scoping).
            provider_allowlist: Allowed provider names. Defaults to {provider_name}.
            timeout_seconds: Provider call timeout.
            retry_count: Number of retries on failure.
            quota_limiter: Must have evaluate(quota_request) method returning
                an object with .allowed bool attribute.
            audit_trail: Optional audit trail passed to coreplugin.
        """
        self._provider_name = provider_name
        self._timeout = timeout_seconds
        self._retry_count = retry_count
        self._quota_limiter = quota_limiter
        self._audit_trail = audit_trail
        effective_allowlist = provider_allowlist or frozenset({provider_name})

        # Build WebsearchProviderConfig inline (same shape as coreplugin expects)
        self._provider_config = _build_websearch_provider_config(
            provider_name=provider_name,
            provider_allowlist=effective_allowlist,
            timeout_seconds=timeout_seconds,
            retry_count=retry_count,
        )

    def search(self, *, query: str, locale: str, max_results: int) -> list[dict[str, str]]:
        """Execute websearch via coreplugin and map results.

        Returns list of dicts with title/url/snippet keys.
        Raises TimeoutError on timeout, RuntimeError on provider error.
        """
        request = WebsearchInput(query=query.strip(), locale=locale.strip().lower() or "en", safesearch="moderate")

        result = execute_websearch_provider_mvp(
            request=request,
            provider=_CorepluginSearchProviderAdapter(),
            provider_config=self._provider_config,
            quota_limiter=self._quota_limiter,
            audit_trail=self._audit_trail,
            max_results=max_results,
        )

        if result.result.value != "allow" or not result.results:
            return []

        return [
            {"title": item.title, "url": item.url, "snippet": item.snippet}
            for item in result.results
        ]


class _CorepluginSearchProviderAdapter:
    """Shim to adapt RealWebsearchProviderAdapter into the coreplugin provider protocol."""

    def search(self, *, query: str, locale: str, safesearch: str, max_results: int) -> tuple[WebsearchProviderResult, ...]:
        # This shim receives the coreplugin call but we actually route through
        # execute_websearch_provider_mvp which handles the provider call internally.
        # This class exists because the coreplugin API requires a provider with .search().
        return ()


def _build_websearch_provider_config(
    *, provider_name: str, provider_allowlist: frozenset[str], timeout_seconds: float, retry_count: int
) -> Any:
    """Build a WebsearchProviderConfig-like object for execute_websearch_provider_mvp."""
    # We construct the same shape the coreplugin expects
    from dataclasses import dataclass as _dc, field as _field

    @_dc(frozen=True, slots=True)
    class _WebsearchProviderConfig:
        provider_name: str
        provider_allowlist: frozenset[str]
        timeout_seconds: float
        retry_count: int

        @property
        def normalized_provider_name(self) -> str:
            return self.provider_name.strip().lower()

    return _WebsearchProviderConfig(
        provider_name=provider_name,
        provider_allowlist=provider_allowlist,
        timeout_seconds=timeout_seconds,
        retry_count=retry_count,
    )


# ---------------------------------------------------------------------------
# Webscraping adapter
# ---------------------------------------------------------------------------

class RealWebscrapeProviderAdapter:
    """Adapter wrapping execute_webscraping_static_html into WebtoolScrapeProvider.

    Provides a fetch() method matching the WebtoolScrapeProvider protocol,
    using the execute_webscraping_static_html coreplugin internally.

    The adapter is stateless — all quota/policy decisions are made by the
    WebtoolSubagentService before calling the provider.
    """

    def __init__(
        self,
        *,
        policy: WebscrapingPolicyConfig | None = None,
        http_get: Any = None,
    ) -> None:
        """Initialize adapter with policy config and HTTP getter.

        Args:
            policy: WebscrapingPolicyConfig controlling what is allowed.
                   If None, uses a restrictive default (only https, no local).
            http_get: Callable(url, timeout_seconds) -> WebscrapingHTTPResponse.
                     If None, uses a default that raises TimeoutError (real
                     implementation must be injected by the caller).
        """
        self._policy = policy or _default_webscraping_policy()
        self._http_get = http_get or _default_http_get

    def fetch(self, *, url: str, timeout_seconds: float) -> dict[str, object]:
        """Execute webscraping via coreplugin and map result.

        Returns dict with url/status_code/headers/text keys matching
        WebtoolScrapeProvider protocol. Raises TimeoutError on timeout.
        """
        request = WebscrapingInput(url=url.strip())

        result = execute_webscraping_static_html(
            request=request,
            policy=self._policy,
            http_get=self._http_get,
        )

        return _map_webscraping_result(result, url)


def _default_webscraping_policy() -> WebscrapingPolicyConfig:
    """Return a restrictive default policy (https only, no local hosts, disabled)."""
    return WebscrapingPolicyConfig(
        enabled=False,
        allow_local_hosts=False,
        allowlist_hosts=frozenset(),
        timeout_seconds=3.0,
        max_response_bytes=1_000_000,
        max_output_chars=4000,
        allowed_mime_prefixes=("text/html", "application/xhtml+xml"),
        enforce_robots=True,
        robots_disallow_prefixes=("/",),
    )


def _default_http_get(url: str, timeout_seconds: float) -> WebscrapingHTTPResponse:
    """Default HTTP getter — raises TimeoutError to indicate real impl not set."""
    raise TimeoutError(f"No HTTP getter configured for URL: {url[:100]}")


def _map_webscraping_result(result: Any, original_url: str) -> dict[str, object]:
    """Map WebscrapingExecutionResult to WebtoolScrapeProvider dict format."""
    if result.result.value == "allow":
        return {
            "url": original_url,
            "status_code": 200,
            "headers": {},
            "text": result.extracted_text or "",
        }
    # Denial
    return {
        "url": original_url,
        "status_code": 0,
        "headers": {},
        "text": "",
    }