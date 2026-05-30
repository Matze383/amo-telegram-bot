from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from html import unescape
from typing import Any
from urllib.parse import urlparse

import httpx

from .websearch_coreplugin import (
    WebsearchInput,
    WebsearchProviderConfig,
    WebsearchProviderResult,
    execute_websearch_provider_mvp,
)
from .webscraping_coreplugin import (
    WebscrapingHTTPResponse,
    WebscrapingInput,
    WebscrapingPolicyConfig,
    execute_webscraping_static_html,
)


class RealWebsearchProviderAdapter:
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
        self._provider_name = provider_name
        self._timeout = timeout_seconds
        self._retry_count = retry_count
        self._quota_limiter = quota_limiter if hasattr(quota_limiter, "evaluate") else _AllowAllCoreQuotaLimiter()
        self._audit_trail = audit_trail
        effective_allowlist = provider_allowlist or frozenset({provider_name})
        self._provider_config = _build_websearch_provider_config(
            provider_name=provider_name,
            provider_allowlist=effective_allowlist,
            timeout_seconds=timeout_seconds,
            retry_count=retry_count,
        )

    def search(self, *, query: str, locale: str, max_results: int) -> list[dict[str, str]]:
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
        return [{"title": item.title, "url": item.url, "snippet": item.snippet} for item in result.results]


class _CorepluginSearchProviderAdapter:
    _ENDPOINT = "https://html.duckduckgo.com/html/"
    _UA = "amo-bot-websearch/1.0"

    def search(self, *, query: str, locale: str, safesearch: str, max_results: int) -> tuple[WebsearchProviderResult, ...]:
        limit = min(max(int(max_results), 1), 5)
        params = {
            "q": query,
            "kl": _normalize_ddg_locale(locale),
            "kp": _normalize_ddg_safesearch(safesearch),
        }
        try:
            with httpx.Client(timeout=1.5, follow_redirects=False, headers={"User-Agent": self._UA}) as client:
                response = client.get(self._ENDPOINT, params=params)
        except httpx.TimeoutException as exc:
            raise TimeoutError("websearch provider timeout") from exc
        except httpx.HTTPError as exc:
            raise RuntimeError("websearch provider request failed") from exc

        if response.status_code >= 500:
            raise RuntimeError("websearch provider server error")
        if response.status_code >= 400:
            return ()

        return tuple(_parse_ddg_html_results(response.text, limit))


def _normalize_ddg_locale(locale: str) -> str:
    candidate = (locale or "").strip().lower()
    if not candidate:
        return "en-us"
    if "-" not in candidate:
        return f"{candidate}-{candidate}"
    return candidate


def _normalize_ddg_safesearch(safesearch: str) -> str:
    normalized = (safesearch or "moderate").strip().lower()
    if normalized == "strict":
        return "1"
    if normalized == "off":
        return "-1"
    return "-1"


def _parse_ddg_html_results(html: str, limit: int) -> list[WebsearchProviderResult]:
    results: list[WebsearchProviderResult] = []
    for match in re.finditer(r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, flags=re.I | re.S):
        href = _strip_html(unescape(match.group(1))).strip()
        title = _strip_html(unescape(match.group(2))).strip()
        if not href or not title or not href.startswith("http"):
            continue

        snippet = ""
        tail = html[match.end(): match.end() + 2000]
        snippet_match = re.search(r'<a[^>]*class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>|<div[^>]*class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</div>', tail, flags=re.I | re.S)
        if snippet_match:
            snippet = _strip_html(unescape(snippet_match.group(1) or snippet_match.group(2) or "")).strip()

        results.append(WebsearchProviderResult(title=title, url=href, snippet=snippet))
        if len(results) >= limit:
            break
    return results


def _strip_html(value: str) -> str:
    return re.sub(r"<[^>]+>", "", value)


def _build_websearch_provider_config(
    *, provider_name: str, provider_allowlist: frozenset[str], timeout_seconds: float, retry_count: int
) -> WebsearchProviderConfig:
    return WebsearchProviderConfig(
        provider_name=provider_name,
        provider_allowlist=provider_allowlist,
        timeout_seconds=timeout_seconds,
        retry_count=retry_count,
    )


class _AllowAllCoreQuotaLimiter:
    class _Decision:
        allowed = True
        reason_code = "ok"

    def evaluate(self, _request: Any):
        return self._Decision()


class RealWebscrapeProviderAdapter:
    def __init__(
        self,
        *,
        policy: WebscrapingPolicyConfig | None = None,
        http_get: Any = None,
    ) -> None:
        self._policy = policy or _default_webscraping_policy()
        self._http_get = http_get or _default_http_get

    def fetch(self, *, url: str, timeout_seconds: float) -> dict[str, object]:
        request = WebscrapingInput(url=url.strip())
        result = execute_webscraping_static_html(
            request=request,
            policy=self._policy,
            http_get=self._http_get,
        )
        return _map_webscraping_result(result, url)


@dataclass(frozen=True, slots=True)
class _PlaywrightDeps:
    sync_playwright: Any
    timeout_error_cls: type[BaseException]


def _load_playwright_deps() -> _PlaywrightDeps | None:
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright
    except Exception:
        return None
    return _PlaywrightDeps(sync_playwright=sync_playwright, timeout_error_cls=PlaywrightTimeoutError)


class RealBrowserProviderAdapter:
    _DEFAULT_MAX_OUTPUT_CHARS = 4000
    _DISALLOWED_SCHEMES = {"file", "data", "javascript", "chrome", "about", "blob", "ftp", "ws", "wss"}

    def __init__(
        self,
        *,
        max_output_chars: int = _DEFAULT_MAX_OUTPUT_CHARS,
        deps: _PlaywrightDeps | None = None,
    ) -> None:
        self._max_output_chars = min(max(int(max_output_chars), 200), 8000)
        self._deps = deps if deps is not None else _load_playwright_deps()

    @property
    def available(self) -> bool:
        return self._deps is not None

    def render(self, *, url: str, timeout_seconds: float) -> dict[str, object]:
        parsed = _validate_browser_target_url(url)
        deps = self._deps
        if deps is None:
            raise RuntimeError("Browser provider unavailable")

        timeout_ms = max(200, int(timeout_seconds * 1000))

        try:
            with deps.sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                try:
                    context = browser.new_context(ignore_https_errors=False)
                    try:
                        page = context.new_page()
                        response = page.goto(parsed.geturl(), wait_until="domcontentloaded", timeout=timeout_ms)
                        body_text = page.locator("body").inner_text(timeout=timeout_ms)
                        status_code = response.status if response is not None else 200
                        return {
                            "url": parsed.geturl(),
                            "status_code": int(status_code),
                            "headers": {},
                            "text": body_text[: self._max_output_chars],
                        }
                    finally:
                        context.close()
                finally:
                    browser.close()
        except deps.timeout_error_cls as exc:
            raise TimeoutError("Browser render timed out") from exc
        except TimeoutError:
            raise
        except ValueError:
            raise
        except Exception as exc:
            raise RuntimeError("Browser render failed") from exc


def _validate_browser_target_url(url: str):
    parsed = urlparse((url or "").strip())
    if parsed.scheme.lower() in RealBrowserProviderAdapter._DISALLOWED_SCHEMES:
        raise ValueError("URL scheme not allowed")
    if parsed.scheme.lower() != "https":
        raise ValueError("Only HTTPS URLs are allowed")
    if not parsed.hostname:
        raise ValueError("Invalid host")

    host = parsed.hostname.strip().lower().rstrip(".")
    if host in {"localhost", "localhost.localdomain"}:
        raise ValueError("Host not allowed")

    try:
        ip = ipaddress.ip_address(host)
        if _is_private_or_internal_ip(ip):
            raise ValueError("Host not allowed")
        return parsed
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(host, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise RuntimeError("Host resolution failed") from exc

    seen_any = False
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        addr = sockaddr[0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        seen_any = True
        if _is_private_or_internal_ip(ip):
            raise ValueError("Host not allowed")

    if not seen_any:
        raise RuntimeError("Host resolution failed")

    return parsed


def _is_private_or_internal_ip(ip: ipaddress._BaseAddress) -> bool:
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _default_webscraping_policy() -> WebscrapingPolicyConfig:
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
    raise TimeoutError("No HTTP getter configured")


def _map_webscraping_result(result: Any, original_url: str) -> dict[str, object]:
    if result.result.value == "allow":
        return {
            "url": original_url,
            "status_code": 200,
            "headers": {},
            "text": result.extracted_text or "",
        }
    return {
        "url": original_url,
        "status_code": 0,
        "headers": {},
        "text": "",
    }
