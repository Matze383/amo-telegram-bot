from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from .capability_audit import CapabilityAuditTrail
from .capability_policy import CapabilityDecisionResult

_MAX_QUERY_LENGTH = 256
_MAX_LOCALE_LENGTH = 16
_MAX_TITLE_LENGTH = 200
_MAX_SNIPPET_LENGTH = 400
_MAX_URL_LENGTH = 2048
_ALLOWED_SAFESEARCH = {"off", "moderate", "strict"}
_DEFAULT_PROVIDER_RESULT_CAP = 3
_MAX_PROVIDER_RESULT_CAP = 5


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
    provider: FakeWebsearchProvider,
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
    provider: FakeWebsearchProvider,
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
    dropped_items = 0
    for item in provider_results[:bounded_max]:
        parsed = urlparse(item.url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            dropped_items += 1
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
        locale = request.locale.strip().lower()
        safesearch = request.safesearch.strip().lower()
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


def _slug(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "-" for ch in value.lower().strip())
    collapsed = "-".join(part for part in cleaned.split("-") if part)
    return (collapsed or "query")[:64]
