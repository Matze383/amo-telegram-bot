import asyncio

from amo_bot.auth.roles import Role
from amo_bot.telegram.commands import create_builtin_registry
from amo_bot.telegram.dispatcher import Dispatcher
from amo_bot.telegram.role_resolver import InMemoryRoleResolver


def test_dispatcher_routes_command_and_calls_send() -> None:
    sent: list[tuple[int, str]] = []

    async def fake_send(chat_id: int, text: str) -> object:
        sent.append((chat_id, text))
        return {"ok": True}

    dispatcher = Dispatcher(
        command_registry=create_builtin_registry(),
        role_resolver=InMemoryRoleResolver({42: Role.NORMAL}),
        send_text=fake_send,
        bot_username="BotName",
    )

    raw_update = {
        "update_id": 7,
        "message": {
            "message_id": 12,
            "from": {"id": 42, "is_bot": False, "first_name": "T", "username": "tester"},
            "chat": {"id": 99, "type": "private"},
            "text": "/ping",
        },
    }

    asyncio.run(dispatcher.handle_raw_update(raw_update))

    assert sent == [(99, "pong")]


def test_dispatcher_ignores_non_message_updates() -> None:
    sent: list[tuple[int, str]] = []

    async def fake_send(chat_id: int, text: str) -> object:
        sent.append((chat_id, text))
        return {"ok": True}

    dispatcher = Dispatcher(
        command_registry=create_builtin_registry(),
        role_resolver=InMemoryRoleResolver({42: Role.NORMAL}),
        send_text=fake_send,
        bot_username="BotName",
    )

    asyncio.run(dispatcher.handle_raw_update({"update_id": 8, "callback_query": {"id": "x"}}))
    assert sent == []


def test_dispatcher_blocks_ignore_role() -> None:
    sent: list[tuple[int, str]] = []

    async def fake_send(chat_id: int, text: str) -> object:
        sent.append((chat_id, text))
        return {"ok": True}

    dispatcher = Dispatcher(
        command_registry=create_builtin_registry(),
        role_resolver=InMemoryRoleResolver({42: Role.IGNORE}),
        send_text=fake_send,
        bot_username="BotName",
    )

    raw_update = {
        "update_id": 9,
        "message": {
            "message_id": 13,
            "from": {"id": 42, "is_bot": False, "first_name": "T", "username": "tester"},
            "chat": {"id": 99, "type": "private"},
            "text": "/ping",
        },
    }

    asyncio.run(dispatcher.handle_raw_update(raw_update))
    assert sent == []


def test_dispatcher_ignores_suffixed_command_without_configured_bot_username() -> None:
    sent: list[tuple[int, str]] = []

    async def fake_send(chat_id: int, text: str) -> object:
        sent.append((chat_id, text))
        return {"ok": True}

    dispatcher = Dispatcher(
        command_registry=create_builtin_registry(),
        role_resolver=InMemoryRoleResolver({42: Role.NORMAL}),
        send_text=fake_send,
        bot_username=None,
    )

    raw_update = {
        "update_id": 10,
        "message": {
            "message_id": 14,
            "from": {"id": 42, "is_bot": False, "first_name": "T", "username": "tester"},
            "chat": {"id": 99, "type": "private"},
            "text": "/ping@OtherBot",
        },
    }

    asyncio.run(dispatcher.handle_raw_update(raw_update))
    assert sent == []


def test_dispatcher_accepts_suffixed_command_for_configured_bot_username() -> None:
    sent: list[tuple[int, str]] = []

    async def fake_send(chat_id: int, text: str) -> object:
        sent.append((chat_id, text))
        return {"ok": True}

    dispatcher = Dispatcher(
        command_registry=create_builtin_registry(),
        role_resolver=InMemoryRoleResolver({42: Role.NORMAL}),
        send_text=fake_send,
        bot_username="ConfiguredBot",
    )

    raw_update = {
        "update_id": 11,
        "message": {
            "message_id": 15,
            "from": {"id": 42, "is_bot": False, "first_name": "T", "username": "tester"},
            "chat": {"id": 99, "type": "private"},
            "text": "/ping@ConfiguredBot",
        },
    }

    asyncio.run(dispatcher.handle_raw_update(raw_update))
    assert sent == [(99, "pong")]


def test_dispatcher_ignores_suffixed_command_for_other_bot_username() -> None:
    sent: list[tuple[int, str]] = []

    async def fake_send(chat_id: int, text: str) -> object:
        sent.append((chat_id, text))
        return {"ok": True}

    dispatcher = Dispatcher(
        command_registry=create_builtin_registry(),
        role_resolver=InMemoryRoleResolver({42: Role.NORMAL}),
        send_text=fake_send,
        bot_username="ConfiguredBot",
    )

    raw_update = {
        "update_id": 12,
        "message": {
            "message_id": 16,
            "from": {"id": 42, "is_bot": False, "first_name": "T", "username": "tester"},
            "chat": {"id": 99, "type": "private"},
            "text": "/ping@OtherBot",
        },
    }

    asyncio.run(dispatcher.handle_raw_update(raw_update))
    assert sent == []
