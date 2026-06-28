from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse

from .capability_audit import CapabilityAuditTrail
from .capability_policy import CapabilityDecisionResult
from .capability_quota import CapabilityQuotaRequest
from .capability_policy import CapabilityActorType, CapabilityScopeType

_MAX_QUERY_LENGTH = 256
_MAX_LOCALE_LENGTH = 16
_MAX_TITLE_LENGTH = 200
_MAX_SNIPPET_LENGTH = 400
_MAX_URL_LENGTH = 2048
_ALLOWED_SAFESEARCH = {"off", "moderate", "strict"}
_DEFAULT_PROVIDER_RESULT_CAP = 10
_MAX_PROVIDER_RESULT_CAP = 10
_DEFAULT_PROVIDER_ALLOWLIST = frozenset({"fake"})
_DEFAULT_TIMEOUT_SECONDS = 1.0
_DEFAULT_RETRY_COUNT = 1
_MAX_RETRY_COUNT = 3


@dataclass(frozen=True, slots=True)
class WebsearchInput:
    query: str
    locale: str = "en"
    safesearch: str = "moderate"


@dataclass(frozen=True, slots=True)
class WebsearchInputValidationResult:
    ok: bool
    reason_code: str


@dataclass(frozen=True, slots=True)
class WebsearchResultItem:
    title: str
    url: str
    snippet: str


@dataclass(frozen=True, slots=True)
class WebsearchExecutionResult:
    result: CapabilityDecisionResult
    reason_code: str
    results: tuple[WebsearchResultItem, ...]


@dataclass(frozen=True, slots=True)
class WebsearchProviderResult:
    title: str
    url: str
    snippet: str


@dataclass(frozen=True, slots=True)
class WebsearchProviderConfig:
    provider_name: str = "fake"
    provider_allowlist: frozenset[str] = _DEFAULT_PROVIDER_ALLOWLIST
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS
    retry_count: int = _DEFAULT_RETRY_COUNT

    def __post_init__(self) -> None:
        normalized_name = _normalize_provider_name(self.provider_name)
        if not normalized_name:
            raise ValueError("provider_name must not be empty")

        normalized_allowlist = frozenset(
            item for item in (_normalize_provider_name(name) for name in self.provider_allowlist) if item
        )
        if not normalized_allowlist:
            raise ValueError("provider_allowlist must not be empty")

        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

        if self.retry_count < 0 or self.retry_count > _MAX_RETRY_COUNT:
            raise ValueError("retry_count is out of range")

    @property
    def normalized_provider_name(self) -> str:
        return _normalize_provider_name(self.provider_name)


class WebsearchProvider(Protocol):
    def search(self, *, query: str, locale: str, safesearch: str, max_results: int) -> tuple[WebsearchProviderResult, ...]:
        ...


class FakeWebsearchProvider:
    """Deterministic offline fake provider with bounded output only."""

    def search(self, *, query: str, locale: str, safesearch: str, max_results: int) -> tuple[WebsearchProviderResult, ...]:
        if max_results < 1:
            return ()

        seeds = (
            (
                f"{query} – overview",
                f"https://example.test/search/{_slug(query)}",
                f"Deterministic fake overview for '{query}' ({locale}, {safesearch}).",
            ),
            (
                f"{query} – details",
                f"https://example.test/docs/{_slug(query)}",
                f"Deterministic fake details page for '{query}'.",
            ),
            (
                f"{query} – references",
                f"https://example.test/ref/{_slug(query)}",
                "Deterministic fake references for offline validation.",
            ),
            (
                f"{query} – latest",
                f"https://example.test/latest/{_slug(query)}",
                "Deterministic fake latest-source item for offline validation.",
            ),
            (
                f"{query} – background",
                f"https://example.test/background/{_slug(query)}",
                "Deterministic fake background-source item for offline validation.",
            ),
        )
        bounded = seeds[: min(max_results, _MAX_PROVIDER_RESULT_CAP)]
        return tuple(
            WebsearchProviderResult(
                title=title[:_MAX_TITLE_LENGTH],
                url=url[:_MAX_URL_LENGTH],
                snippet=snippet[:_MAX_SNIPPET_LENGTH],
            )
            for title, url, snippet in bounded
        )


def validate_websearch_input(request: WebsearchInput) -> WebsearchInputValidationResult:
    if not isinstance(request.query, str):
        return WebsearchInputValidationResult(ok=False, reason_code="invalid_query")
    query = request.query.strip()
    if not query or len(query) > _MAX_QUERY_LENGTH:
        return WebsearchInputValidationResult(ok=False, reason_code="invalid_query")

    if not isinstance(request.locale, str):
        return WebsearchInputValidationResult(ok=False, reason_code="invalid_locale")
    locale = request.locale.strip().lower()
    if not locale or len(locale) > _MAX_LOCALE_LENGTH:
        return WebsearchInputValidationResult(ok=False, reason_code="invalid_locale")
    if not all(ch.isalpha() or ch in {"-", "_"} for ch in locale):
        return WebsearchInputValidationResult(ok=False, reason_code="invalid_locale")

    if not isinstance(request.safesearch, str):
        return WebsearchInputValidationResult(ok=False, reason_code="invalid_safesearch")
    safesearch = request.safesearch.strip().lower()
    if safesearch not in _ALLOWED_SAFESEARCH:
        return WebsearchInputValidationResult(ok=False, reason_code="invalid_safesearch")

    return WebsearchInputValidationResult(ok=True, reason_code="ok")


def execute_websearch_noop(
    *,
    request: WebsearchInput,
    provider: WebsearchProvider,
    audit_trail: CapabilityAuditTrail | None = None,
) -> WebsearchExecutionResult:
    validation = validate_websearch_input(request)
    if not validation.ok:
        return WebsearchExecutionResult(
            result=CapabilityDecisionResult.DENY,
            reason_code=validation.reason_code,
            results=(),
        )

    if audit_trail is not None:
        audit_trail.record_request(
            actor_type="ki",
            actor_id="ki",
            scope_type="topic",
            scope_id="websearch",
            capability_name="ki.websearch.query",
            capability_version="1.0.0",
            summary="websearch_query",
            metadata={
                "query_len": str(len(request.query.strip())),
                "locale": request.locale.strip()[:_MAX_LOCALE_LENGTH],
                "safesearch": request.safesearch.strip().lower()[:16],
            },
        )

    _ = provider
    return WebsearchExecutionResult(
        result=CapabilityDecisionResult.DENY,
        reason_code="not_enabled",
        results=(),
    )


def execute_websearch_fake_allowed(
    *,
    request: WebsearchInput,
    provider: WebsearchProvider,
    max_results: int = _DEFAULT_PROVIDER_RESULT_CAP,
    audit_trail: CapabilityAuditTrail | None = None,
) -> WebsearchExecutionResult:
    validation = validate_websearch_input(request)
    if not validation.ok:
        return WebsearchExecutionResult(
            result=CapabilityDecisionResult.DENY,
            reason_code=validation.reason_code,
            results=(),
        )

    bounded_max = min(max(max_results, 1), _MAX_PROVIDER_RESULT_CAP)
    provider_results = provider.search(
        query=request.query.strip(),
        locale=request.locale.strip().lower(),
        safesearch=request.safesearch.strip().lower(),
        max_results=bounded_max,
    )

    safe_items: list[WebsearchResultItem] = []
    for item in provider_results[:bounded_max]:
        parsed = urlparse(item.url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        safe_items.append(
            WebsearchResultItem(
                title=item.title[:_MAX_TITLE_LENGTH],
                url=item.url[:_MAX_URL_LENGTH],
                snippet=item.snippet[:_MAX_SNIPPET_LENGTH],
            )
        )

    if audit_trail is not None:
        query = request.query.strip()
        request_id = f"websearch_fake_q{len(query)}"[:64]
        audit_trail.record_completed(
            request_id=request_id,
            capability_name="ki.websearch.query",
            capability_version="1.0.0",
        )

    return WebsearchExecutionResult(
        result=CapabilityDecisionResult.ALLOW,
        reason_code="ok",
        results=tuple(safe_items),
    )


def execute_websearch_provider_mvp(
    *,
    request: WebsearchInput,
    provider: WebsearchProvider,
    provider_config: WebsearchProviderConfig,
    quota_limiter,
    audit_trail: CapabilityAuditTrail | None = None,
    max_results: int = _DEFAULT_PROVIDER_RESULT_CAP,
) -> WebsearchExecutionResult:
    validation = validate_websearch_input(request)
    if not validation.ok:
        return WebsearchExecutionResult(result=CapabilityDecisionResult.DENY, reason_code=validation.reason_code, results=())

    normalized_provider = provider_config.normalized_provider_name
    if normalized_provider not in provider_config.provider_allowlist:
        return WebsearchExecutionResult(result=CapabilityDecisionResult.DENY, reason_code="provider_not_allowed", results=())

    quota_decision = quota_limiter.evaluate(_build_websearch_quota_request(provider_name=normalized_provider, query=request.query))
    if not quota_decision.allowed:
        return WebsearchExecutionResult(result=CapabilityDecisionResult.DENY, reason_code=quota_decision.reason_code, results=())

    bounded_max = min(max(max_results, 1), _MAX_PROVIDER_RESULT_CAP)
    attempts = provider_config.retry_count + 1
    failure_reason = "provider_error"
    for _attempt in range(attempts):
        started = time.monotonic()
        try:
            provider_results = provider.search(
                query=request.query.strip(),
                locale=request.locale.strip().lower(),
                safesearch=request.safesearch.strip().lower(),
                max_results=bounded_max,
            )
            if (time.monotonic() - started) > provider_config.timeout_seconds:
                raise TimeoutError("provider timeout")

            safe_items: list[WebsearchResultItem] = []
            for item in provider_results[:bounded_max]:
                parsed = urlparse(item.url)
                if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                    continue
                safe_items.append(
                    WebsearchResultItem(
                        title=item.title[:_MAX_TITLE_LENGTH],
                        url=item.url[:_MAX_URL_LENGTH],
                        snippet=item.snippet[:_MAX_SNIPPET_LENGTH],
                    )
                )

            if audit_trail is not None:
                audit_trail.record_completed(
                    request_id=f"websearch_provider_{normalized_provider}_q{len(request.query.strip())}"[:64],
                    capability_name="ki.websearch.query",
                    capability_version="1.0.0",
                )
            return WebsearchExecutionResult(result=CapabilityDecisionResult.ALLOW, reason_code="ok", results=tuple(safe_items))
        except TimeoutError:
            failure_reason = "provider_timeout"
            continue
        except Exception:
            failure_reason = "provider_error"
            continue

    if audit_trail is not None:
        audit_trail.record_failed(
            request_id=f"websearch_provider_{normalized_provider}_q{len(request.query.strip())}"[:64],
            capability_name="ki.websearch.query",
            capability_version="1.0.0",
            error_code=failure_reason,
        )
    return WebsearchExecutionResult(result=CapabilityDecisionResult.DENY, reason_code=failure_reason, results=())


def _slug(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "-" for ch in value.lower().strip())
    collapsed = "-".join(part for part in cleaned.split("-") if part)
    return (collapsed or "query")[:64]


def _normalize_provider_name(value: str) -> str:
    return value.strip().lower() if isinstance(value, str) else ""


def _build_websearch_quota_request(*, provider_name: str, query: str) -> CapabilityQuotaRequest:
    return CapabilityQuotaRequest(
        capability_name="ki.websearch.query",
        actor_type=CapabilityActorType.AI,
        actor_id=f"provider:{provider_name}",
        scope_type=CapabilityScopeType.TOPIC,
        scope_id=f"query-len:{len(query.strip())}",
    )
