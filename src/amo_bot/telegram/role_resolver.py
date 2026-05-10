from __future__ import annotations

from sqlalchemy.orm import sessionmaker

from amo_bot.auth.roles import Role
from amo_bot.db.models import GROUP_CHAT_TYPES
from amo_bot.db.repositories import ChatScopedRoleRepository, UserRoleRepository
from amo_bot.telegram.commands import RoleResolver


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

    def __init__(self, session_factory: sessionmaker, default_role: Role = Role.NORMAL) -> None:
        self._session_factory = session_factory
        self._default_role = default_role

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
                return group_role or Role.NORMAL

            return global_role
