from __future__ import annotations

import logging
import time
from typing import Protocol

from sqlalchemy.orm import sessionmaker

from amo_bot.auth.roles import Role
from amo_bot.db.models import GROUP_CHAT_TYPES
from amo_bot.db.repositories import ChatScopedRoleRepository, UserRoleRepository
from amo_bot.telegram.commands import RoleResolver

logger = logging.getLogger(__name__)

TELEGRAM_ADMIN_STATUSES = {"administrator", "creator"}
TELEGRAM_CHAT_MEMBER_SCOPE_TYPES = (*GROUP_CHAT_TYPES, "channel")
TELEGRAM_ADMIN_CACHE_TTL_SECONDS = 60.0


class TelegramChatMemberClient(Protocol):
    async def get_chat_member(self, *, chat_id: int, user_id: int) -> dict[str, object]: ...


class InMemoryRoleResolver(RoleResolver):
    def __init__(self, roles_by_user_id: dict[int, Role] | None = None, default_role: Role = Role.NORMAL) -> None:
        self._roles_by_user_id = roles_by_user_id or {}
        self._default_role = default_role

    async def resolve(self, user_id: int, *, chat_id: int | None = None, chat_type: str | None = None) -> Role:
        return self._roles_by_user_id.get(user_id, self._default_role)


class DBRoleResolver(RoleResolver):
    """Resolves user role from DB. Unknown users default to NORMAL.

    Rationale: secure-by-default usable baseline, aligned with existing logic
    where non-registered users are neither privileged nor hard-blocked.
    """

    def __init__(
        self,
        session_factory: sessionmaker,
        default_role: Role = Role.NORMAL,
        telegram_client: TelegramChatMemberClient | None = None,
        telegram_admin_cache_ttl_seconds: float = TELEGRAM_ADMIN_CACHE_TTL_SECONDS,
    ) -> None:
        self._session_factory = session_factory
        self._default_role = default_role
        self._telegram_client = telegram_client
        self._telegram_admin_cache_ttl_seconds = telegram_admin_cache_ttl_seconds
        self._telegram_admin_cache: dict[tuple[int, int], tuple[float, bool]] = {}

    def set_telegram_client(self, telegram_client: TelegramChatMemberClient | None) -> None:
        self._telegram_client = telegram_client

    async def resolve(self, user_id: int, *, chat_id: int | None = None, chat_type: str | None = None) -> Role:
        with self._session_factory() as session:
            global_role = UserRoleRepository(session).get_user_role(user_id) or self._default_role

            if chat_type == "private" or chat_id is None:
                return global_role

            if global_role is Role.OWNER:
                return Role.OWNER
            if global_role is Role.IGNORE:
                return Role.IGNORE

            if chat_type in GROUP_CHAT_TYPES:
                group_role = ChatScopedRoleRepository(session).get_group_role(chat_id=chat_id, telegram_user_id=user_id)
                if group_role is not None:
                    return group_role

        # Auto-detected Telegram owner/admin is an effective per-chat fallback only.
        # It never persists or overwrites explicit scoped roles, and global owner/ignore
        # have already returned above so Telegram status cannot bypass those hard roles.
        if await self._is_telegram_chat_admin(chat_id=chat_id, user_id=user_id, chat_type=chat_type):
            return Role.ADMIN

        if chat_type in GROUP_CHAT_TYPES:
            return Role.NORMAL

        return global_role

    async def _is_telegram_chat_admin(self, *, chat_id: int | None, user_id: int, chat_type: str | None) -> bool:
        if self._telegram_client is None or chat_id is None or chat_type not in TELEGRAM_CHAT_MEMBER_SCOPE_TYPES:
            return False

        cache_key = (chat_id, user_id)
        now = time.monotonic()
        cached = self._telegram_admin_cache.get(cache_key)
        if cached is not None:
            expires_at, value = cached
            if expires_at > now:
                return value
            self._telegram_admin_cache.pop(cache_key, None)

        try:
            member = await self._telegram_client.get_chat_member(chat_id=chat_id, user_id=user_id)
        except Exception:
            logger.info(
                "telegram chat member lookup failed; falling back to stored role",
                exc_info=True,
                extra={"chat_id": chat_id, "user_id": user_id, "chat_type": chat_type},
            )
            return False

        is_admin = str(member.get("status", "")).casefold() in TELEGRAM_ADMIN_STATUSES
        self._telegram_admin_cache[cache_key] = (now + max(0.0, self._telegram_admin_cache_ttl_seconds), is_admin)
        return is_admin
