from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import re
import socket
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.message import Message
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from amo_bot.ai.research_extraction_quality import classify_extraction_quality
from amo_bot.current_info.models import FetchedDocument, JsonDict
from amo_bot.current_info.observability import (
    GLOBAL_HOST_CONCURRENCY_LIMITER,
    CurrentInfoBudgetExceeded,
    CurrentInfoSafetyConfig,
)


_DEFAULT_ALLOWED_MIME_TYPES = (
    "text/html",
    "application/xhtml+xml",
    "text/plain",
)
_DEFAULT_USER_AGENT = "AMOCurrentInfoFetcher/1.0"
_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class DocumentFetchConfig:
    timeout_seconds: float = 5.0
    max_bytes: int = 1_000_000
    max_redirects: int = 3
    allowed_mime_types: tuple[str, ...] = _DEFAULT_ALLOWED_MIME_TYPES
    prefer_crawlee: bool = True
    block_private_ips: bool = True
    user_agent: str = _DEFAULT_USER_AGENT
    crawlee_max_concurrent_per_host: int = 2


@dataclass(frozen=True, slots=True)
class _RawFetchResult:
    url: str
    status_code: int
    headers: dict[str, str]
    content: bytes
    provider: str


class CurrentInfoFetchError(RuntimeError):
    reason_code = "fetch_error"


class CurrentInfoFetchBlocked(CurrentInfoFetchError):
    reason_code = "blocked"


class CurrentInfoFetchTimeout(CurrentInfoFetchError):
    reason_code = "timeout"


class CurrentInfoFetchTooLarge(CurrentInfoFetchError):
    reason_code = "response_too_large"


class CurrentInfoFetchInvalidMime(CurrentInfoFetchError):
    reason_code = "invalid_mime_type"


class CrawleeDocumentFetcher:
    """Current-Info document fetcher with Crawlee primary path and httpx fallback."""

    name = "crawlee"

    def __init__(self, config: DocumentFetchConfig | None = None, *, http_client_factory: Any = None) -> None:
        self._config = config or DocumentFetchConfig()
        self._http_client_factory = http_client_factory or httpx.Client

    def fetch(self, *, url: str, locale: str) -> FetchedDocument | None:
        del locale
        try:
            result = self._fetch_raw(url)
        except CurrentInfoFetchError:
            return None
        except httpx.TimeoutException:
            return None
        except httpx.HTTPError:
            return None

        if result.status_code >= 400:
            return None

        document = extract_document(
            content=result.content,
            url=result.url,
            status_code=result.status_code,
            headers=result.headers,
            provider=result.provider,
        )
        if not document.text.strip():
            return None
        return document

    def _fetch_raw(self, url: str) -> _RawFetchResult:
        current_url = _validate_fetch_url(url, block_private_ips=self._config.block_private_ips)
        redirects = 0

        while True:
            result = self._fetch_once(current_url)
            if result.status_code not in {301, 302, 303, 307, 308}:
                return result
            if redirects >= self._config.max_redirects:
                raise CurrentInfoFetchBlocked("redirect_limit_exceeded")
            location = result.headers.get("location", "").strip()
            if not location:
                return result
            redirects += 1
            current_url = _validate_fetch_url(
                urljoin(current_url, location),
                block_private_ips=self._config.block_private_ips,
            )

    def _fetch_once(self, url: str) -> _RawFetchResult:
        if self._config.prefer_crawlee:
            result = self._fetch_once_with_crawlee(url)
            if result is not None:
                return result
        return self._fetch_once_with_httpx(url)

    def _fetch_once_with_crawlee(self, url: str) -> _RawFetchResult | None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            return None

        try:
            with GLOBAL_HOST_CONCURRENCY_LIMITER.acquire(
                url,
                limit=self._config.crawlee_max_concurrent_per_host,
            ):
                return asyncio.run(self._async_fetch_once_with_crawlee(url))
        except ModuleNotFoundError:
            return None
        except ImportError:
            return None
        except CurrentInfoBudgetExceeded as exc:
            raise CurrentInfoFetchBlocked(exc.reason_code) from exc

    async def _async_fetch_once_with_crawlee(self, url: str) -> _RawFetchResult:
        from crawlee.http_clients import HttpxHttpClient

        headers = self._headers()
        client = HttpxHttpClient(follow_redirects=False, timeout=self._config.timeout_seconds)
        try:
            response = await client.send_request(
                url,
                headers=headers,
                timeout=timedelta(seconds=self._config.timeout_seconds),
            )
            response_headers = {str(key).lower(): str(value) for key, value in response.headers.items()}
            self._check_content_length(response_headers)
            self._check_mime_type(response_headers)
            content = await self._read_crawlee_response(response)
            return _RawFetchResult(
                url=url,
                status_code=int(response.status_code),
                headers=response_headers,
                content=content,
                provider="crawlee",
            )
        finally:
            await client.cleanup()

    def _fetch_once_with_httpx(self, url: str) -> _RawFetchResult:
        with self._http_client_factory(
            timeout=self._config.timeout_seconds,
            follow_redirects=False,
            headers=self._headers(),
        ) as client:
            if hasattr(client, "stream"):
                with client.stream("GET", url) as response:
                    headers = {key.lower(): value for key, value in response.headers.items()}
                    self._check_content_length(headers)
                    self._check_mime_type(headers)
                    content = self._read_httpx_response(response)
                    return _RawFetchResult(
                        url=str(response.url),
                        status_code=response.status_code,
                        headers=headers,
                        content=content,
                        provider="httpx",
                    )

            response = client.get(url)

        headers = {key.lower(): value for key, value in response.headers.items()}
        self._check_content_length(headers)
        self._check_mime_type(headers)
        content = response.content
        self._check_size(content)
        return _RawFetchResult(
            url=str(response.url),
            status_code=response.status_code,
            headers=headers,
            content=content,
            provider="httpx",
        )

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.8",
            "Accept-Language": "en",
            "User-Agent": self._config.user_agent,
        }

    def _check_content_length(self, headers: dict[str, str]) -> None:
        raw = headers.get("content-length", "").strip()
        if not raw:
            return
        try:
            length = int(raw)
        except ValueError:
            return
        if length > self._config.max_bytes:
            raise CurrentInfoFetchTooLarge("content_length_exceeds_limit")

    def _check_mime_type(self, headers: dict[str, str]) -> None:
        content_type = headers.get("content-type", "")
        mime_type = _mime_type(content_type)
        if not mime_type:
            return
        allowed = {item.casefold() for item in self._config.allowed_mime_types}
        if mime_type.casefold() not in allowed:
            raise CurrentInfoFetchInvalidMime("mime_type_not_allowed")

    def _check_size(self, content: bytes) -> None:
        if len(content) > self._config.max_bytes:
            raise CurrentInfoFetchTooLarge("body_exceeds_limit")

    async def _read_crawlee_response(self, response: Any) -> bytes:
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.read_stream():
            total += len(chunk)
            if total > self._config.max_bytes:
                raise CurrentInfoFetchTooLarge("body_exceeds_limit")
            chunks.append(chunk)
        return b"".join(chunks)

    def _read_httpx_response(self, response: httpx.Response) -> bytes:
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_bytes():
            total += len(chunk)
            if total > self._config.max_bytes:
                raise CurrentInfoFetchTooLarge("body_exceeds_limit")
            chunks.append(chunk)
        return b"".join(chunks)


def build_document_fetcher_from_settings(settings: Any, *, http_client_factory: Any = None) -> CrawleeDocumentFetcher:
    safety_config = CurrentInfoSafetyConfig(
        crawlee_max_concurrent_per_host=int(getattr(settings, "amo_crawlee_max_concurrent_per_host", 2)),
    )
    return CrawleeDocumentFetcher(
        DocumentFetchConfig(
            timeout_seconds=float(getattr(settings, "amo_document_fetch_timeout_seconds", 5.0)),
            max_bytes=int(getattr(settings, "amo_document_fetch_max_bytes", 1_000_000)),
            max_redirects=int(getattr(settings, "amo_document_fetch_max_redirects", 3)),
            prefer_crawlee=bool(getattr(settings, "amo_document_fetch_prefer_crawlee", True)),
            crawlee_max_concurrent_per_host=safety_config.crawlee_max_concurrent_per_host,
        ),
        http_client_factory=http_client_factory,
    )


def extract_document(
    *,
    content: bytes,
    url: str,
    status_code: int | None = None,
    headers: dict[str, str] | None = None,
    provider: str = "crawlee",
) -> FetchedDocument:
    headers = {key.lower(): value for key, value in (headers or {}).items()}
    content_type = headers.get("content-type", "")
    charset = _charset(content_type) or "utf-8"
    text = content.decode(charset, errors="replace")
    mime_type = _mime_type(content_type)

    if mime_type == "text/plain":
        title = ""
        main_text = _normalize_text(text)
        metadata: JsonDict = {}
        canonical_url = url
    else:
        parser = _DocumentHTMLParser(base_url=url)
        parser.feed(text)
        parser.close()
        title = parser.title or parser.metadata.get("og:title", "") or parser.metadata.get("twitter:title", "")
        main_text = parser.main_text()
        metadata = dict(parser.metadata)
        canonical_url = parser.canonical_url or url

    quality = classify_extraction_quality(main_text)
    published_at = _first_metadata_value(
        metadata,
        "article:published_time",
        "og:published_time",
        "datePublished",
        "date",
        "pubdate",
    )
    modified_at = _first_metadata_value(
        metadata,
        "article:modified_time",
        "og:updated_time",
        "dateModified",
        "last-modified",
    ) or headers.get("last-modified", "")
    if headers.get("last-modified") and "last-modified" not in metadata:
        metadata["last-modified"] = headers["last-modified"]

    metadata.update(
        {
            "final_url": url,
            "canonical_url": canonical_url,
            "content_hash": hashlib.sha256(content).hexdigest(),
            "mime_type": mime_type,
            "published_at": published_at,
            "modified_at": modified_at,
            "extraction_quality": {
                "usable": quality.usable,
                "warning_codes": quality.warning_codes,
                "text_length": quality.text_length,
                "text_length_bucket": quality.text_length_bucket,
            },
        }
    )

    return FetchedDocument(
        url=canonical_url,
        text=main_text,
        title=_normalize_text(title),
        fetched_at=datetime.now(UTC).isoformat(),
        status_code=status_code,
        provider=provider,
        metadata=metadata,
    )


class _DocumentHTMLParser(HTMLParser):
    def __init__(self, *, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self._base_url = base_url
        self._skip_depth = 0
        self._title_depth = 0
        self._text_parts: list[str] = []
        self._title_parts: list[str] = []
        self._body_seen = False
        self._main_depth = 0
        self._article_depth = 0
        self._preferred_parts: list[str] = []
        self.title = ""
        self.canonical_url = ""
        self.metadata: JsonDict = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attrs_map = {key.lower(): (value or "") for key, value in attrs}
        if tag in {"script", "style", "noscript", "svg", "nav", "footer", "header", "form"}:
            self._skip_depth += 1
            return
        if tag == "body":
            self._body_seen = True
        if tag == "title":
            self._title_depth += 1
        if tag == "main":
            self._main_depth += 1
        if tag == "article":
            self._article_depth += 1
        if tag == "link" and "canonical" in attrs_map.get("rel", "").casefold():
            href = attrs_map.get("href", "").strip()
            if href:
                self.canonical_url = urljoin(self._base_url, href)
        if tag == "meta":
            key = attrs_map.get("property") or attrs_map.get("name") or attrs_map.get("itemprop")
            value = attrs_map.get("content", "").strip()
            if key and value:
                self.metadata.setdefault(key, value)
        if tag in {"p", "div", "section", "br", "li", "h1", "h2", "h3"}:
            self._append_text(" ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._skip_depth > 0 and tag in {"script", "style", "noscript", "svg", "nav", "footer", "header", "form"}:
            self._skip_depth -= 1
            return
        if tag == "title" and self._title_depth:
            self._title_depth -= 1
            self.title = _normalize_text(" ".join(self._title_parts))
        if tag == "main" and self._main_depth:
            self._main_depth -= 1
        if tag == "article" and self._article_depth:
            self._article_depth -= 1
        if tag in {"p", "div", "section", "li", "h1", "h2", "h3"}:
            self._append_text(" ")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = unescape(data)
        if self._title_depth:
            self._title_parts.append(text)
            return
        if not self._body_seen:
            return
        self._append_text(text)

    def main_text(self) -> str:
        preferred = _normalize_text(" ".join(self._preferred_parts))
        if len(preferred) >= 80:
            return preferred
        return _normalize_text(" ".join(self._text_parts))

    def _append_text(self, text: str) -> None:
        self._text_parts.append(text)
        if self._main_depth or self._article_depth:
            self._preferred_parts.append(text)


def _validate_fetch_url(url: str, *, block_private_ips: bool) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise CurrentInfoFetchBlocked("unsupported_url_scheme")
    if not parsed.hostname:
        raise CurrentInfoFetchBlocked("missing_url_host")
    if parsed.username or parsed.password:
        raise CurrentInfoFetchBlocked("url_credentials_not_allowed")

    host = parsed.hostname.rstrip(".")
    if _is_blocked_host(host, block_private_ips=block_private_ips):
        raise CurrentInfoFetchBlocked("private_host_blocked")
    return url


def _is_blocked_host(host: str, *, block_private_ips: bool) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None

    if ip is not None:
        return block_private_ips and _is_private_or_special_ip(ip)

    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise CurrentInfoFetchBlocked("host_resolution_failed") from exc

    for info in infos:
        address = info[4][0]
        try:
            resolved_ip = ipaddress.ip_address(address)
        except ValueError:
            raise CurrentInfoFetchBlocked("host_resolution_invalid")
        if block_private_ips and _is_private_or_special_ip(resolved_ip):
            return True
    return False


def _is_private_or_special_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _mime_type(content_type: str) -> str:
    if not content_type:
        return ""
    message = Message()
    message["content-type"] = content_type
    return message.get_content_type().lower()


def _charset(content_type: str) -> str:
    if not content_type:
        return ""
    message = Message()
    message["content-type"] = content_type
    return message.get_param("charset", header="content-type") or ""


def _normalize_text(value: str) -> str:
    return _WHITESPACE_RE.sub(" ", value or "").strip()


def _first_metadata_value(metadata: JsonDict, *keys: str) -> str:
    lowered = {key.casefold(): value for key, value in metadata.items()}
    for key in keys:
        value = lowered.get(key.casefold())
        if value:
            return str(value)
    return ""
