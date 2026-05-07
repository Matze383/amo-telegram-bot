import asyncio

from amo_bot.auth.roles import Role
from amo_bot.telegram.commands import CommandContext, create_builtin_registry


def test_help_role_aware() -> None:
    reg = create_builtin_registry()
    help_cmd = reg.get("help")
    assert help_cmd is not None

    out_normal = asyncio.run(
        help_cmd.handler(
            CommandContext(chat_id=1, user_id=1, role=Role.NORMAL, command_name="help", argument=None)
        )
    )
    assert out_normal is not None
    assert "/ping" in out_normal
    assert "/help" in out_normal
    assert "/role" in out_normal
    assert "/setrole" not in out_normal


def test_role_command() -> None:
    reg = create_builtin_registry()
    role_cmd = reg.get("role")
    assert role_cmd is not None

    out = asyncio.run(
        role_cmd.handler(
            CommandContext(chat_id=1, user_id=1, role=Role.VIP, command_name="role", argument=None)
        )
    )
    assert out == "your role: vip"


def test_ignore_blocked_on_registry_level() -> None:
    reg = create_builtin_registry()
    assert reg.is_allowed("ping", Role.IGNORE) is False


def test_help_admin_contains_setrole() -> None:
    reg = create_builtin_registry()
    help_cmd = reg.get("help")
    assert help_cmd is not None

    out_admin = asyncio.run(
        help_cmd.handler(
            CommandContext(chat_id=1, user_id=1, role=Role.ADMIN, command_name="help", argument=None)
        )
    )
    assert out_admin is not None
    assert "/setrole" in out_admin
