"""Webtool capability dispatcher — quota-first dispatch seam for AI tool calls (Issue #48).

Provides a clean integration boundary between the AI router/capability layer and
the WebtoolSubagentService. Enforces role-based quotas before any provider call.

Integration boundary:
    AI router / capability dispatcher → WebtoolCapabilityDispatcher.execute(...)
                                         ↓ quota check
                                      WebtoolSubagentService
                                         ↓ provider call
                                      Real providers (websearch_coreplugin / webscraping_coreplugin)

The dispatcher is a thin facade: it applies quota checks and translates between
capability-level requests and WebtoolSubagentRequest objects. All execution,
sanitization, and result mapping is delegated to WebtoolSubagentService.

Audit is metadata-only: no query content, URLs, prompts, or secrets stored.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from amo_bot.auth.roles import Role
    from amo_bot.db.repositories import WebToolRoleQuotaRepository

from .webtool_subagent import (
    WebtoolOperationType,
    WebtoolSubagentRequest,
    WebtoolSubagentResult,
    WebtoolSubagentService,
    create_webtool_subagent_service,
)


@dataclass(frozen=True, slots=True)
class WebtoolCapabilityRequest:
    """Capability-level request for webtool operations.

    This is the integration seam for the AI router / capability dispatcher.
    Translates to WebtoolSubagentRequest internally.

    Attributes:
        capability: One of "websearch", "webresearch", or "webscraping".
        user_id: Telegram user ID making the request.
        role: Role of the user for quota/policy evaluation.
        chat_id: Telegram chat ID where request originated.
        topic_id: Optional message thread ID for scoped counters.
        query: For websearch: the search query string.
        url: For webscraping: the target URL.
        locale: Optional locale for websearch (default "en").
        max_results: Optional max results for websearch (default 5).
        evidence_domain: Optional classified research domain for metadata-only learning.
    """
    capability: str
    user_id: int
    role: "Role"
    chat_id: int
    topic_id: int | None = None
    query: str = ""
    url: str = ""
    locale: str = "en"
    max_results: int = 5
    evidence_domain: str = ""


@dataclass(frozen=True, slots=True)
class WebtoolCapabilityResult:
    """Result of a webtool capability dispatch.

    Attributes:
        allowed: Whether the operation was permitted and executed.
        decision: Detailed decision code (allow, deny, disabled, quota_exceeded, etc.).
        reason: Human-readable reason for the decision.
        text: Cleaned text content from the operation (empty if denied).
        sources: List of source URLs/hostnames (empty if denied).
        hosts: List of hostnames/domains from sources (empty if denied).
        result_type: Type of result (websearch_summary, webscraping_text, empty).
        metadata: Metadata-only audit info (no query/url content).
        error: Error message if execution failed.
    """
    allowed: bool
    decision: str
    reason: str
    text: str
    sources: tuple[str, ...]
    hosts: tuple[str, ...]
    result_type: str
    metadata: dict
    error: str | None = None


def _map_operation_type(capability: str) -> str:
    """Map capability string to WebtoolOperationType."""
    normalized = capability.strip().lower()
    if normalized in {"websearch", "webresearch"}:
        return WebtoolOperationType.WEBSEARCH
    if normalized == "webscraping":
        return WebtoolOperationType.WEBSCRAPING
    if normalized == "browser":
        return WebtoolOperationType.BROWSER
    if normalized in {"weather_evidence", "weather"}:
        return WebtoolOperationType.WEATHER_EVIDENCE
    if normalized in {"crypto_evidence", "crypto"}:
        return WebtoolOperationType.CRYPTO_EVIDENCE
    return normalized


class WebtoolCapabilityDispatcher:
    """Quota-first dispatcher for AI-initiated webtool capabilities.

    This is the integration seam that the AI router calls when it wants to
    invoke a websearch or webscraping tool. Quota enforcement happens here
    (via WebtoolSubagentService) before any provider call.

    The dispatcher is stateless: it creates a fresh WebtoolSubagentService
    per call or can be initialized with a shared service instance.

    Usage:
        dispatcher = WebtoolCapabilityDispatcher(quota_repo=quota_repo)
        result = dispatcher.execute(
            WebtoolCapabilityRequest(
                capability="websearch",
                user_id=42,
                role=Role.VIP,
                chat_id=-100,
                query="python best practices",
            )
        )
    """

    def __init__(
        self,
        quota_repo: "WebToolRoleQuotaRepository",
        service: WebtoolSubagentService | None = None,
    ) -> None:
        """Initialize dispatcher.

        Args:
            quota_repo: Repository for role quota checks (required).
            service: Optional shared WebtoolSubagentService instance.
                    If None, a new service is created per call using
                    create_webtool_subagent_service with no providers
                    (fail-closed by default).
        """
        self._quota_repo = quota_repo
        self._service = service

    def execute(self, request: WebtoolCapabilityRequest) -> WebtoolCapabilityResult:
        """Execute a webtool capability request with quota enforcement.

        Translates the capability-level request to a WebtoolSubagentRequest,
        delegates to WebtoolSubagentService (which enforces quota), and maps
        the result back to WebtoolCapabilityResult.

        The quota check in WebtoolSubagentService ensures:
        - Disabled roles are denied before any provider call
        - Quota limits are enforced before any provider call
        - Provider is never called if quota check fails

        Args:
            request: WebtoolCapabilityRequest describing the operation.

        Returns:
            WebtoolCapabilityResult with allowed flag, decision, sanitized
            text/sources/hosts, and metadata-only audit info.
        """
        # Create service on demand (stateless per-call pattern)
        service = self._service
        if service is None:
            service = create_webtool_subagent_service(self._quota_repo)

        # Map capability to operation type
        operation_type = _map_operation_type(request.capability)

        # Build day string for quota tracking
        day = date.today().isoformat()

        # Translate to WebtoolSubagentRequest
        subagent_request = WebtoolSubagentRequest(
            operation_type=operation_type,
            user_id=request.user_id,
            role=request.role,
            chat_id=request.chat_id,
            topic_id=request.topic_id,
            day=day,
            query=request.query,
            url=request.url,
            locale=request.locale,
            max_results=request.max_results,
            evidence_domain=request.evidence_domain,
        )

        # Execute via service (which checks quota and sanitizes results)
        subagent_result = service.execute(subagent_request)

        # Map result back to capability-level result
        return WebtoolCapabilityResult(
            allowed=subagent_result.allowed,
            decision=subagent_result.decision,
            reason=subagent_result.reason,
            text=subagent_result.sanitized.text,
            sources=subagent_result.sanitized.sources,
            hosts=subagent_result.sanitized.hosts,
            result_type=subagent_result.sanitized.result_type,
            metadata=subagent_result.metadata,
            error=subagent_result.error,
        )
