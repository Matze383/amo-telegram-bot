from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal

from amo_bot.auth.roles import Role
from amo_bot.db.repositories import RetrievableMemoryRecord, RetrievableMemoryRepository

RememberVisibility = Literal["topic", "chat", "user", "global"]

MAX_MANUAL_MEMORY_CHARS = 1000
_ALLOWED_SCOPES = {"topic", "chat", "user", "global"}
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\b(api[_-]?key|token|password|passwd|authorization|cookie|session|secret|private[_-]?key)\b\s*[:=]"),
    re.compile(r"(?i)\b(bearer|sk-[a-z0-9]|xox[baprs]-|gh[pousr]_|github_pat_)"),
    re.compile(r"-----BEGIN [A-Z ]*(?:PRIVATE KEY|SECRET)[A-Z ]*-----"),
    re.compile(r"(?i)\bsystem\s+prompt\b|\bdeveloper\s+prompt\b"),
)


@dataclass(frozen=True, slots=True)
class ManualMemoryRequest:
    visibility: RememberVisibility
    memory_type: str
    content: str


@dataclass(frozen=True, slots=True)
class ManualMemoryScope:
    chat_id: int | None
    message_thread_id: int | None
    user_id: int | None


@dataclass(frozen=True, slots=True)
class ManualMemorySaveResult:
    record: RetrievableMemoryRecord
    created: bool


class ManualMemoryError(ValueError):
    """Safe, user-facing validation error for explicit memory saves."""


class ManualMemoryService:
    def __init__(self, repository: RetrievableMemoryRepository) -> None:
        self._repository = repository

    @classmethod
    def parse_command_argument(
        cls,
        argument: str | None,
        *,
        chat_id: int,
        message_thread_id: int | None,
        role: Role,
    ) -> ManualMemoryRequest:
        raw = (argument or "").strip()
        if not raw:
            raise ManualMemoryError("usage")
        parts = raw.split(maxsplit=2)
        if len(parts) < 2:
            raise ManualMemoryError("usage")

        first = parts[0].casefold()
        second = parts[1].casefold()
        if first in _ALLOWED_SCOPES:
            visibility = first
            memory_type = second
            content = parts[2].strip() if len(parts) >= 3 else ""
        else:
            visibility = cls.default_visibility(chat_id=chat_id, message_thread_id=message_thread_id)
            memory_type = first
            content = raw[len(parts[0]):].strip()

        if visibility == "global" and role not in {Role.OWNER, Role.ADMIN}:
            raise ManualMemoryError("global_disallowed")
        if visibility == "global":
            # v1 keeps global manual memory disabled even for admins to avoid accidental broad leakage.
            raise ManualMemoryError("global_disallowed")
        if memory_type not in RetrievableMemoryRepository.ALLOWED_MEMORY_TYPES:
            raise ManualMemoryError("invalid_type")
        safe_content = cls.sanitize_content(content)
        return ManualMemoryRequest(visibility=visibility, memory_type=memory_type, content=safe_content)

    @staticmethod
    def default_visibility(*, chat_id: int, message_thread_id: int | None) -> RememberVisibility:
        if chat_id > 0:
            return "user"
        if message_thread_id is not None:
            return "topic"
        return "chat"

    @staticmethod
    def sanitize_content(content: str | None) -> str:
        safe = re.sub(r"\s+", " ", (content or "").strip())
        if not safe:
            raise ManualMemoryError("empty")
        if len(safe) > MAX_MANUAL_MEMORY_CHARS:
            raise ManualMemoryError("too_long")
        if any(pattern.search(safe) for pattern in _SECRET_PATTERNS):
            raise ManualMemoryError("sensitive")
        return safe

    @staticmethod
    def scope_for_request(
        request: ManualMemoryRequest,
        *,
        chat_id: int,
        message_thread_id: int | None,
        user_id: int,
    ) -> ManualMemoryScope:
        if request.visibility == "topic":
            if chat_id > 0 or message_thread_id is None:
                raise ManualMemoryError("topic_unavailable")
            return ManualMemoryScope(chat_id=chat_id, message_thread_id=message_thread_id, user_id=None)
        if request.visibility == "chat":
            if chat_id > 0:
                raise ManualMemoryError("chat_unavailable")
            return ManualMemoryScope(chat_id=chat_id, message_thread_id=None, user_id=None)
        if request.visibility == "user":
            return ManualMemoryScope(chat_id=None if chat_id > 0 else chat_id, message_thread_id=None, user_id=user_id)
        raise ManualMemoryError("global_disallowed")

    def save_manual_memory(
        self,
        request: ManualMemoryRequest,
        *,
        chat_id: int,
        message_thread_id: int | None,
        user_id: int,
    ) -> ManualMemorySaveResult:
        scope = self.scope_for_request(
            request,
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            user_id=user_id,
        )
        record, created = self._repository.upsert_manual_memory(
            visibility=request.visibility,
            memory_type=request.memory_type,
            content=request.content,
            chat_id=scope.chat_id,
            message_thread_id=scope.message_thread_id,
            user_id=scope.user_id,
            confidence=0.9,
        )
        return ManualMemorySaveResult(record=record, created=created)
