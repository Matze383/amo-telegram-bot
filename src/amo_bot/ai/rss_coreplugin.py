from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from ipaddress import ip_address
from time import monotonic
from typing import Protocol
from urllib.parse import urlparse
import socket
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
    audit: dict[str, object]


@dataclass(frozen=True, slots=True)
class RSSFetchRequest:
    feed_id: str
    url: str
    allowed_urls: frozenset[str]
    min_interval_seconds: int
    timeout_seconds: float
    max_response_bytes: int
    max_entries: int = 20
    plugin_id: str = ""
    max_redirects: int = 5
    host_allowlist: frozenset[str] | None = None


@dataclass(frozen=True, slots=True)
class RSSHTTPResponse:
    status_code: int
    body: bytes
    redirects: int = 0


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
    resolve_host_ips: callable | None = None,
) -> RSSFetchResult:
    audit = _new_audit(request=request)
    started = monotonic()

    def _deny(reason: str, *, blocked_reason: str | None = None, bytes_count: int = 0, items_count: int = 0) -> RSSFetchResult:
        audit.update(
            {
                "status": "deny",
                "reason": reason,
                "duration_ms": int((monotonic() - started) * 1000),
                "bytes": bytes_count,
                "items_count": items_count,
                "blocked_reason": blocked_reason,
            }
        )
        return RSSFetchResult(result=CapabilityDecisionResult.DENY, reason_code=reason, entries=(), audit=audit)

    def _allow(entries: tuple[RSSFeedEntry, ...], *, bytes_count: int) -> RSSFetchResult:
        audit.update(
            {
                "status": "allow",
                "reason": "ok",
                "duration_ms": int((monotonic() - started) * 1000),
                "bytes": bytes_count,
                "items_count": len(entries),
                "blocked_reason": None,
            }
        )
        return RSSFetchResult(result=CapabilityDecisionResult.ALLOW, reason_code="ok", entries=entries, audit=audit)

    validation = validate_rss_input(feed_id=request.feed_id, url=request.url)
    if not validation.ok:
        return _deny(validation.reason_code)

    allowed_urls = {_normalize_url(item) for item in request.allowed_urls if isinstance(item, str) and item.strip()}
    if _normalize_url(request.url) not in allowed_urls:
        return _deny("url_not_allowlisted")

    host = (urlparse(request.url).hostname or "").strip().lower()
    if not host:
        return _deny("invalid_url")
    audit["url_host"] = host

    if request.host_allowlist is not None and host not in {h.strip().lower() for h in request.host_allowlist if isinstance(h, str)}:
        return _deny("host_not_allowlisted", blocked_reason="policy_host_allowlist")

    blocked_reason = _block_reason_for_host(host=host, resolve_host_ips=resolve_host_ips)
    if blocked_reason is not None:
        return _deny("ssrf_blocked", blocked_reason=blocked_reason)

    if request.min_interval_seconds < 1:
        return _deny("invalid_min_interval")
    if request.timeout_seconds <= 0:
        return _deny("invalid_timeout")
    if request.max_response_bytes < 1024:
        return _deny("invalid_max_response_bytes")
    if request.max_entries < 1:
        return _deny("invalid_max_entries")
    if request.max_redirects < 0:
        return _deny("invalid_max_redirects")

    if last_fetch_monotonic_seconds is not None:
        elapsed = now_monotonic_seconds - last_fetch_monotonic_seconds
        if elapsed < request.min_interval_seconds:
            return _deny("rate_limited")

    try:
        response = http_get(request.url, request.timeout_seconds)
    except TimeoutError:
        return _deny("fetch_timeout")
    except Exception:
        return _deny("fetch_failed")

    if response.redirects > request.max_redirects:
        return _deny("redirect_limit_exceeded", blocked_reason="max_redirects")

    if response.status_code < 200 or response.status_code >= 300:
        return _deny("http_error")

    if len(response.body) > request.max_response_bytes:
        return _deny("response_too_large", bytes_count=len(response.body))

    try:
        root = ET.fromstring(response.body)
    except ET.ParseError:
        return _deny("malformed_xml", bytes_count=len(response.body))

    entries = _parse_rss_entries(root=root, max_entries=request.max_entries)
    return _allow(entries, bytes_count=len(response.body))


def _normalize_url(url: str) -> str:
    return url.strip()


def _new_audit(*, request: RSSFetchRequest) -> dict[str, object]:
    host = (urlparse(request.url).hostname or "").strip().lower()
    return {
        "plugin_id": request.plugin_id or "unknown",
        "url_host": host,
        "status": "deny",
        "reason": "unknown",
        "duration_ms": 0,
        "bytes": 0,
        "items_count": 0,
        "blocked_reason": None,
    }


def _block_reason_for_host(*, host: str, resolve_host_ips: callable | None) -> str | None:
    if host in {"localhost", "localhost.localdomain", "0", "0.0.0.0"}:
        return "localhost"

    if _is_ip_blocked(host):
        return "ip_literal_blocked"

    resolver = resolve_host_ips or _resolve_host_ips
    try:
        ips = resolver(host)
    except Exception:
        return None

    for ip_text in ips:
        if _is_ip_blocked(ip_text):
            return "resolved_ip_blocked"
    return None


def _resolve_host_ips(host: str) -> tuple[str, ...]:
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    out: list[str] = []
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        ip_text = sockaddr[0]
        if ip_text not in out:
            out.append(ip_text)
    return tuple(out)


def _is_ip_blocked(host_or_ip: str) -> bool:
    try:
        addr = ip_address(host_or_ip)
    except ValueError:
        return False

    if addr.is_loopback or addr.is_link_local or addr.is_private:
        return True
    if addr.is_multicast or addr.is_unspecified or addr.is_reserved:
        return True
    return False


def _parse_rss_entries(*, root: ET.Element, max_entries: int) -> tuple[RSSFeedEntry, ...]:
    items = list(root.findall("./channel/item"))
    entries = list(root.findall("./{http://www.w3.org/2005/Atom}entry"))
    seen_keys: set[str] = set()
    normalized: list[RSSFeedEntry] = []

    for item in items:
        parsed = _parse_rss_item(item)
        _append_entry(parsed=parsed, normalized=normalized, seen_keys=seen_keys)
        if len(normalized) >= max_entries:
            break

    if len(normalized) < max_entries:
        for entry in entries:
            parsed = _parse_atom_entry(entry)
            _append_entry(parsed=parsed, normalized=normalized, seen_keys=seen_keys)
            if len(normalized) >= max_entries:
                break

    return tuple(normalized)


def _parse_rss_item(item: ET.Element) -> tuple[str, str, str, str, str]:
    title = _text_or_empty(item.find("title"))
    link = _text_or_empty(item.find("link"))
    guid = _text_or_empty(item.find("guid"))
    published = _text_or_empty(item.find("pubDate"))
    summary = _text_or_empty(item.find("description"))
    return title, link, guid, published, summary


def _parse_atom_entry(entry: ET.Element) -> tuple[str, str, str, str, str]:
    atom_ns = "{http://www.w3.org/2005/Atom}"
    title = _text_or_empty(entry.find(f"{atom_ns}title"))
    entry_id = _text_or_empty(entry.find(f"{atom_ns}id"))
    published = _text_or_empty(entry.find(f"{atom_ns}published")) or _text_or_empty(entry.find(f"{atom_ns}updated"))
    summary = _text_or_empty(entry.find(f"{atom_ns}summary")) or _text_or_empty(entry.find(f"{atom_ns}content"))

    link = ""
    for link_node in entry.findall(f"{atom_ns}link"):
        href = (link_node.attrib.get("href") or "").strip()
        if not href:
            continue
        rel = (link_node.attrib.get("rel") or "alternate").strip().lower()
        if rel == "alternate":
            link = href
            break
        if not link:
            link = href

    return title, link, entry_id, published, summary


def _append_entry(
    *, parsed: tuple[str, str, str, str, str], normalized: list[RSSFeedEntry], seen_keys: set[str]
) -> None:
    title, link, source_id, published, summary = parsed

    if not (title or link or source_id):
        return

    stable_key = "|".join((source_id, link, published)).strip().lower()
    if not stable_key:
        return
    dedupe_key = sha256(stable_key.encode("utf-8", errors="ignore")).hexdigest()
    if dedupe_key in seen_keys:
        return
    seen_keys.add(dedupe_key)

    entry_id = source_id or dedupe_key
    normalized.append(
        RSSFeedEntry(
            id=entry_id[:256],
            title=title[:512],
            link=link[:2048],
            published=published[:256],
            summary=summary[:2048],
        )
    )


def _text_or_empty(node: ET.Element | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.strip()
