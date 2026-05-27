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
    assert out == "deine rolle: vip"


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
    assert "/test" in out_admin


def test_test_command_returns_inline_button_markup() -> None:
    reg = create_builtin_registry()
    test_cmd = reg.get("test")
    assert test_cmd is not None

    out = asyncio.run(
        test_cmd.handler(
            CommandContext(chat_id=1, user_id=1, role=Role.ADMIN, command_name="test", argument=None)
        )
    )
    assert isinstance(out, dict)
    assert out.get("text") == "Inline-Button-Test: Bitte klicken."
    assert out.get("reply_markup") == {
        "inline_keyboard": [[{"text": "✅ Test Button", "callback_data": "test:ok"}]]
    }


def test_memory_profile_commands_private_scope_update_view_delete() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        db_url = f"sqlite:///{tmp}/mem.sqlite3"
        reg = create_builtin_registry(database_url=db_url)
        set_cmd = reg.get("memory_profile_set")
        view_cmd = reg.get("memory_profile")
        del_cmd = reg.get("memory_profile_delete")
        assert set_cmd and view_cmd and del_cmd

        out_set = asyncio.run(set_cmd.handler(CommandContext(chat_id=101, user_id=101, role=Role.NORMAL, command_name="memory_profile_set", argument="language=de,verbosity=high,password=secret")))
        assert "Gespeichert" in out_set or "Stored" in out_set

        out_view = asyncio.run(view_cmd.handler(CommandContext(chat_id=101, user_id=101, role=Role.NORMAL, command_name="memory_profile", argument=None)))
        assert "language" in out_view
        assert "verbosity" in out_view
        assert "password" not in out_view

        out_other = asyncio.run(view_cmd.handler(CommandContext(chat_id=202, user_id=202, role=Role.NORMAL, command_name="memory_profile", argument=None)))
        assert "Kein Profil" in out_other or "No profile" in out_other

        out_del = asyncio.run(del_cmd.handler(CommandContext(chat_id=101, user_id=101, role=Role.NORMAL, command_name="memory_profile_delete", argument=None)))
        assert "gelöscht" in out_del or "deleted" in out_del

        out_after = asyncio.run(view_cmd.handler(CommandContext(chat_id=101, user_id=101, role=Role.NORMAL, command_name="memory_profile", argument=None)))
        assert "Kein Profil" in out_after or "No profile" in out_after


def test_memory_profile_set_rejects_when_no_allowed_fields() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        db_url = f"sqlite:///{tmp}/mem.sqlite3"
        reg = create_builtin_registry(database_url=db_url)
        set_cmd = reg.get("memory_profile_set")
        assert set_cmd
        out = asyncio.run(set_cmd.handler(CommandContext(chat_id=1, user_id=1, role=Role.NORMAL, command_name="memory_profile_set", argument="password=abc,token=def")))
        assert "Keine erlaubten" in out or "No allowed" in out
