from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum

from .capability_audit import CapabilityAuditTrail, InMemoryCapabilityAuditSink
from .capability_policy import CapabilityActorType, CapabilityScopeType
from .memory_capability import MemoryCapabilityRequest, evaluate_memory_capability_policy
from ..db.repositories import TopicAgentMemoryRepository


class MemoryOperationResult(StrEnum):
    SUCCESS = "success"
    DENIED = "denied"
    NOT_FOUND = "not_found"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class MemoryScopeRef:
    scope_type: str
    chat_id: int | None = None
    topic_id: int | None = None
    user_id: int | None = None


@dataclass(frozen=True, slots=True)
class MemoryEntrySummary:
    memory_id: int
    scope_type: str
    created_at: str
    is_active: bool
    summary: str


@dataclass(frozen=True, slots=True)
class MemoryOperationResponse:
    result: MemoryOperationResult
    reason_code: str
    summaries: tuple[MemoryEntrySummary, ...] = ()


@dataclass(frozen=True, slots=True)
class MemoryCorepluginRequest:
    actor_type: CapabilityActorType
    capability_name: str
    scope: MemoryScopeRef
    consent_granted: bool


class MemoryCorepluginService:
    """CP-G2 bounded, scoped memory operations with redacted output only."""

    CAPABILITY_VERSION = "1.0.0"

    def __init__(
        self,
        *,
        repository: TopicAgentMemoryRepository,
        audit_trail: CapabilityAuditTrail,
        max_summary_chars: int = 80,
    ) -> None:
        self._repository = repository
        self._audit = audit_trail
        self._max_summary_chars = max(24, min(max_summary_chars, 240))

    def put_summary(
        self,
        request: MemoryCorepluginRequest,
        *,
        memory_date: str,
        summary_text: str,
        tokens_estimate: int,
    ) -> MemoryOperationResponse:
        decision = self._authorize(request)
        if not decision.allowed:
            return self._deny(request, decision.reason_code)

        try:
            row = self._repository.upsert_daily_memory(
                scope_type=request.scope.scope_type,
                chat_id=request.scope.chat_id,
                topic_id=request.scope.topic_id,
                user_id=request.scope.user_id,
                memory_date=memory_date,
                summary_text=summary_text,
                tokens_estimate=max(0, tokens_estimate),
            )
        except Exception:
            return self._fail(request, "memory_put_failed")

        summary = self._to_daily_summary(row.id, row.scope_type, row.memory_date, row.summary_text)
        self._audit.record_decision(
            request_id=f"memory-put-{row.id}",
            capability_name=request.capability_name,
            capability_version=self.CAPABILITY_VERSION,
            decision_result="allow",
            reason_code="memory_put_ok",
        )
        return MemoryOperationResponse(
            result=MemoryOperationResult.SUCCESS,
            reason_code="memory_put_ok",
            summaries=(summary,),
        )

    def get_summary(self, request: MemoryCorepluginRequest, *, memory_date: str) -> MemoryOperationResponse:
        decision = self._authorize(request)
        if not decision.allowed:
            return self._deny(request, decision.reason_code)

        row = self._repository.get_daily_memory(
            scope_type=request.scope.scope_type,
            chat_id=request.scope.chat_id,
            topic_id=request.scope.topic_id,
            user_id=request.scope.user_id,
            memory_date=memory_date,
        )
        if row is None:
            return MemoryOperationResponse(result=MemoryOperationResult.NOT_FOUND, reason_code="memory_not_found")

        summary = self._to_daily_summary(row.id, row.scope_type, row.memory_date, row.summary_text)
        self._audit.record_decision(
            request_id=f"memory-get-{row.id}",
            capability_name=request.capability_name,
            capability_version=self.CAPABILITY_VERSION,
            decision_result="allow",
            reason_code="memory_get_ok",
        )
        return MemoryOperationResponse(result=MemoryOperationResult.SUCCESS, reason_code="memory_get_ok", summaries=(summary,))

    def search_summaries(self, request: MemoryCorepluginRequest, *, limit: int = 20) -> MemoryOperationResponse:
        decision = self._authorize(request)
        if not decision.allowed:
            return self._deny(request, decision.reason_code)

        rows = self._repository.list_long_memories(
            scope_type=request.scope.scope_type,
            chat_id=request.scope.chat_id,
            topic_id=request.scope.topic_id,
            user_id=request.scope.user_id,
            active_only=True,
            limit=max(1, min(limit, 100)),
        )
        summaries = tuple(
            MemoryEntrySummary(
                memory_id=row.id,
                scope_type=row.scope_type,
                created_at=f"id:{row.id}",
                is_active=row.is_active,
                summary=_redact(row.fact_text, max_chars=self._max_summary_chars),
            )
            for row in rows
        )
        self._audit.record_decision(
            request_id=f"memory-search-{request.scope.scope_type}",
            capability_name=request.capability_name,
            capability_version=self.CAPABILITY_VERSION,
            decision_result="allow",
            reason_code="memory_search_ok",
        )
        return MemoryOperationResponse(result=MemoryOperationResult.SUCCESS, reason_code="memory_search_ok", summaries=summaries)

    def deactivate_long_memory(self, request: MemoryCorepluginRequest, *, memory_id: int) -> MemoryOperationResponse:
        decision = self._authorize(request)
        if not decision.allowed:
            return self._deny(request, decision.reason_code)

        ok = self._repository.deactivate_long_memory(memory_id=memory_id)
        if not ok:
            return MemoryOperationResponse(result=MemoryOperationResult.NOT_FOUND, reason_code="memory_not_found")

        self._audit.record_decision(
            request_id=f"memory-deactivate-{memory_id}",
            capability_name=request.capability_name,
            capability_version=self.CAPABILITY_VERSION,
            decision_result="allow",
            reason_code="memory_deactivate_ok",
        )
        return MemoryOperationResponse(result=MemoryOperationResult.SUCCESS, reason_code="memory_deactivate_ok")

    def delete_daily_memory(self, request: MemoryCorepluginRequest, *, retention_days: int, today: date | None = None) -> MemoryOperationResponse:
        decision = self._authorize(request)
        if not decision.allowed:
            return self._deny(request, decision.reason_code)

        deleted = self._repository.prune_daily_memories(
            scope_type=request.scope.scope_type,
            chat_id=request.scope.chat_id,
            topic_id=request.scope.topic_id,
            user_id=request.scope.user_id,
            retention_days=retention_days,
            today=today,
        )
        self._audit.record_decision(
            request_id=f"memory-delete-{datetime.now(UTC).timestamp()}",
            capability_name=request.capability_name,
            capability_version=self.CAPABILITY_VERSION,
            decision_result="allow",
            reason_code="memory_delete_ok",
        )
        return MemoryOperationResponse(result=MemoryOperationResult.SUCCESS, reason_code="memory_delete_ok")

    def _authorize(self, request: MemoryCorepluginRequest):
        return evaluate_memory_capability_policy(
            MemoryCapabilityRequest(
                actor_type=request.actor_type,
                capability_name=request.capability_name,
                scope_type=_to_scope_type(request.scope.scope_type),
                consent_granted=request.consent_granted,
            )
        )

    def _deny(self, request: MemoryCorepluginRequest, reason_code: str) -> MemoryOperationResponse:
        self._audit.record_decision(
            request_id="memory-denied",
            capability_name=request.capability_name,
            capability_version=self.CAPABILITY_VERSION,
            decision_result="deny",
            reason_code=reason_code,
        )
        return MemoryOperationResponse(result=MemoryOperationResult.DENIED, reason_code=reason_code)

    def _fail(self, request: MemoryCorepluginRequest, reason_code: str) -> MemoryOperationResponse:
        self._audit.record_failed(
            request_id="memory-failed",
            capability_name=request.capability_name,
            capability_version=self.CAPABILITY_VERSION,
            error_code=reason_code,
        )
        return MemoryOperationResponse(result=MemoryOperationResult.ERROR, reason_code=reason_code)

    def _to_daily_summary(self, memory_id: int, scope_type: str, memory_date: str, text: str) -> MemoryEntrySummary:
        return MemoryEntrySummary(
            memory_id=memory_id,
            scope_type=scope_type,
            created_at=memory_date,
            is_active=True,
            summary=_redact(text, max_chars=self._max_summary_chars),
        )


def _to_scope_type(scope_type: str) -> CapabilityScopeType:
    if scope_type == "topic":
        return CapabilityScopeType.TOPIC
    if scope_type == "private_user":
        return CapabilityScopeType.USER
    return CapabilityScopeType.GROUP


def _scope_id(scope: MemoryScopeRef) -> str:
    if scope.scope_type == "topic":
        return f"chat:{scope.chat_id}:topic:{scope.topic_id}"
    if scope.scope_type == "private_user":
        return f"user:{scope.user_id}"
    return f"group:{scope.chat_id}"


def _redact(value: str, *, max_chars: int) -> str:
    trimmed = " ".join((value or "").split())
    if not trimmed:
        return "[redacted-empty]"

    redacted_len = len(trimmed)
    visibility = "short" if redacted_len <= max_chars else "truncated"
    return f"[redacted:{visibility};chars={redacted_len}]"
