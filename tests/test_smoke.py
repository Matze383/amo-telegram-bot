from amo_bot.auth.permissions import can_assign_role
from amo_bot.auth.roles import Role


def test_admin_cannot_assign_owner() -> None:
    assert can_assign_role(Role.ADMIN, Role.OWNER) is False


def test_owner_can_assign_owner() -> None:
    assert can_assign_role(Role.OWNER, Role.OWNER) is True
