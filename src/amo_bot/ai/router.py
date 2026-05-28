from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
import logging
import re

from amo_bot.core.logging import log_event
from amo_bot.db.repositories import TopicAgentMemoryRepository

logger = logging.getLogger(__name__)
_COMPONENT = "ai.router"


class AIRouterReasonCode(StrEnum):
    """Machine-readable reason codes for deterministic router decisions."""

    DEFAULT_NOOP = "default_noop"
    SCOPE_ENABLED = "scope_enabled"
    MENTION_IN_ACTIVE_SCOPE = "mention_in_active_scope"
    REPLY_TO_BOT_IN_ACTIVE_SCOPE = "reply_to_bot_in_active_scope"
    CONTEXT_GUARD_FALLBACK = "context_guard_fallback"


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
    context_error: str = ""
    flag_ai_scope_active: bool = False
    flag_bot_mention: bool = False
    flag_reply_to_bot: bool = False
    assembled_soul_text: str = ""
    daily_memory_text: str = ""
    long_memory_text: str = ""
    recent_messages_text: str = ""
    recall_memory_text: str = ""


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
    _RECENT_WINDOW_DEFAULT_MESSAGES = 20
    _RECENT_WINDOW_MAX_MESSAGES = 50
    _RECENT_WINDOW_MAX_AGE_SECONDS = 60 * 60 * 24 * 14
    _RECALL_MAX_RECORDS = 20
    _RECALL_MAX_CHARS = 1200
    _RECALL_TIMEOUT_MS = 150
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
    _SENSITIVE_RECENT_MARKERS = (
        "system prompt",
        "system message",
        "internal prompt",
        "internal planning",
        "chain of thought",
        "private memory",
        "db dump",
        "sqlite",
        "postgres://",
        "mysql://",
        "api_key",
        "token=",
        "authorization:",
        "bearer ",
        "BEGIN RSA PRIVATE KEY",
        "BEGIN OPENSSH PRIVATE KEY",
        "OPENCLAW",
        "/home/",
        "C:\\",
    )
    _SECRET_ASSIGNMENT_RE = re.compile(
        r"\b(?:api[_-]?key|token|secret|password|passwd|pwd|auth(?:orization)?|access[_-]?token|refresh[_-]?token)\b\s*[:=]\s*\S+",
        re.IGNORECASE,
    )
    _JWT_LIKE_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
    _HEX_SECRET_RE = re.compile(r"\b[a-f0-9]{32,}\b", re.IGNORECASE)
    _BASE64_SECRET_RE = re.compile(r"\b(?:[A-Za-z0-9+/]{40,}={0,2}|[A-Za-z0-9_-]{40,})\b")
    _EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
    _PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d\s().-]{8,}\d)(?!\w)")

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
        recent_messages_text = ""
        recall_memory_text = ""
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
            recent_messages_text=recent_messages_text,
            recall_memory_text=recall_memory_text,
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
        daily_memory_text, daily_error = self._read_daily_memory_text(
            scope_type=scope["scope_type"],
            chat_id=scope["chat_id"] if isinstance(scope["chat_id"], int) else None,
            topic_id=scope["topic_id"] if isinstance(scope["topic_id"], int) else None,
            user_id=scope["user_id"] if isinstance(scope["user_id"], int) else None,
        )
        long_memory_text, long_error = self._read_long_memory_text(
            scope_type=scope["scope_type"],
            chat_id=scope["chat_id"] if isinstance(scope["chat_id"], int) else None,
            topic_id=scope["topic_id"] if isinstance(scope["topic_id"], int) else None,
            user_id=scope["user_id"] if isinstance(scope["user_id"], int) else None,
        )
        recent_window_size = max(0, min(int(getattr(config, "recent_context_window_size", self._RECENT_WINDOW_DEFAULT_MESSAGES) or 0), self._RECENT_WINDOW_MAX_MESSAGES))
        recent_messages_text, recent_error = self._read_recent_messages_text(
            scope_type=scope["scope_type"],
            chat_id=scope["chat_id"] if isinstance(scope["chat_id"], int) else None,
            topic_id=scope["topic_id"] if isinstance(scope["topic_id"], int) else None,
            user_id=scope["user_id"] if isinstance(scope["user_id"], int) else None,
            limit=recent_window_size,
        )
        recall_text, recall_error, recall_meta = self._read_active_recall_text(
            scope=scope,
            daily_memory_text=daily_memory_text,
            long_memory_text=long_memory_text,
            recent_messages_text=recent_messages_text,
        )
        context_error = ",".join(part for part in (daily_error, long_error, recent_error, recall_error) if part)
        if recall_meta:
            self._audit_recall(scope=scope, meta=recall_meta)

        if self._has_bot_mention(prompt=safe_prompt, bot_username=bot_username):
            reason_code = AIRouterReasonCode.CONTEXT_GUARD_FALLBACK if context_error else AIRouterReasonCode.MENTION_IN_ACTIVE_SCOPE
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
                    recent_messages_text=recent_messages_text,
                    recall_memory_text=recall_text,
                    context_error=context_error,
                ),
            )

        if reply_to_is_bot:
            reason_code = AIRouterReasonCode.CONTEXT_GUARD_FALLBACK if context_error else AIRouterReasonCode.REPLY_TO_BOT_IN_ACTIVE_SCOPE
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
                    recent_messages_text=recent_messages_text,
                    recall_memory_text=recall_text,
                    context_error=context_error,
                ),
            )

        scope_type = str(scope["scope_type"])
        if scope_type != "private_user":
            return AIRouterDecision(context=base_context)

        reason_code = AIRouterReasonCode.CONTEXT_GUARD_FALLBACK if context_error else AIRouterReasonCode.SCOPE_ENABLED
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
                recent_messages_text=recent_messages_text,
                recall_memory_text=recall_text,
                context_error=context_error,
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
        recent_messages_text: str,
        recall_memory_text: str,
        context_error: str = "",
    ) -> AIRouterContextV1:
        if scope is None:
            return AIRouterContextV1(
                user_id=user_id,
                message_text=message_text,
                route_reason=route_reason,
                context_error=context_error,
                flag_ai_scope_active=flag_ai_scope_active,
                flag_bot_mention=flag_bot_mention,
                flag_reply_to_bot=flag_reply_to_bot,
                assembled_soul_text=assembled_soul_text,
                daily_memory_text=daily_memory_text,
                long_memory_text=long_memory_text,
                recent_messages_text=recent_messages_text,
                recall_memory_text=recall_memory_text,
            )

        return AIRouterContextV1(
            scope_type=str(scope["scope_type"]),
            scope_chat_id=scope["chat_id"] if isinstance(scope["chat_id"], int) else None,
            scope_topic_id=scope["topic_id"] if isinstance(scope["topic_id"], int) else None,
            scope_user_id=scope["user_id"] if isinstance(scope["user_id"], int) else None,
            user_id=user_id,
            message_text=message_text,
            route_reason=route_reason,
            context_error=context_error,
            flag_ai_scope_active=flag_ai_scope_active,
            flag_bot_mention=flag_bot_mention,
            flag_reply_to_bot=flag_reply_to_bot,
            assembled_soul_text=assembled_soul_text,
            daily_memory_text=daily_memory_text,
            long_memory_text=long_memory_text,
            recent_messages_text=recent_messages_text,
            recall_memory_text=recall_memory_text,
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
    ) -> tuple[str, str]:
        repo = self._topic_agent_memory_repository
        if repo is None:
            return "", ""

        try:
            today = datetime.now(UTC).date()
            for memory_date in (today, today - timedelta(days=1)):
                record = repo.get_daily_memory(
                    scope_type=scope_type,
                    chat_id=chat_id,
                    topic_id=topic_id,
                    user_id=user_id,
                    memory_date=memory_date.isoformat(),
                )
                if record is None:
                    continue

                # Reuse KI-C1 soul text bounding and redaction-style filters for daily memory injection.
                return self._sanitize_soul_text(record.summary_text)[: self._MAX_SOUL_CHARS], ""

            return "", ""
        except Exception:
            return "", "daily_memory_error"

    def _read_long_memory_text(
        self,
        *,
        scope_type: str,
        chat_id: int | None,
        topic_id: int | None,
        user_id: int | None,
    ) -> tuple[str, str]:
        repo = self._topic_agent_memory_repository
        if repo is None:
            return "", ""

        try:
            records = repo.list_long_memories(
                scope_type=scope_type,
                chat_id=chat_id,
                topic_id=topic_id,
                user_id=user_id,
                active_only=True,
                answer_effective_only=True,
                limit=100,
            )
            if not records:
                return "", ""

            # Repository ordering is newest-first; reverse to deterministic oldest-first chronology.
            parts = [self._sanitize_soul_text(record.fact_text) for record in reversed(records)]
            joined = "\n".join(part for part in parts if part)
            if not joined:
                return "", ""
            return joined[: self._MAX_SOUL_CHARS].rstrip(), ""
        except Exception:
            return "", "long_memory_error"


    def _sanitize_recent_message(self, value: str | None) -> str:
        if not value:
            return ""

        normalized = re.sub(r"\s+", " ", value).strip()
        if not normalized:
            return ""

        normalized = self._SECRET_ASSIGNMENT_RE.sub("[redacted:secret]", normalized)
        normalized = re.sub(r"(?:/home/\S+|[A-Za-z]:\\\S+)", "[redacted:path]", normalized)
        normalized = self._JWT_LIKE_RE.sub("[redacted:jwt]", normalized)
        normalized = self._HEX_SECRET_RE.sub("[redacted:hex]", normalized)
        normalized = self._BASE64_SECRET_RE.sub("[redacted:base64]", normalized)
        normalized = self._EMAIL_RE.sub("[redacted:email]", normalized)
        normalized = self._PHONE_RE.sub("[redacted:phone]", normalized)

        lower = normalized.casefold()
        for marker in self._SENSITIVE_RECENT_MARKERS:
            if marker.casefold() in lower:
                return "[redacted:filtered]"

        return normalized.strip()

    def _read_active_recall_text(
        self,
        *,
        scope: dict[str, int | str | None] | None,
        daily_memory_text: str,
        long_memory_text: str,
        recent_messages_text: str,
    ) -> tuple[str, str, dict[str, object]]:
        meta: dict[str, object] = {
            "decision": "skip",
            "reason": "invalid_scope",
            "records_in": 0,
            "records_out": 0,
            "chars_out": 0,
            "truncated_records": False,
            "truncated_chars": False,
            "timeout_hit": False,
            "error_class": "",
        }
        if scope is None:
            return "", "", meta

        scope_type = scope.get("scope_type")
        chat_id = scope.get("chat_id")
        topic_id = scope.get("topic_id")
        user_id = scope.get("user_id")

        is_topic = scope_type == "topic" and isinstance(chat_id, int) and isinstance(topic_id, int) and user_id is None
        is_private = scope_type == "private_user" and chat_id is None and topic_id is None and isinstance(user_id, int)
        if not (is_topic or is_private):
            return "", "", meta

        raw_lines: list[str] = []
        for part in (daily_memory_text, long_memory_text, recent_messages_text):
            if not part:
                continue
            for line in part.splitlines():
                normalized = line.strip()
                if normalized:
                    raw_lines.append(normalized)

        meta["records_in"] = len(raw_lines)
        if not raw_lines:
            meta["reason"] = "empty_payload"
            return "", "", meta

        try:
            started = datetime.now(UTC)
            compact_lines: list[str] = []
            for line in raw_lines:
                if (datetime.now(UTC) - started).total_seconds() * 1000 > self._RECALL_TIMEOUT_MS:
                    meta["timeout_hit"] = True
                    break
                sanitized = self._sanitize_recent_message(line)
                if not sanitized:
                    continue
                compact_lines.append(sanitized)
                if len(compact_lines) >= self._RECALL_MAX_RECORDS:
                    meta["truncated_records"] = len(raw_lines) > len(compact_lines)
                    break

            if not compact_lines:
                meta["reason"] = "empty_after_sanitize"
                return "", "", meta

            compact = "\n".join(compact_lines)
            truncated_chars = len(compact) > self._RECALL_MAX_CHARS
            compact = compact[: self._RECALL_MAX_CHARS].rstrip()
            if not compact:
                meta["reason"] = "empty_after_trim"
                return "", "", meta

            meta["decision"] = "include"
            meta["reason"] = "ok"
            meta["records_out"] = len(compact.splitlines())
            meta["chars_out"] = len(compact)
            meta["truncated_chars"] = truncated_chars
            return compact, "", meta
        except Exception as exc:
            meta["decision"] = "error"
            meta["reason"] = "exception"
            meta["error_class"] = exc.__class__.__name__
            return "", "", meta


    def _read_recent_messages_text(
        self,
        *,
        scope_type: str,
        chat_id: int | None,
        topic_id: int | None,
        user_id: int | None,
        limit: int,
    ) -> tuple[str, str]:
        repo = self._topic_agent_memory_repository
        if repo is None:
            return "", ""

        try:
            safe_limit = max(0, min(limit, self._RECENT_WINDOW_MAX_MESSAGES))
            if safe_limit == 0:
                return "", ""

            rows = repo.list_recent(
                scope_type=scope_type,
                chat_id=chat_id,
                topic_id=topic_id,
                user_id=user_id,
                limit=safe_limit,
                max_age_seconds=self._RECENT_WINDOW_MAX_AGE_SECONDS,
            )
            if not rows:
                return "", ""

            parts = [self._sanitize_recent_message(row.message_text) for row in rows]
            joined = "\n".join(part for part in parts if part)
            if not joined:
                return "", ""
            return joined[: self._MAX_SOUL_CHARS].rstrip(), ""
        except Exception:
            return "", "recent_messages_error"

    def _audit_recall(self, *, scope: dict[str, int | str | None], meta: dict[str, object]) -> None:
        repo = self._topic_agent_memory_repository
        if repo is None:
            return

        scope_type = str(scope.get("scope_type") or "")
        log_event(
            logger,
            logging.INFO,
            event="ai_router_recall",
            component=_COMPONENT,
            extra={
                "scope_type": scope_type,
                "scope_chat_id": scope.get("chat_id") if isinstance(scope.get("chat_id"), int) else None,
                "scope_topic_id": scope.get("topic_id") if isinstance(scope.get("topic_id"), int) else None,
                "scope_user_id": scope.get("user_id") if isinstance(scope.get("user_id"), int) else None,
                "decision": str(meta.get("decision") or "skip"),
                "reason": str(meta.get("reason") or ""),
                "records_in": int(meta.get("records_in") or 0),
                "records_out": int(meta.get("records_out") or 0),
                "chars_out": int(meta.get("chars_out") or 0),
                "truncated_records": bool(meta.get("truncated_records")),
                "truncated_chars": bool(meta.get("truncated_chars")),
                "timeout_hit": bool(meta.get("timeout_hit")),
                "error_class": str(meta.get("error_class") or ""),
            },
        )

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
