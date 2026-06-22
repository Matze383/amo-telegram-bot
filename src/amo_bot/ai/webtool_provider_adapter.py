from __future__ import annotations

import base64
import ipaddress
import os
import re
import shutil
import socket
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from typing import Any
from urllib.parse import parse_qs, urlparse

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
        normalized_locale = locale.strip().lower() or "en"
        normalized_query = _normalize_market_price_query(query=query, locale=normalized_locale)
        request = WebsearchInput(query=normalized_query, locale=normalized_locale, safesearch="moderate")
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


def _normalize_market_price_query(*, query: str, locale: str) -> str:
    raw = " ".join((query or "").split()).strip()
    if not raw:
        return ""

    lowered = raw.lower()
    locale_norm = (locale or "").strip().lower()

    rewritten_current = _rewrite_currentness_query(raw=raw)
    rewritten_lowered = rewritten_current.lower()

    currency_match = re.search(r"\b(usd|eur|gbp|chf|jpy|cad|aud)\b", rewritten_lowered)
    currency = (currency_match.group(1) if currency_match else ("eur" if locale_norm.startswith("de") else "usd")).upper()

    has_btc_hint = bool(re.search(r"\b(bitcoin|btc)\b", rewritten_lowered))
    has_price_hint = bool(re.search(r"\b(price|kurs|preis|rate|wert)\b", rewritten_lowered))
    has_current_hint = bool(re.search(r"\b(current|currently|aktuell(?:e|er|en|es)?|jetzt|now)\b", rewritten_lowered))
    is_market_query = has_btc_hint and (has_price_hint or has_current_hint)
    if not is_market_query:
        return rewritten_current

    if locale_norm.startswith("de"):
        if has_current_hint and currency_match is None:
            return "bitcoin kurs"
        return f"bitcoin kurs {currency} BTC"
    return f"bitcoin price {currency} BTC"


def _rewrite_currentness_query(*, raw: str) -> str:
    tokens = [tok for tok in raw.split(" ") if tok]
    if not tokens:
        return ""

    cleaned = [tok.strip(".,!?;:()[]{}\"'") for tok in tokens]
    lowered = [tok.lower() for tok in cleaned]

    lowered_text = " ".join(lowered)
    latest_news_match = re.match(r"^latest\s+(.+?)\s+(news|updates)$", lowered_text)
    if latest_news_match:
        return f"{latest_news_match.group(1)} {latest_news_match.group(2)}".strip()
    topic_latest_news_match = re.match(r"^(.+?)\s+latest\s+(news|updates)$", lowered_text)
    if topic_latest_news_match:
        return f"{topic_latest_news_match.group(1)} {topic_latest_news_match.group(2)}".strip()

    subject_tokens = [tok for tok in lowered if tok and tok not in {"current", "currently", "latest", "now", "today"}]
    has_subject = any(re.search(r"[a-zA-ZäöüÄÖÜ0-9]", tok or "") for tok in subject_tokens)

    electrical_context = {
        "amp",
        "amps",
        "ampere",
        "amperes",
        "voltage",
        "volt",
        "volts",
        "resistance",
        "ohm",
        "ohms",
        "ac",
        "dc",
        "circuit",
        "electronics",
        "electrical",
        "electric",
    }
    has_electrical_context = any(tok in electrical_context for tok in lowered)

    if has_subject and not has_electrical_context:
        filtered: list[str] = []
        for original, lower in zip(tokens, lowered, strict=False):
            if lower in {"current", "currently"}:
                continue
            if lower == "latest":
                continue
            filtered.append(original)
        return " ".join(filtered).strip()

    return raw

@dataclass(frozen=True, slots=True)
class _SearxngConfig:
    base_url: str
    timeout_seconds: float
    max_results: int
    language: str | None = None
    categories: str | None = None


class _CorepluginSearchProviderAdapter:
    _HTML_ENDPOINT = "https://html.duckduckgo.com/html/"
    _LITE_ENDPOINT = "https://lite.duckduckgo.com/lite/"
    _BING_ENDPOINT = "https://www.bing.com/search"
    _MOJEEK_ENDPOINT = "https://www.mojeek.com/search"
    _DDG_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    _BING_UA = "Mozilla/5.0"

    def search(self, *, query: str, locale: str, safesearch: str, max_results: int) -> tuple[WebsearchProviderResult, ...]:
        limit = min(max(int(max_results), 1), 5)

        searxng = _resolve_searxng_config(locale=locale, max_results=limit)
        if searxng is None:
            return ()
        searxng_results = _search_searxng_json(query=query, config=searxng)
        return tuple(searxng_results[:limit])


def _resolve_searxng_config(*, locale: str, max_results: int) -> _SearxngConfig | None:
    base_url = (os.getenv("SEARXNG_BASE_URL") or "").strip()
    config_family = "primary"
    if not base_url:
        base_url = (os.getenv("AMO_WEBSEARCH_SEARXNG_BASE_URL") or "").strip()
        config_family = "websearch"
    if not base_url:
        base_url = (os.getenv("AMO_SEARXNG_URL") or "").strip()
        config_family = "current_info"
    if not base_url:
        return None

    validated_base_url = _validate_search_endpoint_base_url(base_url)
    if config_family == "current_info":
        timeout_raw = (
            os.getenv("AMO_SEARXNG_TIMEOUT_SECONDS") or os.getenv("AMO_WEBSEARCH_SEARXNG_TIMEOUT_SECONDS") or ""
        ).strip()
        max_results_raw = (
            os.getenv("AMO_SEARCH_MAX_RESULTS") or os.getenv("AMO_WEBSEARCH_MAX_RESULTS") or ""
        ).strip()
    else:
        timeout_raw = (
            os.getenv("AMO_WEBSEARCH_SEARXNG_TIMEOUT_SECONDS") or os.getenv("AMO_SEARXNG_TIMEOUT_SECONDS") or ""
        ).strip()
        max_results_raw = (
            os.getenv("AMO_WEBSEARCH_MAX_RESULTS") or os.getenv("AMO_SEARCH_MAX_RESULTS") or ""
        ).strip()
    timeout_seconds = 3.0
    if timeout_raw:
        try:
            timeout_seconds = float(timeout_raw)
        except ValueError:
            timeout_seconds = 3.0
    timeout_seconds = min(max(timeout_seconds, 0.5), 12.0)

    configured_max_results = max_results
    if max_results_raw:
        try:
            configured_max_results = int(max_results_raw)
        except ValueError:
            configured_max_results = max_results
    configured_max_results = min(max(configured_max_results, 1), 5)

    lang_raw = (os.getenv("AMO_WEBSEARCH_SEARXNG_LANGUAGE") or "").strip().lower()
    language = lang_raw if re.fullmatch(r"[a-z]{2}(?:-[a-z]{2})?", lang_raw) else None
    if language is None:
        language = _normalize_ddg_locale(locale)

    categories_raw = (os.getenv("AMO_WEBSEARCH_SEARXNG_CATEGORIES") or "").strip().lower()
    categories = categories_raw if re.fullmatch(r"[a-z0-9_,\-]+", categories_raw) else None

    return _SearxngConfig(
        base_url=validated_base_url,
        timeout_seconds=timeout_seconds,
        max_results=configured_max_results,
        language=language,
        categories=categories,
    )


def _validate_search_endpoint_base_url(value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Invalid search endpoint scheme")
    if not parsed.hostname:
        raise ValueError("Invalid search endpoint host")
    if parsed.query or parsed.fragment:
        raise ValueError("Search endpoint base URL must not include query or fragment")

    host = parsed.hostname.strip().lower().rstrip(".")
    is_private_host = _is_private_or_internal_hostname(host)
    if is_private_host and parsed.scheme != "http":
        # private/loopback networks are allowed with either scheme; normalize to parsed url
        pass
    elif (not is_private_host) and parsed.scheme != "https":
        raise ValueError("Public search endpoint must use HTTPS")

    path = parsed.path.rstrip("/")
    normalized_path = path if path else ""
    port = f":{parsed.port}" if parsed.port is not None else ""
    return f"{parsed.scheme}://{host}{port}{normalized_path}"


def _is_private_or_internal_hostname(host: str) -> bool:
    if host in {"localhost", "localhost.localdomain"}:
        return True
    try:
        ip = ipaddress.ip_address(host)
        return _is_private_or_internal_ip(ip)
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except OSError:
        return False

    has_ip = False
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        addr = sockaddr[0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        has_ip = True
        if _is_private_or_internal_ip(ip):
            return True
    return has_ip and False


def _search_searxng_json(*, query: str, config: _SearxngConfig) -> list[WebsearchProviderResult]:
    params: dict[str, str | int] = {
        "q": query,
        "format": "json",
        "language": config.language or "en-us",
    }
    if config.categories:
        params["categories"] = config.categories

    endpoint = f"{config.base_url}/search"
    try:
        with httpx.Client(
            timeout=config.timeout_seconds,
            follow_redirects=False,
            headers={"User-Agent": _CorepluginSearchProviderAdapter._DDG_UA, "Accept": "application/json"},
        ) as client:
            response = client.get(endpoint, params=params)
            if response.status_code >= 500:
                raise RuntimeError("websearch provider server error")
            if response.status_code >= 400:
                return []
            payload = response.json()
    except httpx.TimeoutException as exc:
        raise TimeoutError("websearch provider timeout") from exc
    except httpx.HTTPError as exc:
        raise RuntimeError("websearch provider request failed") from exc

    parsed_results: list[WebsearchProviderResult] = []
    for item in (payload.get("results") or []):
        if not isinstance(item, dict):
            continue
        title = _bound_text(str(item.get("title") or "").strip(), 200)
        url = _bound_text(str(item.get("url") or "").strip(), 1000)
        snippet = _bound_text(str(item.get("content") or item.get("snippet") or "").strip(), 400)
        if not title or not url:
            continue
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        parsed_results.append(WebsearchProviderResult(title=title, url=url, snippet=snippet))
        if len(parsed_results) >= config.max_results:
            break
    return parsed_results


def _normalize_ddg_locale(locale: str) -> str:
    candidate = (locale or "").strip().lower()
    if not candidate:
        return "en-us"

    normalized = candidate.replace("_", "-")
    safe_map = {
        "en": "en-us",
        "en-us": "en-us",
        "en-gb": "en-gb",
        "de": "de-de",
        "de-de": "de-de",
    }
    if normalized in safe_map:
        return safe_map[normalized]

    if re.fullmatch(r"[a-z]{2}-[a-z]{2}", normalized):
        return normalized

    return "en-us"


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
        href = _bound_text(_strip_html(unescape(match.group(1))).strip(), 1000)
        title = _bound_text(_strip_html(unescape(match.group(2))).strip(), 200)
        if not href or not title or not href.startswith("http"):
            continue

        snippet = ""
        tail = html[match.end(): match.end() + 2000]
        snippet_match = re.search(r'<a[^>]*class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>|<div[^>]*class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</div>', tail, flags=re.I | re.S)
        if snippet_match:
            snippet = _bound_text(_strip_html(unescape(snippet_match.group(1) or snippet_match.group(2) or "")).strip(), 400)

        results.append(WebsearchProviderResult(title=title, url=href, snippet=snippet))
        if len(results) >= limit:
            break
    return results


def _parse_ddg_lite_results(html: str, limit: int) -> list[WebsearchProviderResult]:
    results: list[WebsearchProviderResult] = []
    for match in re.finditer(r'<a[^>]*class="[^"]*result-link[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, flags=re.I | re.S):
        href = _bound_text(_strip_html(unescape(match.group(1))).strip(), 1000)
        title = _bound_text(_strip_html(unescape(match.group(2))).strip(), 200)
        if not href or not title or not href.startswith("http"):
            continue

        snippet = ""
        tail = html[match.end(): match.end() + 2500]
        snippet_match = re.search(r'<td[^>]*class="[^"]*result-snippet[^"]*"[^>]*>(.*?)</td>|<td[^>]*>(.*?)</td>', tail, flags=re.I | re.S)
        if snippet_match:
            snippet_candidate = _strip_html(unescape(snippet_match.group(1) or snippet_match.group(2) or "")).strip()
            if snippet_candidate and not snippet_candidate.startswith("http"):
                snippet = _bound_text(snippet_candidate, 400)

        results.append(WebsearchProviderResult(title=title, url=href, snippet=snippet))
        if len(results) >= limit:
            break
    return results


def _parse_bing_html_results(html: str, limit: int) -> list[WebsearchProviderResult]:
    if _looks_like_bing_challenge(html):
        return []

    results: list[WebsearchProviderResult] = []
    for block in re.finditer(r"<li[^>]*class=[\"'][^\"']*b_algo[^\"']*[\"'][^>]*>(.*?)</li>", html, flags=re.I | re.S):
        li_html = block.group(1)
        link_match = re.search(r"<h2[^>]*>\s*<a[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", li_html, flags=re.I | re.S)
        if not link_match:
            continue

        raw_href = _bound_text(_strip_html(unescape(link_match.group(1))).strip(), 1000)
        title = _bound_text(_strip_html(unescape(link_match.group(2))).strip(), 200)
        if not raw_href or not title:
            continue

        resolved = _resolve_bing_result_url(raw_href)
        if not resolved:
            continue

        snippet_match = re.search(
            r'<div[^>]*class=["\'][^"\']*b_caption[^"\']*["\'][^>]*>.*?<p[^>]*>(.*?)</p>|<p[^>]*>(.*?)</p>',
            li_html,
            flags=re.I | re.S,
        )
        snippet = ""
        if snippet_match:
            snippet = _bound_text(_strip_html(unescape(snippet_match.group(1) or snippet_match.group(2) or "")).strip(), 400)

        results.append(WebsearchProviderResult(title=title, url=resolved, snippet=snippet))
        if len(results) >= limit:
            break

    return results


def _looks_like_bing_challenge(html: str) -> bool:
    lowered = (html or "").lower()
    markers = (
        "captcha",
        "challenge",
        "verify you are human",
        "why did this happen",
        "our systems have detected unusual traffic",
        "distil",
        "perimeterx",
    )
    return any(marker in lowered for marker in markers)


def _parse_mojeek_html_results(html: str, limit: int) -> list[WebsearchProviderResult]:
    results: list[WebsearchProviderResult] = []
    for block in re.finditer(r"<li[^>]*class=[\"'][^\"']*result[^\"']*[\"'][^>]*>(.*?)</li>", html, flags=re.I | re.S):
        li_html = block.group(1)
        link_match = re.search(r"<a[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", li_html, flags=re.I | re.S)
        if not link_match:
            continue
        href = _bound_text(_strip_html(unescape(link_match.group(1))).strip(), 1000)
        title = _bound_text(_strip_html(unescape(link_match.group(2))).strip(), 200)
        if not href or not title:
            continue
        parsed_href = urlparse(href)
        if parsed_href.scheme not in {"http", "https"} or not parsed_href.netloc:
            continue

        snippet = ""
        snippet_match = re.search(
            r'<p[^>]*class=["\'][^"\']*(?:s|desc|snippet)[^"\']*["\'][^>]*>(.*?)</p>|<p[^>]*>(.*?)</p>',
            li_html,
            flags=re.I | re.S,
        )
        if snippet_match:
            snippet = _bound_text(_strip_html(unescape(snippet_match.group(1) or snippet_match.group(2) or "")).strip(), 400)

        results.append(WebsearchProviderResult(title=title, url=href, snippet=snippet))
        if len(results) >= limit:
            break

    return results


def _resolve_bing_result_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme in {"http", "https"} and parsed.netloc and "bing.com/ck/a" not in url:
        return _bound_text(parsed.geturl(), 1000)

    host = (parsed.netloc or "").lower()
    if not host.endswith("bing.com"):
        return ""
    if not parsed.path.startswith("/ck/a"):
        return ""

    raw_u = (parse_qs(parsed.query).get("u") or [""])[0]
    if not raw_u.startswith("a1"):
        return ""

    payload = raw_u[2:]
    if not payload:
        return ""

    try:
        padded = payload + "=" * ((4 - len(payload) % 4) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8", errors="ignore").strip()
    except Exception:
        return ""

    target = decoded
    if target.lower().startswith("http://") or target.lower().startswith("https://"):
        parsed_target = urlparse(target)
        if parsed_target.scheme in {"http", "https"} and parsed_target.netloc:
            return _bound_text(parsed_target.geturl(), 1000)
    return ""

def _strip_html(value: str) -> str:
    return re.sub(r"<[^>]+>", "", value)


def _bound_text(value: str, max_len: int) -> str:
    text = " ".join((value or "").split()).strip()
    if len(text) <= max_len:
        return text
    return text[:max_len].rstrip()


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
    _DEFAULT_UA = _CorepluginSearchProviderAdapter._DDG_UA
    _DEFAULT_ACCEPT = "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8"
    _DEFAULT_ACCEPT_LANGUAGE = "en-US,en;q=0.9,de;q=0.8"

    def __init__(
        self,
        *,
        policy: WebscrapingPolicyConfig | None = None,
        http_get: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._policy = policy or _default_webscraping_policy()
        self._headers = _default_static_fetch_headers(headers)
        self._http_get = http_get or self._default_http_get

    def fetch(self, *, url: str, timeout_seconds: float) -> dict[str, object]:
        request = WebscrapingInput(url=url.strip())
        policy = self._policy
        if policy is _DEFAULT_WEBSCRAPING_POLICY:
            policy = _policy_for_single_public_url(url, timeout_seconds=timeout_seconds)
        result = execute_webscraping_static_html(
            request=request,
            policy=policy,
            http_get=self._http_get,
        )
        return _map_webscraping_result(result, url)

    def _default_http_get(self, url: str, timeout_seconds: float) -> WebscrapingHTTPResponse:
        try:
            with httpx.Client(timeout=timeout_seconds, follow_redirects=True, headers=self._headers) as client:
                response = client.get(url)
        except httpx.TimeoutException as exc:
            raise TimeoutError("Static scrape timed out") from exc
        except httpx.HTTPError as exc:
            raise RuntimeError("Static scrape request failed") from exc
        return WebscrapingHTTPResponse(
            status_code=int(response.status_code),
            headers=dict(response.headers),
            body=response.content,
        )


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
    _DEFAULT_MAX_PAGES = 3
    _DEFAULT_TIME_BUDGET_SECONDS = 10.0
    _MAX_SNIPPETS_PER_PAGE = 5
    _MAX_SNIPPET_CHARS = 500
    _DISALLOWED_SCHEMES = {"file", "data", "javascript", "chrome", "about", "blob", "ftp", "ws", "wss"}

    def __init__(
        self,
        *,
        max_output_chars: int = _DEFAULT_MAX_OUTPUT_CHARS,
        max_pages: int = _DEFAULT_MAX_PAGES,
        time_budget_seconds: float = _DEFAULT_TIME_BUDGET_SECONDS,
        deps: _PlaywrightDeps | None = None,
    ) -> None:
        self._max_output_chars = min(max(int(max_output_chars), 200), 8000)
        self._max_pages = min(max(int(max_pages), 1), 5)
        self._time_budget_seconds = min(max(float(time_budget_seconds), 0.5), 30.0)
        self._deps = deps if deps is not None else _load_playwright_deps()

    @property
    def available(self) -> bool:
        return self._deps is not None

    def render(self, *, url: str, timeout_seconds: float) -> dict[str, object]:
        result = self.render_pages(urls=(url,), timeout_seconds=timeout_seconds)
        evidence = tuple(result.get("evidence", ()) or ())
        if not evidence:
            raise RuntimeError("Browser render returned no evidence")
        first = evidence[0]
        return {
            "url": str(first.get("url") or url),
            "status_code": int(first.get("status_code") or result.get("status_code") or 0),
            "headers": {},
            "title": str(first.get("title") or ""),
            "timestamp": str(first.get("timestamp") or ""),
            "snippets": tuple(str(item) for item in first.get("snippets", ()) or ()),
            "evidence": evidence,
            "text": str(result.get("text") or ""),
            "page_count": int(result.get("page_count") or len(evidence)),
            "max_pages": self._max_pages,
        }

    def render_pages(self, *, urls: tuple[str, ...] | list[str], timeout_seconds: float) -> dict[str, object]:
        bounded_urls = tuple(urls[: self._max_pages])
        if not bounded_urls:
            raise ValueError("No browser URLs provided")
        parsed_urls = tuple(_validate_browser_target_url(url) for url in bounded_urls)
        deps = self._deps
        if deps is None:
            raise RuntimeError("Browser provider unavailable")
        time_budget = min(max(float(timeout_seconds), 0.2), self._time_budget_seconds)
        deadline = time.monotonic() + time_budget

        launch_kwargs: dict[str, Any] = {"headless": True}
        executable_path = _detect_system_chromium_executable()
        if executable_path:
            launch_kwargs["executable_path"] = executable_path

        evidence: list[dict[str, object]] = []
        try:
            with deps.sync_playwright() as p:
                browser = p.chromium.launch(**launch_kwargs)
                try:
                    context = browser.new_context(ignore_https_errors=False)
                    try:
                        for parsed in parsed_urls:
                            remaining_seconds = deadline - time.monotonic()
                            if remaining_seconds <= 0:
                                if not evidence:
                                    raise TimeoutError("Browser render timed out")
                                break
                            page = context.new_page()
                            _install_bounded_browser_routes(page)
                            timeout_ms = max(200, int(remaining_seconds * 1000))
                            response = page.goto(parsed.geturl(), wait_until="domcontentloaded", timeout=timeout_ms)
                            body_text = page.locator("body").inner_text(timeout=timeout_ms)
                            title = str(page.title() or "") if hasattr(page, "title") else ""
                            status_code = response.status if response is not None else 200
                            evidence.append(
                                {
                                    "url": parsed.geturl(),
                                    "title": _bound_text(title, 200),
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                    "status_code": int(status_code),
                                    "snippets": _extract_browser_snippets(body_text),
                                }
                            )
                    finally:
                        context.close()
                finally:
                    browser.close()
            text = _format_browser_evidence_text(tuple(evidence), max_output_chars=self._max_output_chars)
            status_code = int(evidence[0].get("status_code") or 0) if evidence else 0
            return {
                "url": str(evidence[0].get("url") or parsed_urls[0].geturl()) if evidence else parsed_urls[0].geturl(),
                "status_code": status_code,
                "headers": {},
                "evidence": tuple(evidence),
                "text": text,
                "page_count": len(evidence),
                "max_pages": self._max_pages,
            }
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
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("Only HTTP(S) URLs are allowed")
    if not parsed.hostname:
        raise ValueError("Invalid host")
    if parsed.username or parsed.password:
        raise ValueError("URL credentials are not allowed")

    host = parsed.hostname.strip().lower().rstrip(".")
    if host in {"localhost", "localhost.localdomain"}:
        raise ValueError("Host not allowed")

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        if _is_private_or_internal_ip(ip):
            raise ValueError("Host not allowed")
        return parsed

    try:
        default_port = 443 if parsed.scheme.lower() == "https" else 80
        infos = socket.getaddrinfo(host, parsed.port or default_port, proto=socket.IPPROTO_TCP)
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


def _install_bounded_browser_routes(page: Any) -> None:
    if hasattr(page, "add_init_script"):
        page.add_init_script(
            """
            document.addEventListener('submit', (event) => {
              event.preventDefault();
              event.stopImmediatePropagation();
            }, true);
            """
        )

    if not hasattr(page, "route"):
        return

    def _handler(route: Any, request: Any) -> None:
        method = str(getattr(request, "method", "GET") or "GET").upper()
        request_url = str(getattr(request, "url", "") or "")
        try:
            _validate_browser_target_url(request_url)
        except Exception:
            route.abort()
            return
        if method not in {"GET", "HEAD", "OPTIONS"}:
            route.abort()
            return
        route.continue_()

    page.route("**/*", _handler)


def _extract_browser_snippets(text: str) -> tuple[str, ...]:
    normalized = " ".join((text or "").split()).strip()
    if not normalized:
        return ()
    candidates = re.split(r"(?<=[.!?])\s+", normalized)
    snippets: list[str] = []
    for candidate in candidates:
        snippet = _bound_text(candidate, RealBrowserProviderAdapter._MAX_SNIPPET_CHARS)
        if not snippet:
            continue
        snippets.append(snippet)
        if len(snippets) >= RealBrowserProviderAdapter._MAX_SNIPPETS_PER_PAGE:
            break
    if snippets:
        return tuple(snippets)
    return (_bound_text(normalized, RealBrowserProviderAdapter._MAX_SNIPPET_CHARS),)


def _format_browser_evidence_text(evidence: tuple[dict[str, object], ...], *, max_output_chars: int) -> str:
    lines: list[str] = []
    for index, item in enumerate(evidence, 1):
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        timestamp = str(item.get("timestamp") or "").strip()
        lines.append(f"{index}. {title or url} ({timestamp})")
        for snippet in tuple(item.get("snippets", ()) or ())[
            : RealBrowserProviderAdapter._MAX_SNIPPETS_PER_PAGE
        ]:
            lines.append(f"- {str(snippet)}")
    return _bound_text("\n".join(lines), max_output_chars)


def _detect_system_chromium_executable() -> str | None:
    for binary_name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        executable = shutil.which(binary_name)
        if executable:
            return executable
    return None


def _is_private_or_internal_ip(ip: ipaddress._BaseAddress) -> bool:
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


_DEFAULT_WEBSCRAPING_POLICY = WebscrapingPolicyConfig(
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


def _default_webscraping_policy() -> WebscrapingPolicyConfig:
    return _DEFAULT_WEBSCRAPING_POLICY


def _policy_for_single_public_url(url: str, *, timeout_seconds: float) -> WebscrapingPolicyConfig:
    parsed = _validate_browser_target_url(url)
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    bounded_timeout = min(max(float(timeout_seconds), 0.5), 12.0)
    return WebscrapingPolicyConfig(
        enabled=True,
        allow_local_hosts=False,
        allowlist_hosts=frozenset({host}),
        timeout_seconds=bounded_timeout,
        max_response_bytes=1_000_000,
        max_output_chars=4000,
        allowed_mime_prefixes=("text/html", "application/xhtml+xml"),
        enforce_robots=False,
        robots_disallow_prefixes=(),
    )


def _default_static_fetch_headers(overrides: dict[str, str] | None = None) -> dict[str, str]:
    headers = {
        "User-Agent": os.getenv("AMO_WEBSCRAPE_USER_AGENT", RealWebscrapeProviderAdapter._DEFAULT_UA).strip()
        or RealWebscrapeProviderAdapter._DEFAULT_UA,
        "Accept": os.getenv("AMO_WEBSCRAPE_ACCEPT", RealWebscrapeProviderAdapter._DEFAULT_ACCEPT).strip()
        or RealWebscrapeProviderAdapter._DEFAULT_ACCEPT,
        "Accept-Language": os.getenv("AMO_WEBSCRAPE_ACCEPT_LANGUAGE", RealWebscrapeProviderAdapter._DEFAULT_ACCEPT_LANGUAGE).strip()
        or RealWebscrapeProviderAdapter._DEFAULT_ACCEPT_LANGUAGE,
    }
    if overrides:
        headers.update({str(key): str(value) for key, value in overrides.items() if str(key).strip() and str(value).strip()})
    return headers


def _map_webscraping_result(result: Any, original_url: str) -> dict[str, object]:
    if result.result.value == "allow":
        return {
            "url": original_url,
            "status_code": 200,
            "headers": {},
            "text": result.extracted_text or "",
        }
    status_raw = (getattr(result, "audit_payload", {}) or {}).get("status_code")
    try:
        status_code = int(status_raw) if status_raw is not None and str(status_raw).isdigit() else 0
    except (TypeError, ValueError):
        status_code = 0
    return {
        "url": original_url,
        "status_code": status_code,
        "headers": {},
        "text": "",
        "reason_code": getattr(result, "reason_code", "fetch_failed"),
    }
