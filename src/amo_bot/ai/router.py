from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from amo_bot.db.repositories import TopicAgentMemoryRepository


class AIRouterReasonCode(StrEnum):
    """Machine-readable reason codes for deterministic router decisions."""

    DEFAULT_NOOP = "default_noop"
    SCOPE_ENABLED = "scope_enabled"
    MENTION_IN_ACTIVE_SCOPE = "mention_in_active_scope"
    REPLY_TO_BOT_IN_ACTIVE_SCOPE = "reply_to_bot_in_active_scope"


@dataclass(frozen=True, slots=True)
class AIRouterDecision:
    """Deterministic KI-B routing decision metadata."""

    passthrough: bool = True
    eligible: bool = False
    reason_code: AIRouterReasonCode = AIRouterReasonCode.DEFAULT_NOOP


class AIRouter:
    """Minimal router seam for KI scope gating logic."""

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

        repo = self._topic_agent_memory_repository
        if repo is None:
            return AIRouterDecision()

        scope = self._resolve_scope(chat_id=chat_id, topic_id=topic_id, user_id=user_id)
        if scope is None:
            return AIRouterDecision()

        config = repo.get_config(
            scope_type=scope["scope_type"],
            chat_id=scope["chat_id"],
            topic_id=scope["topic_id"],
            user_id=scope["user_id"],
        )
        if config is None or not config.ai_enabled:
            return AIRouterDecision()

        if self._has_bot_mention(prompt=prompt, bot_username=bot_username):
            return AIRouterDecision(
                passthrough=True,
                eligible=True,
                reason_code=AIRouterReasonCode.MENTION_IN_ACTIVE_SCOPE,
            )

        if reply_to_is_bot:
            return AIRouterDecision(
                passthrough=True,
                eligible=True,
                reason_code=AIRouterReasonCode.REPLY_TO_BOT_IN_ACTIVE_SCOPE,
            )

        return AIRouterDecision(passthrough=True, eligible=True, reason_code=AIRouterReasonCode.SCOPE_ENABLED)

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


__all__ = ["AIRouter", "AIRouterDecision", "AIRouterReasonCode"]
