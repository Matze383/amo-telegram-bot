from __future__ import annotations

from sqlalchemy.orm import sessionmaker

from amo_bot.auth.roles import Role
from amo_bot.db.repositories import UserRoleRepository
from amo_bot.telegram.commands import RoleResolver


class InMemoryRoleResolver(RoleResolver):
    def __init__(self, roles_by_user_id: dict[int, Role] | None = None, default_role: Role = Role.NORMAL) -> None:
        self._roles_by_user_id = roles_by_user_id or {}
        self._default_role = default_role

    async def resolve(self, user_id: int) -> Role:
        return self._roles_by_user_id.get(user_id, self._default_role)


class DBRoleResolver(RoleResolver):
    """Resolves user role from DB. Unknown users default to NORMAL.

    Rationale: secure-by-default usable baseline, aligned with existing logic
    where non-registered users are neither privileged nor hard-blocked.
    """

    def __init__(self, session_factory: sessionmaker, default_role: Role = Role.NORMAL) -> None:
        self._session_factory = session_factory
        self._default_role = default_role

    async def resolve(self, user_id: int) -> Role:
        with self._session_factory() as session:
            repo = UserRoleRepository(session)
            role = repo.get_user_role(user_id)
            return role or self._default_role
