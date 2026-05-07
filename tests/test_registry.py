import pytest

from amo_bot.auth.roles import Role
from amo_bot.telegram.commands import Command, CommandContext, CommandRegistry


async def _ok_handler(_: CommandContext) -> str:
    return "ok"


def test_registry_permission_allowed_and_blocked() -> None:
    reg = CommandRegistry()
    reg.register(
        Command(
            name="ping",
            description="desc",
            allowed_roles={Role.NORMAL, Role.ADMIN, Role.OWNER, Role.VIP},
            handler=_ok_handler,
        )
    )

    assert reg.is_allowed("ping", Role.NORMAL) is True
    assert reg.is_allowed("ping", Role.IGNORE) is False


def test_registry_duplicate_command() -> None:
    reg = CommandRegistry()
    reg.register(Command(name="ping", description="desc", allowed_roles={Role.NORMAL}, handler=_ok_handler))
    with pytest.raises(ValueError):
        reg.register(Command(name="PING", description="desc2", allowed_roles={Role.NORMAL}, handler=_ok_handler))
