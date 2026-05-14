from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
import re

from amo_bot.db.repositories import TopicAgentMemoryRepository


class AIRouterReasonCode(StrEnum):
    """Machine-readable reason codes for deterministic router decisions."""

    DEFAULT_NOOP = "default_noop"
    SCOPE_ENABLED = "scope_enabled"
    MENTION_IN_ACTIVE_SCOPE = "mention_in_active_scope"
    REPLY_TO_BOT_IN_ACTIVE_SCOPE = "reply_to_bot_in_active_scope"


@dataclass(frozen=True, slots=True)
class AIRouterContextV1:
    """Minimal deterministic context object for router decisions."""

    scope_type: str = "none"
    scope_chat_id: int | None = None
    scope_topic_id: int | None = None
    scope_user_id: int | None = None
    user_id: int | None = None
    message_text: str = ""
    route_reason: AIRouterReasonCode = AIRouterReasonCode.DEFAULT_NOOP
    flag_ai_scope_active: bool = False
    flag_bot_mention: bool = False
    flag_reply_to_bot: bool = False
    assembled_soul_text: str = ""
    daily_memory_text: str = ""
    long_memory_text: str = ""


@dataclass(frozen=True, slots=True)
class AIRouterDecision:
    """Deterministic KI-B routing decision metadata."""

    passthrough: bool = True
    eligible: bool = False
    reason_code: AIRouterReasonCode = AIRouterReasonCode.DEFAULT_NOOP
    context: AIRouterContextV1 = AIRouterContextV1()


class AIRouter:
    """Minimal router seam for KI scope gating logic."""

    _MAX_SOUL_CHARS = 2000
    _SUSPICIOUS_SOUL_MARKERS = (
        "system prompt",
        "system message",
        "internal prompt",
        "raw file",
        "/etc/",
        "proc/",
        "BEGIN RSA PRIVATE KEY",
        "OPENCLAW",
    )

    def __init__(self, *, topic_agent_memory_repository: TopicAgentMemoryRepository | None = None) -> None:
        self._topic_agent_memory_repository = topic_agent_memory_repository

    def decide(
        self,
        *,
        prompt: str,
        chat_id: int | None = None,
        topic_id: int | None = None,
        user_id: int | None = None,
        chat_type: str | None = None,
        bot_username: str | None = None,
        reply_to_is_bot: bool = False,
    ) -> AIRouterDecision:
        _ = chat_type

        safe_prompt = prompt.strip()
        scope = self._resolve_scope(chat_id=chat_id, topic_id=topic_id, user_id=user_id)
        assembled_soul_text = ""
        daily_memory_text = ""
        long_memory_text = ""
        base_context = self._build_context(
            scope=scope,
            user_id=user_id,
            message_text=safe_prompt,
            route_reason=AIRouterReasonCode.DEFAULT_NOOP,
            flag_ai_scope_active=False,
            flag_bot_mention=False,
            flag_reply_to_bot=reply_to_is_bot,
            assembled_soul_text=assembled_soul_text,
            daily_memory_text=daily_memory_text,
            long_memory_text=long_memory_text,
        )

        repo = self._topic_agent_memory_repository
        if repo is None:
            return AIRouterDecision(context=base_context)

        if scope is None:
            return AIRouterDecision(context=base_context)

        config = repo.get_config(
            scope_type=scope["scope_type"],
            chat_id=scope["chat_id"],
            topic_id=scope["topic_id"],
            user_id=scope["user_id"],
        )
        if config is None or not config.ai_enabled:
            return AIRouterDecision(context=base_context)

        assembled_soul_text = self._assemble_soul_text(
            main_soul=config.main_soul_text,
            topic_soul=config.topic_soul_text,
        )
        daily_memory_text = self._read_daily_memory_text(
            scope_type=scope["scope_type"],
            chat_id=scope["chat_id"] if isinstance(scope["chat_id"], int) else None,
            topic_id=scope["topic_id"] if isinstance(scope["topic_id"], int) else None,
            user_id=scope["user_id"] if isinstance(scope["user_id"], int) else None,
        )
        long_memory_text = self._read_long_memory_text(
            scope_type=scope["scope_type"],
            chat_id=scope["chat_id"] if isinstance(scope["chat_id"], int) else None,
            topic_id=scope["topic_id"] if isinstance(scope["topic_id"], int) else None,
            user_id=scope["user_id"] if isinstance(scope["user_id"], int) else None,
        )

        if self._has_bot_mention(prompt=safe_prompt, bot_username=bot_username):
            reason_code = AIRouterReasonCode.MENTION_IN_ACTIVE_SCOPE
            return AIRouterDecision(
                passthrough=True,
                eligible=True,
                reason_code=reason_code,
                context=self._build_context(
                    scope=scope,
                    user_id=user_id,
                    message_text=safe_prompt,
                    route_reason=reason_code,
                    flag_ai_scope_active=True,
                    flag_bot_mention=True,
                    flag_reply_to_bot=reply_to_is_bot,
                    assembled_soul_text=assembled_soul_text,
                    daily_memory_text=daily_memory_text,
                    long_memory_text=long_memory_text,
                ),
            )

        if reply_to_is_bot:
            reason_code = AIRouterReasonCode.REPLY_TO_BOT_IN_ACTIVE_SCOPE
            return AIRouterDecision(
                passthrough=True,
                eligible=True,
                reason_code=reason_code,
                context=self._build_context(
                    scope=scope,
                    user_id=user_id,
                    message_text=safe_prompt,
                    route_reason=reason_code,
                    flag_ai_scope_active=True,
                    flag_bot_mention=False,
                    flag_reply_to_bot=True,
                    assembled_soul_text=assembled_soul_text,
                    daily_memory_text=daily_memory_text,
                    long_memory_text=long_memory_text,
                ),
            )

        reason_code = AIRouterReasonCode.SCOPE_ENABLED
        return AIRouterDecision(
            passthrough=True,
            eligible=True,
            reason_code=reason_code,
            context=self._build_context(
                scope=scope,
                user_id=user_id,
                message_text=safe_prompt,
                route_reason=reason_code,
                flag_ai_scope_active=True,
                flag_bot_mention=False,
                flag_reply_to_bot=reply_to_is_bot,
                assembled_soul_text=assembled_soul_text,
                daily_memory_text=daily_memory_text,
                long_memory_text=long_memory_text,
            ),
        )

    @staticmethod
    def _build_context(
        *,
        scope: dict[str, int | str | None] | None,
        user_id: int | None,
        message_text: str,
        route_reason: AIRouterReasonCode,
        flag_ai_scope_active: bool,
        flag_bot_mention: bool,
        flag_reply_to_bot: bool,
        assembled_soul_text: str,
        daily_memory_text: str,
        long_memory_text: str,
    ) -> AIRouterContextV1:
        if scope is None:
            return AIRouterContextV1(
                user_id=user_id,
                message_text=message_text,
                route_reason=route_reason,
                flag_ai_scope_active=flag_ai_scope_active,
                flag_bot_mention=flag_bot_mention,
                flag_reply_to_bot=flag_reply_to_bot,
                assembled_soul_text=assembled_soul_text,
                daily_memory_text=daily_memory_text,
                long_memory_text=long_memory_text,
            )

        return AIRouterContextV1(
            scope_type=str(scope["scope_type"]),
            scope_chat_id=scope["chat_id"] if isinstance(scope["chat_id"], int) else None,
            scope_topic_id=scope["topic_id"] if isinstance(scope["topic_id"], int) else None,
            scope_user_id=scope["user_id"] if isinstance(scope["user_id"], int) else None,
            user_id=user_id,
            message_text=message_text,
            route_reason=route_reason,
            flag_ai_scope_active=flag_ai_scope_active,
            flag_bot_mention=flag_bot_mention,
            flag_reply_to_bot=flag_reply_to_bot,
            assembled_soul_text=assembled_soul_text,
            daily_memory_text=daily_memory_text,
            long_memory_text=long_memory_text,
        )

    @staticmethod
    def _resolve_scope(
        *,
        chat_id: int | None,
        topic_id: int | None,
        user_id: int | None,
    ) -> dict[str, int | str | None] | None:
        if chat_id is not None and chat_id < 0 and topic_id is not None:
            return {"scope_type": "topic", "chat_id": chat_id, "topic_id": topic_id, "user_id": None}

        if (chat_id is not None and chat_id > 0) or user_id is not None:
            private_user_id = user_id if user_id is not None else chat_id
            if private_user_id is None:
                return None
            return {"scope_type": "private_user", "chat_id": None, "topic_id": None, "user_id": private_user_id}

        return None

    @classmethod
    def _sanitize_soul_text(cls, value: str | None) -> str:
        if not value:
            return ""

        normalized = value.strip()
        if not normalized:
            return ""

        lower = normalized.casefold()
        for marker in cls._SUSPICIOUS_SOUL_MARKERS:
            if marker.casefold() in lower:
                return ""

        normalized = re.sub(r"\s+", " ", normalized)
        return normalized.strip()

    @classmethod
    def _assemble_soul_text(cls, *, main_soul: str | None, topic_soul: str | None) -> str:
        # deterministic order: main first, topic second
        parts = [cls._sanitize_soul_text(main_soul), cls._sanitize_soul_text(topic_soul)]
        assembled = "\n\n".join(part for part in parts if part)
        if len(assembled) > cls._MAX_SOUL_CHARS:
            return assembled[: cls._MAX_SOUL_CHARS].rstrip()
        return assembled

    def _read_daily_memory_text(
        self,
        *,
        scope_type: str,
        chat_id: int | None,
        topic_id: int | None,
        user_id: int | None,
    ) -> str:
        repo = self._topic_agent_memory_repository
        if repo is None:
            return ""

        today = datetime.now(UTC).date().isoformat()
        record = repo.get_daily_memory(
            scope_type=scope_type,
            chat_id=chat_id,
            topic_id=topic_id,
            user_id=user_id,
            memory_date=today,
        )
        if record is None:
            return ""

        # Reuse KI-C1 soul text bounding and redaction-style filters for daily memory injection.
        return self._sanitize_soul_text(record.summary_text)[: self._MAX_SOUL_CHARS]

    def _read_long_memory_text(
        self,
        *,
        scope_type: str,
        chat_id: int | None,
        topic_id: int | None,
        user_id: int | None,
    ) -> str:
        repo = self._topic_agent_memory_repository
        if repo is None:
            return ""

        records = repo.list_long_memories(
            scope_type=scope_type,
            chat_id=chat_id,
            topic_id=topic_id,
            user_id=user_id,
            active_only=True,
            limit=100,
        )
        if not records:
            return ""

        # Repository ordering is newest-first; reverse to deterministic oldest-first chronology.
        parts = [self._sanitize_soul_text(record.fact_text) for record in reversed(records)]
        joined = "\n".join(part for part in parts if part)
        if not joined:
            return ""
        return joined[: self._MAX_SOUL_CHARS].rstrip()

    @staticmethod
    def _has_bot_mention(*, prompt: str, bot_username: str | None) -> bool:
        if bot_username is None:
            return False

        normalized = bot_username.strip().lstrip("@").lower()
        if not normalized:
            return False

        prompt_lower = prompt.lower()
        mention = f"@{normalized}"
        idx = prompt_lower.find(mention)
        while idx != -1:
            next_index = idx + len(mention)
            if next_index >= len(prompt_lower) or not (prompt_lower[next_index].isalnum() or prompt_lower[next_index] == "_"):
                return True
            idx = prompt_lower.find(mention, idx + 1)
        return False


__all__ = ["AIRouter", "AIRouterContextV1", "AIRouterDecision", "AIRouterReasonCode"]
