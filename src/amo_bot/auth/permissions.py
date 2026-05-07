from __future__ import annotations

from amo_bot.auth.roles import Role


ADMIN_ASSIGNABLE_ROLES: set[Role] = {Role.VIP, Role.NORMAL, Role.IGNORE}


def can_assign_role(actor: Role, target_role: Role) -> bool:
    if actor == Role.OWNER:
        return True
    if actor == Role.ADMIN:
        return target_role in ADMIN_ASSIGNABLE_ROLES
    return False


def can_use_bot(role: Role) -> bool:
    return role != Role.IGNORE
