"""Webtool subagent service for Issue #48.

Provides isolated, quota-checked execution of websearch and webscraping
operations behind a subagent/service boundary. Enforces role-based quotas,
fails closed on any error/disabled/quota-exceeded, and sanitizes results
to prevent prompt injection.

Audit is metadata-only: no query content, URLs, prompts, or secrets stored.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from amo_bot.auth.roles import Role
from amo_bot.core.logging import log_event
from amo_bot.db.repositories import WebToolRoleQuotaRepository, WebToolQuotaDecision


_COMPONENT = "ai.webtool_subagent"
logger = logging.getLogger(__name__)



# Operation types supported by the subagent
class WebtoolOperationType:
    WEBSEARCH = "websearch"
    WEBSCRAPING = "webscraping"
    BROWSER = "browser"
    WEATHER_EVIDENCE = "weather_evidence"
    CRYPTO_EVIDENCE = "crypto_evidence"


# Prompt injection patterns to sanitize from result text
# All patterns use case-insensitive matching via re.IGNORECASE flag
_PROMPT_INJECTION_PATTERNS = [
    # Direct instruction overrides
    r"ignore\s+(?:all\s+)?(?:previous\s+)?instructions?",
    r"ignore\s+(?:the\s+)?(?:above|previous)\s+(?:system\s+)?prompt",
    r"disregard\s+(?:all\s+)?(?:previous\s+)?instructions?",
    r"forget\s+(?:all\s+)?(?:previous\s+)?instructions?",
    # System prompt references
    r"system\s+prompt",
    r"system\s+instruction",
    r"developer\s+message",
    r"initial\s+prompt",
    # Jailbreak patterns
    r"dual\s+role",
    r"act\s+as\s+(?:if\s+)?you\s+(?:are|were)",
    r"pretend\s+(?:to\s+)?be",
    r"simulate\s+being",
    r"hypothetically",
    r"in\s+a\s+hypothetical\s+scenario",
    # Tool/secret extraction
    r"api\s*key",
    r"secret\s*key",
    r"access\s*token",
    r"bearer\s+token",
    r"password",
    r"show\s+(?:me\s+)?(?:your\s+)?(?:system\s+)?prompt",
    r"print\s+(?:your\s+)?(?:system\s+)?prompt",
    r"reveal\s+(?:your\s+)?(?:system\s+)?prompt",
    r"what\s+(?:were\s+)?you\s+told\s+to\s+do",
    r"what\s+is\s+your\s+instruction",
]

_PROMPT_INJECTION_REGEX = re.compile("|".join(_PROMPT_INJECTION_PATTERNS), re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class WebtoolSubagentRequest:
    """Request to execute a webtool operation.

    Attributes:
        operation_type: One of websearch, webscraping, browser.
        user_id: Telegram user ID making the request.
        role: Role of the user for quota/policy evaluation.
        chat_id: Telegram chat ID where request originated.
        topic_id: Optional message thread ID for scoped counters.
        day: Date string YYYY-MM-DD for quota tracking.
        query: For websearch: the search query string.
        url: For webscraping: the target URL.
        locale: Optional locale for websearch (default "en").
        max_results: Optional max results for websearch (default 5).
    """
    operation_type: str
    user_id: int
    role: Role
    chat_id: int
    topic_id: int | None
    day: str
    query: str = ""
    url: str = ""
    locale: str = "en"
    max_results: int = 5


@dataclass(frozen=True, slots=True)
class WebtoolSanitizedResult:
    """Sanitized result ready for LLM consumption.

    Attributes:
        text: Cleaned text content (sanitized, compact).
        sources: List of source URLs/hostnames (separate from text).
        hosts: List of hostnames/domains extracted from sources.
        result_type: Type of result (websearch_summary, webscraping_text, etc.).
    """
    text: str
    sources: tuple[str, ...]
    hosts: tuple[str, ...]
    result_type: str


@dataclass(frozen=True, slots=True)
class WebtoolSubagentResult:
    """Result of webtool subagent execution.

    Attributes:
        allowed: Whether the operation was permitted and executed.
        decision: Detailed decision code (allow, deny, disabled, quota_exceeded, etc.).
        reason: Human-readable reason for the decision.
        sanitized: Sanitized result data if allowed=True, else empty.
        metadata: Metadata-only audit info (no query/url content).
        error: Error message if execution failed.
    """
    allowed: bool
    decision: str
    reason: str
    sanitized: WebtoolSanitizedResult
    metadata: dict
    error: str | None = None


class WebtoolSearchProvider(Protocol):
    """Protocol for websearch provider implementations."""

    def search(self, *, query: str, locale: str, max_results: int) -> list[dict[str, str]]:
        """Return list of results with title, url, snippet keys."""
        ...


class WebtoolScrapeProvider(Protocol):
    """Protocol for webscraping provider implementations."""

    def fetch(self, *, url: str, timeout_seconds: float) -> dict[str, object]:
        """Return dict with status_code, headers, text, error keys."""
        ...




class WebtoolBrowserProvider(Protocol):
    """Protocol for browser provider implementations."""

    def render(self, *, url: str, timeout_seconds: float) -> dict[str, object]:
        """Return dict with url/status_code/text."""
        ...


class WebtoolWeatherEvidenceProvider(Protocol):
    """Protocol for structured weather evidence providers."""

    def get_weather(self, *, query: str, locale: str):
        """Return structured DomainEvidenceResult for weather."""
        ...


class WebtoolCryptoEvidenceProvider(Protocol):
    """Protocol for structured crypto evidence providers."""

    def get_crypto(self, *, query: str, locale: str):
        """Return structured DomainEvidenceResult for crypto."""
        ...


class ResearchObservationWriter(Protocol):
    """Metadata-only writer for research source observations."""

    def record_observation(
        self,
        *,
        provider_name: str,
        domain: str,
        outcome: str,
        confidence: float | None = None,
        source_name: str | None = None,
        source_hosts: tuple[str, ...] | None = None,
        source_urls: tuple[str, ...] | None = None,
        source_count: int | None = None,
        warning_codes: tuple[str, ...] | None = None,
        warning_count: int | None = None,
        error_class: str | None = None,
        timing_ms: int | None = None,
        metadata: dict[str, object] | None = None,
    ):
        ...


class WebtoolSubagentService:
    """Service for executing webtools with quota checks and sanitization.

    Enforces:
    - Role-based quota checks before execution
    - Fail-closed on disabled/limit reached/timeout/failure
    - Sanitized output (compact text + sources/hosts separated)
    - Metadata-only audit logging (no query/url content)
    """

    # Operation timeout defaults
    _DEFAULT_SEARCH_TIMEOUT_SECONDS = 10.0
    _DEFAULT_SCRAPE_TIMEOUT_SECONDS = 10.0

    # Output limits for sanitization
    _MAX_RESULT_TEXT_CHARS = 8000
    _MAX_SNIPPET_CHARS = 500
    _TRUNCATION_MARKER = "\n[truncated: oversized webtool result omitted from active context]"

    def __init__(
        self,
        quota_repo: WebToolRoleQuotaRepository,
        search_provider: WebtoolSearchProvider | None = None,
        scrape_provider: WebtoolScrapeProvider | None = None,
        browser_provider: WebtoolBrowserProvider | None = None,
        weather_evidence_provider: WebtoolWeatherEvidenceProvider | None = None,
        crypto_evidence_provider: WebtoolCryptoEvidenceProvider | None = None,
        observation_writer: ResearchObservationWriter | None = None,
        search_timeout_seconds: float = _DEFAULT_SEARCH_TIMEOUT_SECONDS,
        scrape_timeout_seconds: float = _DEFAULT_SCRAPE_TIMEOUT_SECONDS,
    ) -> None:
        self._quota_repo = quota_repo
        self._search_provider = search_provider
        self._scrape_provider = scrape_provider
        self._browser_provider = browser_provider
        self._weather_evidence_provider = weather_evidence_provider
        self._crypto_evidence_provider = crypto_evidence_provider
        self._observation_writer = observation_writer
        self._search_timeout = search_timeout_seconds
        self._scrape_timeout = scrape_timeout_seconds

    def execute(self, request: WebtoolSubagentRequest) -> WebtoolSubagentResult:
        """Execute webtool request with quota check and sanitization.

        Steps:
        1. Check role quota via WebToolRoleQuotaRepository
        2. If denied, return fail-closed result with metadata audit
        3. If allowed, execute operation in isolated/worker-style manner
        4. Sanitize result to prevent prompt injection
        5. Return sanitized result with metadata-only audit info
        """
        start_time = time.perf_counter()

        # Step 1: Check quota
        quota_decision = self._check_quota(request)

        # Build metadata for audit (metadata-only, no query/url content)
        metadata = self._build_metadata(request, quota_decision, start_time)

        # Structured log event (metadata-only, no query/url/prompt/secret content)
        log_event(
            logger,
            logging.INFO,
            event="webtool_quota_check",
            component=_COMPONENT,
            user_id=request.user_id,
            chat_id=request.chat_id,
            message_thread_id=request.topic_id,
            reason_code=quota_decision.reason,
            extra={
                "operation": request.operation_type,
                "role": request.role.value,
                "decision": quota_decision.decision,
                "timing_ms": metadata.get("timing_ms", 0),
            },
        )

        # Step 2: Fail closed if quota denied
        if not quota_decision.allowed:
            result = WebtoolSubagentResult(
                allowed=False,
                decision=quota_decision.decision,
                reason=quota_decision.reason,
                sanitized=self._empty_result(),
                metadata=metadata,
                error=None,
            )
            self._record_source_observation(request, result)
            return result

        # Step 3: Execute operation based on type
        try:
            if request.operation_type == WebtoolOperationType.WEBSEARCH:
                result = self._execute_websearch(request, metadata)
            elif request.operation_type == WebtoolOperationType.WEBSCRAPING:
                result = self._execute_webscraping(request, metadata)
            elif request.operation_type == WebtoolOperationType.BROWSER:
                result = self._execute_browser(request, metadata)
            elif request.operation_type == WebtoolOperationType.WEATHER_EVIDENCE:
                result = self._execute_weather_evidence(request, metadata)
            elif request.operation_type == WebtoolOperationType.CRYPTO_EVIDENCE:
                result = self._execute_crypto_evidence(request, metadata)
            else:
                result = self._unsupported_result(request, metadata, "unknown_operation_type")
        except Exception as exc:
            # Fail closed on any execution error
            result = self._execution_error_result(request, metadata, exc)
        self._record_source_observation(request, result)
        return result

    def _check_quota(self, request: WebtoolSubagentRequest) -> WebToolQuotaDecision:
        """Check role quota for the operation."""
        return self._quota_repo.check_quota(
            user_id=request.user_id,
            role=request.role,
            chat_id=request.chat_id,
            message_thread_id=request.topic_id,
            operation_type=request.operation_type,
            day=request.day,
        )

    def _execute_websearch(
        self, request: WebtoolSubagentRequest, metadata: dict
    ) -> WebtoolSubagentResult:
        """Execute websearch with provider or fail-closed."""
        if self._search_provider is None:
            return WebtoolSubagentResult(
                allowed=False,
                decision="provider_unavailable",
                reason="search_provider_not_configured",
                sanitized=self._empty_result(),
                metadata=metadata,
                error="No search provider configured",
            )

        try:
            # Execute with timeout
            results = self._search_provider.search(
                query=request.query,
                locale=request.locale,
                max_results=min(max(request.max_results, 1), 5),
            )

            # Build sanitized result
            sanitized = self._sanitize_search_results(results)
            if not sanitized.text.strip() or not sanitized.sources:
                return WebtoolSubagentResult(
                    allowed=False,
                    decision="deny",
                    reason="empty_result",
                    sanitized=self._empty_result(),
                    metadata=metadata,
                    error="Search provider returned no usable results",
                )

            return WebtoolSubagentResult(
                allowed=True,
                decision="allow",
                reason="search_completed",
                sanitized=sanitized,
                metadata=metadata,
                error=None,
            )

        except TimeoutError:
            log_event(logger, logging.WARNING, event="webtool_search_timeout", component=_COMPONENT,
                      reason_code="search_timeout",
                      extra={"operation": WebtoolOperationType.WEBSEARCH, "timing_ms": metadata.get("timing_ms", 0)})
            return WebtoolSubagentResult(
                allowed=False,
                decision="deny",
                reason="search_timeout",
                sanitized=self._empty_result(),
                metadata=metadata,
                error="Search operation timed out",
            )
        except Exception as exc:
            log_event(logger, logging.WARNING, event="webtool_search_error", component=_COMPONENT,
                      reason_code="search_failed",
                      extra={"operation": WebtoolOperationType.WEBSEARCH, "error_class": type(exc).__name__, "timing_ms": metadata.get("timing_ms", 0)})
            return WebtoolSubagentResult(
                allowed=False,
                decision="deny",
                reason="search_failed",
                sanitized=self._empty_result(),
                metadata=metadata,
                error=f"Search failed: {type(exc).__name__}",
            )

    def _execute_webscraping(
        self, request: WebtoolSubagentRequest, metadata: dict
    ) -> WebtoolSubagentResult:
        """Execute webscraping with provider or fail-closed."""
        if self._scrape_provider is None:
            return WebtoolSubagentResult(
                allowed=False,
                decision="provider_unavailable",
                reason="scrape_provider_not_configured",
                sanitized=self._empty_result(),
                metadata=metadata,
                error="No scrape provider configured",
            )

        try:
            # Execute with timeout
            result = self._scrape_provider.fetch(
                url=request.url,
                timeout_seconds=self._scrape_timeout,
            )

            # Build sanitized result
            sanitized = self._sanitize_scrape_result(result)

            # Check HTTP status
            status_code = result.get("status_code", 0)
            if not (200 <= status_code < 300):
                return WebtoolSubagentResult(
                    allowed=False,
                    decision="deny",
                    reason=f"http_error_{status_code}",
                    sanitized=self._empty_result(),
                    metadata=metadata,
                    error=f"HTTP error: {status_code}",
                )

            return WebtoolSubagentResult(
                allowed=True,
                decision="allow",
                reason="scrape_completed",
                sanitized=sanitized,
                metadata=metadata,
                error=None,
            )

        except TimeoutError:
            log_event(logger, logging.WARNING, event="webtool_scrape_timeout", component=_COMPONENT,
                      reason_code="scrape_timeout",
                      extra={"operation": WebtoolOperationType.WEBSCRAPING, "timing_ms": metadata.get("timing_ms", 0)})
            return WebtoolSubagentResult(
                allowed=False,
                decision="deny",
                reason="scrape_timeout",
                sanitized=self._empty_result(),
                metadata=metadata,
                error="Scrape operation timed out",
            )
        except Exception as exc:
            log_event(logger, logging.WARNING, event="webtool_scrape_error", component=_COMPONENT,
                      reason_code="scrape_failed",
                      extra={"operation": WebtoolOperationType.WEBSCRAPING, "error_class": type(exc).__name__, "timing_ms": metadata.get("timing_ms", 0)})
            return WebtoolSubagentResult(
                allowed=False,
                decision="deny",
                reason="scrape_failed",
                sanitized=self._empty_result(),
                metadata=metadata,
                error=f"Scrape failed: {type(exc).__name__}",
            )

    def _execute_browser(
        self, request: WebtoolSubagentRequest, metadata: dict
    ) -> WebtoolSubagentResult:
        if self._browser_provider is None:
            return WebtoolSubagentResult(
                allowed=False,
                decision="provider_unavailable",
                reason="browser_provider_not_configured",
                sanitized=self._empty_result(),
                metadata=metadata,
                error="No browser provider configured",
            )
        try:
            result = self._browser_provider.render(url=request.url, timeout_seconds=self._scrape_timeout)
            status_code = int(result.get("status_code", 0) or 0)
            if not (200 <= status_code < 300):
                return WebtoolSubagentResult(
                    allowed=False,
                    decision="deny",
                    reason=f"http_error_{status_code}",
                    sanitized=self._empty_result(),
                    metadata=metadata,
                    error=f"HTTP error: {status_code}",
                )
            sanitized = self._sanitize_scrape_result(result)
            return WebtoolSubagentResult(
                allowed=True,
                decision="allow",
                reason="browser_completed",
                sanitized=WebtoolSanitizedResult(
                    text=sanitized.text,
                    sources=sanitized.sources,
                    hosts=sanitized.hosts,
                    result_type="browser_text",
                ),
                metadata=metadata,
                error=None,
            )
        except TimeoutError:
            return WebtoolSubagentResult(
                allowed=False,
                decision="deny",
                reason="browser_timeout",
                sanitized=self._empty_result(),
                metadata=metadata,
                error="Browser operation timed out",
            )
        except Exception as exc:
            return WebtoolSubagentResult(
                allowed=False,
                decision="deny",
                reason="browser_failed",
                sanitized=self._empty_result(),
                metadata=metadata,
                error=f"Browser failed: {type(exc).__name__}",
            )

    def _execute_weather_evidence(self, request: WebtoolSubagentRequest, metadata: dict) -> WebtoolSubagentResult:
        """Execute structured weather evidence behind the webtool quota/audit path."""
        if self._weather_evidence_provider is None:
            return WebtoolSubagentResult(
                allowed=False,
                decision="provider_unavailable",
                reason="weather_provider_not_configured",
                sanitized=self._empty_result(),
                metadata=metadata,
                error="No weather evidence provider configured",
            )
        try:
            result = self._weather_evidence_provider.get_weather(query=request.query, locale=request.locale)
            return self._evidence_result_to_subagent_result(
                result,
                metadata,
                result_type="weather_evidence",
                empty_reason="weather_evidence_unconfirmed",
            )
        except Exception as exc:
            return self._execution_error_result(request, metadata, exc)

    def _execute_crypto_evidence(self, request: WebtoolSubagentRequest, metadata: dict) -> WebtoolSubagentResult:
        """Execute structured crypto evidence behind the webtool quota/audit path."""
        if self._crypto_evidence_provider is None:
            return WebtoolSubagentResult(
                allowed=False,
                decision="provider_unavailable",
                reason="crypto_provider_not_configured",
                sanitized=self._empty_result(),
                metadata=metadata,
                error="No crypto evidence provider configured",
            )
        try:
            result = self._crypto_evidence_provider.get_crypto(query=request.query, locale=request.locale)
            return self._evidence_result_to_subagent_result(
                result,
                metadata,
                result_type="crypto_evidence",
                empty_reason="crypto_evidence_unconfirmed",
            )
        except Exception as exc:
            return self._execution_error_result(request, metadata, exc)

    def _evidence_result_to_subagent_result(
        self,
        result,
        metadata: dict,
        *,
        result_type: str,
        empty_reason: str,
    ) -> WebtoolSubagentResult:
        """Map structured evidence to sanitized output without adding content to metadata."""
        evidence_sources = tuple(getattr(result, "sources", ()) or ())
        source_urls = tuple(str(getattr(source, "url", "") or "") for source in evidence_sources if getattr(source, "url", ""))
        source_names = tuple(str(getattr(source, "name", "") or "") for source in evidence_sources if getattr(source, "name", ""))
        source_hosts = tuple(host for url in source_urls if (host := self._extract_host(url)))
        metadata.update(
            {
                "evidence_domain": str(getattr(result, "domain", "")),
                "evidence_status": str(getattr(result, "status", "")),
                "evidence_confidence": float(getattr(result, "confidence", 0.0) or 0.0),
                "source_count": len(source_urls),
                "source_names": source_names[:5],
                "warning_count": len(getattr(result, "warnings", ()) or ()),
                "warning_codes": tuple(str(item) for item in tuple(getattr(result, "warnings", ()) or ())[:20]),
            }
        )
        if not getattr(result, "confirmed", False):
            return WebtoolSubagentResult(
                allowed=False,
                decision="deny",
                reason=str(getattr(result, "status", "") or empty_reason),
                sanitized=self._empty_result(),
                metadata=metadata,
                error=None,
            )
        sanitized = WebtoolSanitizedResult(
            text=self._cap_result_text(self._sanitize_text(str(getattr(result, "text", "") or ""))),
            sources=source_urls,
            hosts=source_hosts,
            result_type=result_type,
        )
        return WebtoolSubagentResult(
            allowed=True,
            decision="allow",
            reason=f"{result_type}_completed",
            sanitized=sanitized,
            metadata=metadata,
            error=None,
        )

    def _sanitize_search_results(self, results: list[dict[str, str]]) -> WebtoolSanitizedResult:
        """Sanitize search results: compact text + sources/hosts separated."""
        texts: list[str] = []
        sources: list[str] = []
        hosts: list[str] = []

        for i, item in enumerate(results[:5], 1):
            title = self._sanitize_text(item.get("title", ""))
            url = item.get("url", "")
            snippet = self._sanitize_text(item.get("snippet", ""))

            # Extract host from URL
            host = self._extract_host(url)
            if host and host not in hosts:
                hosts.append(host)
            if url:
                sources.append(url)

            # Compact format: number. title - snippet
            if title or snippet:
                compact = f"{i}. {title}: {snippet}".strip(": ")
                texts.append(compact)

        combined_text = self._cap_result_text("\n".join(texts))

        return WebtoolSanitizedResult(
            text=combined_text,
            sources=tuple(sources),
            hosts=tuple(hosts),
            result_type="websearch_summary",
        )

    def _sanitize_scrape_result(self, result: dict[str, object]) -> WebtoolSanitizedResult:
        """Sanitize scrape result: extract visible text, separate sources/hosts."""
        url = result.get("url", "")
        text = self._sanitize_text(str(result.get("text", "")))

        # Extract host
        host = self._extract_host(url)
        hosts = (host,) if host else ()
        sources = (url,) if url else ()

        combined_text = self._cap_result_text(text)

        return WebtoolSanitizedResult(
            text=combined_text,
            sources=sources,
            hosts=hosts,
            result_type="webscraping_text",
        )

    def _cap_result_text(self, text: str) -> str:
        if len(text) <= self._MAX_RESULT_TEXT_CHARS:
            return text
        keep = max(0, self._MAX_RESULT_TEXT_CHARS - len(self._TRUNCATION_MARKER))
        return text[:keep].rstrip() + self._TRUNCATION_MARKER

    def _sanitize_text(self, text: str) -> str:
        """Sanitize text: remove/neutralize prompt injection patterns."""
        # Remove null bytes
        cleaned = text.replace("\x00", "")

        # Replace prompt injection patterns with [REDACTED]
        cleaned = _PROMPT_INJECTION_REGEX.sub("[REDACTED]", cleaned)

        # Normalize whitespace
        cleaned = " ".join(cleaned.split())

        return cleaned.strip()

    def _extract_host(self, url: str) -> str:
        """Extract hostname from URL safely."""
        if not url:
            return ""
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            host = parsed.hostname or ""
            return host.lower() if host else ""
        except Exception:
            return ""

    def _build_metadata(
        self, request: WebtoolSubagentRequest, quota_decision: WebToolQuotaDecision, start_time: float
    ) -> dict:
        """Build metadata-only audit info (no query/url content)."""
        elapsed_ms = int((time.perf_counter() - start_time) * 1000)

        return {
            "role": request.role.value,
            "user_id": request.user_id,
            "chat_id": request.chat_id,
            "topic_id": request.topic_id,
            "operation": request.operation_type,
            "decision": quota_decision.decision,
            "limit": quota_decision.limit,
            "count": quota_decision.current_count,
            "remaining": quota_decision.remaining,
            "reason": quota_decision.reason,
            "timing_ms": elapsed_ms + (quota_decision.timing_ms or 0),
            # No query, no url, no content, no prompts, no secrets
        }

    def _empty_result(self) -> WebtoolSanitizedResult:
        """Return empty sanitized result."""
        return WebtoolSanitizedResult(
            text="",
            sources=(),
            hosts=(),
            result_type="empty",
        )

    def _unsupported_result(
        self, request: WebtoolSubagentRequest, metadata: dict, reason_code: str
    ) -> WebtoolSubagentResult:
        """Return fail-closed result for unsupported operation."""
        return WebtoolSubagentResult(
            allowed=False,
            decision="deny",
            reason=reason_code,
            sanitized=self._empty_result(),
            metadata=metadata,
            error=f"Operation {request.operation_type} is not supported or disabled",
        )

    def _execution_error_result(
        self, request: WebtoolSubagentRequest, metadata: dict, exc: Exception
    ) -> WebtoolSubagentResult:
        """Return fail-closed result for execution error."""
        return WebtoolSubagentResult(
            allowed=False,
            decision="deny",
            reason="execution_error",
            sanitized=self._empty_result(),
            metadata={**metadata, "error_class": type(exc).__name__},
            error=f"Execution failed: {type(exc).__name__}",
        )

    def _record_source_observation(self, request: WebtoolSubagentRequest, result: WebtoolSubagentResult) -> None:
        if self._observation_writer is None:
            return
        is_quota_denial = self._is_quota_denial(result)
        provider_name = "webtool_dispatcher" if is_quota_denial else self._observation_provider_name(request.operation_type)
        domain = "webtool_dispatcher" if is_quota_denial else str(result.metadata.get("evidence_domain") or self._observation_domain(request.operation_type))
        outcome = "denied" if is_quota_denial else str(result.metadata.get("evidence_status") or result.reason or result.decision)
        confidence = result.metadata.get("evidence_confidence")
        warning_codes = tuple(str(item) for item in result.metadata.get("warning_codes", ()) or ())
        error_class = result.metadata.get("error_class")
        observation_metadata = {
            "operation": request.operation_type,
            "decision": result.decision,
            "reason": result.reason,
        }
        try:
            self._observation_writer.record_observation(
                provider_name=provider_name,
                source_name=self._observation_source_name(request.operation_type, result),
                domain=domain,
                outcome=outcome,
                confidence=float(confidence) if confidence is not None else None,
                source_hosts=tuple(result.sanitized.hosts),
                source_urls=tuple(result.sanitized.sources),
                source_count=int(result.metadata.get("source_count", len(result.sanitized.sources)) or 0),
                warning_codes=warning_codes,
                warning_count=int(result.metadata.get("warning_count", len(warning_codes)) or 0),
                error_class=str(error_class) if error_class else None,
                timing_ms=int(result.metadata.get("timing_ms", 0) or 0),
                metadata=observation_metadata,
            )
        except Exception as exc:
            log_event(
                logger,
                logging.WARNING,
                event="research_source_observation_write_failed",
                component=_COMPONENT,
                reason_code="observation_write_failed",
                extra={
                    "operation": request.operation_type,
                    "provider_name": provider_name,
                    "error_class": type(exc).__name__,
                },
            )

    @staticmethod
    def _is_quota_denial(result: WebtoolSubagentResult) -> bool:
        return result.decision in {"disabled", "quota_exceeded"} or result.reason in {"role_disabled", "daily_limit_reached"}

    @staticmethod
    def _observation_provider_name(operation_type: str) -> str:
        mapping = {
            WebtoolOperationType.WEBSEARCH: "websearch_provider",
            WebtoolOperationType.WEBSCRAPING: "webscrape_provider",
            WebtoolOperationType.BROWSER: "browser_provider",
            WebtoolOperationType.WEATHER_EVIDENCE: "weather_evidence_provider",
            WebtoolOperationType.CRYPTO_EVIDENCE: "crypto_evidence_provider",
        }
        return mapping.get(operation_type, "unknown_webtool_provider")

    @staticmethod
    def _observation_domain(operation_type: str) -> str:
        mapping = {
            WebtoolOperationType.WEBSEARCH: "websearch",
            WebtoolOperationType.WEBSCRAPING: "webscraping",
            WebtoolOperationType.BROWSER: "browser",
            WebtoolOperationType.WEATHER_EVIDENCE: "weather",
            WebtoolOperationType.CRYPTO_EVIDENCE: "crypto",
        }
        return mapping.get(operation_type, "generic")

    @staticmethod
    def _observation_source_name(operation_type: str, result: WebtoolSubagentResult) -> str | None:
        names = result.metadata.get("source_names")
        if isinstance(names, tuple) and names:
            return str(names[0])
        if isinstance(names, list) and names:
            return str(names[0])
        source_names = {
            WebtoolOperationType.WEBSEARCH: "websearch",
            WebtoolOperationType.WEBSCRAPING: "webscrape",
            WebtoolOperationType.BROWSER: "browser",
            WebtoolOperationType.WEATHER_EVIDENCE: "structured_weather",
            WebtoolOperationType.CRYPTO_EVIDENCE: "structured_crypto",
        }
        return source_names.get(operation_type)


class FakeSearchProvider:
    """Deterministic fake search provider for testing."""

    def search(self, *, query: str, locale: str, max_results: int) -> list[dict[str, str]]:
        """Return fake search results."""
        results = [
            {
                "title": f"Result for '{query}'",
                "url": f"https://example.com/search?q={query[:20]}",
                "snippet": f"This is a fake result for query '{query}' in locale {locale}.",
            },
            {
                "title": "Documentation",
                "url": "https://docs.example.com/page",
                "snippet": "Documentation page with relevant information.",
            },
            {
                "title": "References",
                "url": "https://refs.example.com/citations",
                "snippet": "Reference materials and citations.",
            },
        ]
        return results[:max_results]


class FakeScrapeProvider:
    """Deterministic fake scrape provider for testing."""

    def fetch(self, *, url: str, timeout_seconds: float) -> dict[str, object]:
        """Return fake scrape result."""
        return {
            "url": url,
            "status_code": 200,
            "headers": {"content-type": "text/html"},
            "text": f"<html><body><h1>Page content</h1><p>Extracted text from {url}</p></body></html>",
        }


def create_webtool_subagent_service(
    quota_repo: WebToolRoleQuotaRepository,
    use_fake_providers: bool = False,
    search_provider: WebtoolSearchProvider | None = None,
    scrape_provider: WebtoolScrapeProvider | None = None,
    browser_provider: WebtoolBrowserProvider | None = None,
    weather_evidence_provider: WebtoolWeatherEvidenceProvider | None = None,
    crypto_evidence_provider: WebtoolCryptoEvidenceProvider | None = None,
    observation_writer: ResearchObservationWriter | None = None,
) -> WebtoolSubagentService:
    """Factory to create webtool subagent service.

    Default is fail-closed: without explicit providers, all requests are denied
    with provider_unavailable. Real providers must be explicitly injected.
    use_fake_providers=True creates in-memory fake providers for tests only.

    Args:
        quota_repo: Repository for role quota checks (required).
        use_fake_providers: If True, use deterministic fake providers (tests only).
        search_provider: Real search provider implementing WebtoolSearchProvider.
            If None and use_fake_providers=False, all searches fail closed.
        scrape_provider: Real scrape provider implementing WebtoolScrapeProvider.
            If None and use_fake_providers=False, all scrapes fail closed.

    Returns:
        Configured WebtoolSubagentService instance.
    """
    if (
        search_provider is not None
        or scrape_provider is not None
        or browser_provider is not None
        or weather_evidence_provider is not None
        or crypto_evidence_provider is not None
    ):
        # Real providers injected — no fakes even if use_fake_providers=True
        return WebtoolSubagentService(
            quota_repo=quota_repo,
            search_provider=search_provider,
            scrape_provider=scrape_provider,
            browser_provider=browser_provider,
            weather_evidence_provider=weather_evidence_provider,
            crypto_evidence_provider=crypto_evidence_provider,
            observation_writer=observation_writer,
        )

    if use_fake_providers:
        search_provider = FakeSearchProvider()
        scrape_provider = FakeScrapeProvider()

    # Default: fail-closed (no providers → all operations denied)
    return WebtoolSubagentService(
        quota_repo=quota_repo,
        search_provider=search_provider,
        scrape_provider=scrape_provider,
        browser_provider=browser_provider,
        weather_evidence_provider=weather_evidence_provider,
        crypto_evidence_provider=crypto_evidence_provider,
        observation_writer=observation_writer,
    )
