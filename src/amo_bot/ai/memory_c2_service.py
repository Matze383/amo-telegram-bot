from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import Enum

from amo_bot.auth.roles import Role
from amo_bot.db.repositories import TopicAgentMemoryRepository, TopicLongMemoryRecord


class DreamStage(str, Enum):
    LIGHT = "light"
    REM = "rem"
    DEEP = "deep"


class ReviewAction(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    ARCHIVE = "archive"
    DEACTIVATE = "deactivate"


@dataclass(frozen=True, slots=True)
class MemoryScope:
    scope_type: str
    chat_id: int | None = None
    topic_id: int | None = None
    user_id: int | None = None


@dataclass(frozen=True, slots=True)
class ReviewActor:
    telegram_user_id: int
    role: Role


@dataclass(frozen=True, slots=True)
class ReviewListItem:
    memory_id: int
    scope_type: str
    chat_id: int | None
    topic_id: int | None
    user_id: int | None
    active: bool
    answer_status: str
    promotion_status: str
    source_daily_memory_id: int | None


@dataclass(frozen=True, slots=True)
class C2AuditPayload:
    event: str
    memory_id: int | None
    scope_type: str
    chat_id: int | None
    topic_id: int | None
    user_id: int | None
    stage: str | None
    action: str | None
    actor_id: int
    actor_role: str
    refs: dict[str, str]


class PermissionDeniedError(PermissionError):
    pass


class MemoryC2RedactionPolicy:
    @staticmethod
    def _safe_ref(value: str) -> str:
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
        return f"sha256:{digest[:12]}"

    @classmethod
    def refs(
        cls,
        *,
        source_daily_memory_id: int | None,
        source_recent_message_id: int | None,
        source_context_ref: str | None,
    ) -> dict[str, str]:
        payload: dict[str, str] = {}
        if source_daily_memory_id is not None:
            payload["daily_ref"] = cls._safe_ref(f"daily:{source_daily_memory_id}")
        if source_recent_message_id is not None:
            payload["recent_ref"] = cls._safe_ref(f"recent:{source_recent_message_id}")
        if source_context_ref:
            payload["context_ref"] = cls._safe_ref(f"ctx:{source_context_ref[:64]}")
        return payload


class MemoryC2PermissionPolicy:
    @staticmethod
    def _is_owner_or_admin(role: Role) -> bool:
        return role in {Role.OWNER, Role.ADMIN}

    def can_review(self, *, actor: ReviewActor, scope: MemoryScope) -> bool:
        if scope.scope_type == "private_user":
            return actor.telegram_user_id == scope.user_id
        if scope.scope_type in {"topic", "group_chat"}:
            return self._is_owner_or_admin(actor.role)
        return False


class MemoryC2Service:
    def __init__(
        self,
        *,
        repository: TopicAgentMemoryRepository,
        permission_policy: MemoryC2PermissionPolicy | None = None,
        redaction_policy: type[MemoryC2RedactionPolicy] = MemoryC2RedactionPolicy,
    ) -> None:
        self._repository = repository
        self._permission = permission_policy or MemoryC2PermissionPolicy()
        self._redaction = redaction_policy

    @staticmethod
    def _scope_kwargs(scope: MemoryScope) -> dict[str, int | str | None]:
        return {
            "scope_type": scope.scope_type,
            "chat_id": scope.chat_id,
            "topic_id": scope.topic_id,
            "user_id": scope.user_id,
        }

    def _assert_review_allowed(self, *, actor: ReviewActor, scope: MemoryScope) -> None:
        if not self._permission.can_review(actor=actor, scope=scope):
            raise PermissionDeniedError("memory c2 review permission denied")

    def create_dream_candidate(
        self,
        *,
        scope: MemoryScope,
        stage: DreamStage,
        fact_text: str,
        source_daily_memory_id: int | None = None,
        source_recent_message_id: int | None = None,
        source_context_ref: str | None = None,
    ) -> tuple[TopicLongMemoryRecord, C2AuditPayload]:
        row = self._repository.create_long_memory(
            **self._scope_kwargs(scope),
            fact_text=fact_text,
            source_daily_memory_id=source_daily_memory_id,
            promotion_status="candidate",
        )
        audit = C2AuditPayload(
            event="dream_candidate_created",
            memory_id=row.id,
            scope_type=row.scope_type,
            chat_id=row.chat_id,
            topic_id=row.topic_id,
            user_id=row.user_id,
            stage=stage.value,
            action=None,
            actor_id=0,
            actor_role="system",
            refs=self._redaction.refs(
                source_daily_memory_id=source_daily_memory_id,
                source_recent_message_id=source_recent_message_id,
                source_context_ref=source_context_ref,
            ),
        )
        return row, audit

    def list_review_candidates(self, *, actor: ReviewActor, scope: MemoryScope) -> list[ReviewListItem]:
        self._assert_review_allowed(actor=actor, scope=scope)
        rows = self._repository.list_long_memories(**self._scope_kwargs(scope), active_only=False)
        items: list[ReviewListItem] = []
        for row in rows:
            if row.promotion_status != "candidate":
                continue
            items.append(
                ReviewListItem(
                    memory_id=row.id,
                    scope_type=row.scope_type,
                    chat_id=row.chat_id,
                    topic_id=row.topic_id,
                    user_id=row.user_id,
                    active=row.is_active,
                    answer_status=row.answer_status,
                    promotion_status=row.promotion_status,
                    source_daily_memory_id=row.source_daily_memory_id,
                )
            )
        return items

    def apply_review_action(
        self,
        *,
        actor: ReviewActor,
        scope: MemoryScope,
        memory_id: int,
        action: ReviewAction,
    ) -> C2AuditPayload:
        self._assert_review_allowed(actor=actor, scope=scope)
        rows = self._repository.list_long_memories(**self._scope_kwargs(scope), active_only=False)
        target = next((row for row in rows if row.id == memory_id), None)
        if target is None:
            raise PermissionDeniedError("memory is outside actor scope")

        if action is ReviewAction.APPROVE:
            ok = self._repository.approve_long_memory(memory_id=memory_id)
        elif action is ReviewAction.REJECT:
            ok = self._repository.reject_long_memory(memory_id=memory_id)
        elif action is ReviewAction.ARCHIVE:
            ok = self._repository.archive_long_memory(memory_id=memory_id)
        else:
            ok = self._repository.deactivate_long_memory(memory_id=memory_id)

        if not ok:
            raise ValueError("review action failed")

        refreshed = next(
            row for row in self._repository.list_long_memories(**self._scope_kwargs(scope), active_only=False) if row.id == memory_id
        )
        return C2AuditPayload(
            event="memory_review_action",
            memory_id=memory_id,
            scope_type=refreshed.scope_type,
            chat_id=refreshed.chat_id,
            topic_id=refreshed.topic_id,
            user_id=refreshed.user_id,
            stage=None,
            action=action.value,
            actor_id=actor.telegram_user_id,
            actor_role=actor.role.value,
            refs=self._redaction.refs(
                source_daily_memory_id=refreshed.source_daily_memory_id,
                source_recent_message_id=None,
                source_context_ref=None,
            ),
        )


def current_utc_date() -> date:
    return datetime.now(UTC).date()
