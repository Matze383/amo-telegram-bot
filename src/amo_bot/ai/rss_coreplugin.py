from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

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
