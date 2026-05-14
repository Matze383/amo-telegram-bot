from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Callable, Protocol
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

from .capability_policy import CapabilityDecisionResult

_MAX_FEED_ID_LENGTH = 128
_MAX_URL_LENGTH = 2048
_ALLOWED_SCHEMES = {"http", "https"}


@dataclass(frozen=True, slots=True)
class RSSInputValidationResult:
    ok: bool
    reason_code: str


@dataclass(frozen=True, slots=True)
class RSSNoOpResult:
    result: CapabilityDecisionResult
    reason_code: str


@dataclass(frozen=True, slots=True)
class RSSFeedEntry:
    id: str
    title: str
    link: str
    published: str
    summary: str


@dataclass(frozen=True, slots=True)
class RSSFetchResult:
    result: CapabilityDecisionResult
    reason_code: str
    entries: tuple[RSSFeedEntry, ...]


@dataclass(frozen=True, slots=True)
class RSSFetchRequest:
    feed_id: str
    url: str
    allowed_urls: frozenset[str]
    min_interval_seconds: int
    timeout_seconds: float
    max_response_bytes: int
    max_entries: int = 20


@dataclass(frozen=True, slots=True)
class RSSHTTPResponse:
    status_code: int
    body: bytes


class RSSHTTPClient(Protocol):
    def __call__(self, url: str, timeout_seconds: float) -> RSSHTTPResponse: ...


def validate_rss_input(*, feed_id: str, url: str) -> RSSInputValidationResult:
    if not isinstance(feed_id, str) or not feed_id.strip():
        return RSSInputValidationResult(ok=False, reason_code="invalid_feed_id")
    normalized_feed_id = feed_id.strip()
    if len(normalized_feed_id) > _MAX_FEED_ID_LENGTH:
        return RSSInputValidationResult(ok=False, reason_code="invalid_feed_id")
    if any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for ch in normalized_feed_id):
        return RSSInputValidationResult(ok=False, reason_code="invalid_feed_id")

    if not isinstance(url, str):
        return RSSInputValidationResult(ok=False, reason_code="invalid_url")
    normalized_url = url.strip()
    if not normalized_url or len(normalized_url) > _MAX_URL_LENGTH:
        return RSSInputValidationResult(ok=False, reason_code="invalid_url")

    parsed = urlparse(normalized_url)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        return RSSInputValidationResult(ok=False, reason_code="invalid_url")
    if not parsed.netloc:
        return RSSInputValidationResult(ok=False, reason_code="invalid_url")

    return RSSInputValidationResult(ok=True, reason_code="ok")


def execute_rss_noop(*, feed_id: str, url: str) -> RSSNoOpResult:
    validation = validate_rss_input(feed_id=feed_id, url=url)
    if not validation.ok:
        return RSSNoOpResult(result=CapabilityDecisionResult.DENY, reason_code=validation.reason_code)
    return RSSNoOpResult(result=CapabilityDecisionResult.DENY, reason_code="not_enabled")


def execute_rss_fetch(
    *,
    request: RSSFetchRequest,
    http_get: RSSHTTPClient,
    now_monotonic_seconds: float,
    last_fetch_monotonic_seconds: float | None,
) -> RSSFetchResult:
    validation = validate_rss_input(feed_id=request.feed_id, url=request.url)
    if not validation.ok:
        return RSSFetchResult(result=CapabilityDecisionResult.DENY, reason_code=validation.reason_code, entries=())

    allowed_urls = {_normalize_url(item) for item in request.allowed_urls if isinstance(item, str) and item.strip()}
    if _normalize_url(request.url) not in allowed_urls:
        return RSSFetchResult(result=CapabilityDecisionResult.DENY, reason_code="url_not_allowlisted", entries=())

    if request.min_interval_seconds < 1:
        return RSSFetchResult(result=CapabilityDecisionResult.DENY, reason_code="invalid_min_interval", entries=())
    if request.timeout_seconds <= 0:
        return RSSFetchResult(result=CapabilityDecisionResult.DENY, reason_code="invalid_timeout", entries=())
    if request.max_response_bytes < 1024:
        return RSSFetchResult(result=CapabilityDecisionResult.DENY, reason_code="invalid_max_response_bytes", entries=())
    if request.max_entries < 1:
        return RSSFetchResult(result=CapabilityDecisionResult.DENY, reason_code="invalid_max_entries", entries=())

    if last_fetch_monotonic_seconds is not None:
        elapsed = now_monotonic_seconds - last_fetch_monotonic_seconds
        if elapsed < request.min_interval_seconds:
            return RSSFetchResult(result=CapabilityDecisionResult.DENY, reason_code="rate_limited", entries=())

    try:
        response = http_get(request.url, request.timeout_seconds)
    except TimeoutError:
        return RSSFetchResult(result=CapabilityDecisionResult.DENY, reason_code="fetch_timeout", entries=())
    except Exception:
        return RSSFetchResult(result=CapabilityDecisionResult.DENY, reason_code="fetch_failed", entries=())

    if response.status_code < 200 or response.status_code >= 300:
        return RSSFetchResult(result=CapabilityDecisionResult.DENY, reason_code="http_error", entries=())

    if len(response.body) > request.max_response_bytes:
        return RSSFetchResult(result=CapabilityDecisionResult.DENY, reason_code="response_too_large", entries=())

    try:
        root = ET.fromstring(response.body)
    except ET.ParseError:
        return RSSFetchResult(result=CapabilityDecisionResult.DENY, reason_code="malformed_xml", entries=())

    entries = _parse_rss_entries(root=root, max_entries=request.max_entries)
    return RSSFetchResult(result=CapabilityDecisionResult.ALLOW, reason_code="ok", entries=entries)


def _normalize_url(url: str) -> str:
    return url.strip()


def _parse_rss_entries(*, root: ET.Element, max_entries: int) -> tuple[RSSFeedEntry, ...]:
    items = list(root.findall("./channel/item"))
    seen_keys: set[str] = set()
    normalized: list[RSSFeedEntry] = []

    for item in items:
        title = _text_or_empty(item.find("title"))
        link = _text_or_empty(item.find("link"))
        guid = _text_or_empty(item.find("guid"))
        published = _text_or_empty(item.find("pubDate"))
        summary = _text_or_empty(item.find("description"))

        if not (title or link or guid):
            continue

        stable_key = "|".join((guid, link, published)).strip().lower()
        if not stable_key:
            continue
        dedupe_key = sha256(stable_key.encode("utf-8", errors="ignore")).hexdigest()
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)

        entry_id = guid or dedupe_key
        normalized.append(
            RSSFeedEntry(
                id=entry_id[:256],
                title=title[:512],
                link=link[:2048],
                published=published[:256],
                summary=summary[:2048],
            )
        )
        if len(normalized) >= max_entries:
            break

    return tuple(normalized)


def _text_or_empty(node: ET.Element | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.strip()
