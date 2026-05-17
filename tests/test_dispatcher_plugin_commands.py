from __future__ import annotations

import asyncio

from amo_bot.auth.roles import Role
from amo_bot.db.base import create_session_factory
from amo_bot.db.init_db import init_db
from amo_bot.db.repositories import PrivateChatPolicyRepository
from amo_bot.telegram.commands import CommandRegistry, StaticRoleResolver
from amo_bot.telegram.dispatcher import Dispatcher


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, str, int, int]] = []

    async def execute(self, *, actor, invocation) -> bool:
        self.calls.append(
            (
                actor.role.value,
                actor.telegram_user_id,
                invocation.command_name,
                invocation.chat_id,
                invocation.message_id,
                len(invocation.attachments),
            )
        )
        return True


def _init_policy_db(tmp_path) -> str:
    database_url = f"sqlite:///{tmp_path / 'plugin-policy.sqlite3'}"
    init_db(database_url)
    session_factory = create_session_factory(database_url)
    with session_factory() as session:
        PrivateChatPolicyRepository(session).update_policy(
            min_ai_role="normal",
            min_general_command_role="normal",
            min_plugin_command_role="vip",
        )
        session.commit()
    return database_url


def test_dispatcher_routes_unknown_command_to_plugin_executor() -> None:
    registry = CommandRegistry()
    recorder = _Recorder()

    sent: list[tuple[int, str]] = []

    async def _send(chat_id: int, text: str):
        sent.append((chat_id, text))
        return {"ok": True}

    dispatcher = Dispatcher(
        command_registry=registry,
        role_resolver=StaticRoleResolver(mapping={55: Role.ADMIN}),
        send_text=_send,
        plugin_command_executor=recorder,
    )

    raw_update = {
        "update_id": 77,
        "message": {
            "message_id": 88,
            "text": "/plug demo",
            "chat": {"id": 111, "type": "private"},
            "from": {"id": 55, "is_bot": False, "first_name": "A"},
        },
    }

    asyncio.run(dispatcher.handle_raw_update(raw_update))

    assert sent == []
    assert recorder.calls == [("admin", 55, "plug", 111, 88, 0)]


def test_private_plugin_command_blocked_below_min_plugin_role(tmp_path) -> None:
    registry = CommandRegistry()
    recorder = _Recorder()

    async def _send(chat_id: int, text: str):
        return {"ok": True}

    database_url = _init_policy_db(tmp_path)

    dispatcher = Dispatcher(
        command_registry=registry,
        role_resolver=StaticRoleResolver(mapping={55: Role.NORMAL}),
        send_text=_send,
        plugin_command_executor=recorder,
        database_url=database_url,
    )

    raw_update = {
        "update_id": 78,
        "message": {
            "message_id": 89,
            "text": "/plug demo",
            "chat": {"id": 111, "type": "private"},
            "from": {"id": 55, "is_bot": False, "first_name": "A"},
        },
    }

    asyncio.run(dispatcher.handle_raw_update(raw_update))

    assert recorder.calls == []


def test_private_plugin_command_allowed_at_min_plugin_role(tmp_path) -> None:
    registry = CommandRegistry()
    recorder = _Recorder()

    async def _send(chat_id: int, text: str):
        return {"ok": True}

    database_url = _init_policy_db(tmp_path)

    dispatcher = Dispatcher(
        command_registry=registry,
        role_resolver=StaticRoleResolver(mapping={55: Role.VIP}),
        send_text=_send,
        plugin_command_executor=recorder,
        database_url=database_url,
    )

    raw_update = {
        "update_id": 79,
        "message": {
            "message_id": 90,
            "text": "/plug demo",
            "chat": {"id": 111, "type": "private"},
            "from": {"id": 55, "is_bot": False, "first_name": "A"},
        },
    }

    asyncio.run(dispatcher.handle_raw_update(raw_update))

    assert recorder.calls == [("vip", 55, "plug", 111, 90, 0)]


def test_private_plugin_command_ignore_role_does_not_execute_plugin(tmp_path) -> None:
    registry = CommandRegistry()
    recorder = _Recorder()

    async def _send(chat_id: int, text: str):
        return {"ok": True}

    database_url = _init_policy_db(tmp_path)

    dispatcher = Dispatcher(
        command_registry=registry,
        role_resolver=StaticRoleResolver(mapping={55: Role.IGNORE}),
        send_text=_send,
        plugin_command_executor=recorder,
        database_url=database_url,
    )

    raw_update = {
        "update_id": 80,
        "message": {
            "message_id": 91,
            "text": "/plug demo",
            "chat": {"id": 111, "type": "private"},
            "from": {"id": 55, "is_bot": False, "first_name": "A"},
        },
    }

    asyncio.run(dispatcher.handle_raw_update(raw_update))

    assert recorder.calls == []


def test_group_plugin_command_not_gated_by_private_plugin_policy(tmp_path) -> None:
    registry = CommandRegistry()
    recorder = _Recorder()

    async def _send(chat_id: int, text: str):
        return {"ok": True}

    database_url = _init_policy_db(tmp_path)

    dispatcher = Dispatcher(
        command_registry=registry,
        role_resolver=StaticRoleResolver(mapping={55: Role.NORMAL}),
        send_text=_send,
        plugin_command_executor=recorder,
        database_url=database_url,
    )

    raw_update = {
        "update_id": 81,
        "message": {
            "message_id": 92,
            "text": "/plug demo",
            "chat": {"id": -222, "type": "group"},
            "from": {"id": 55, "is_bot": False, "first_name": "A"},
        },
    }

    asyncio.run(dispatcher.handle_raw_update(raw_update))

    assert recorder.calls == [("normal", 55, "plug", -222, 92, 0)]


def test_dispatcher_passes_image_attachments_to_plugin_invocation() -> None:
    registry = CommandRegistry()
    recorder = _Recorder()

    async def _send(chat_id: int, text: str):
        return {"ok": True}

    dispatcher = Dispatcher(
        command_registry=registry,
        role_resolver=StaticRoleResolver(mapping={55: Role.ADMIN}),
        send_text=_send,
        plugin_command_executor=recorder,
    )

    raw_update = {
        "update_id": 82,
        "message": {
            "message_id": 93,
            "text": "/plug demo",
            "chat": {"id": 111, "type": "private"},
            "from": {"id": 55, "is_bot": False, "first_name": "A"},
            "photo": [
                {"file_id": "small", "width": 10, "height": 10},
                {"file_id": "large", "width": 100, "height": 80},
            ],
        },
    }

    asyncio.run(dispatcher.handle_raw_update(raw_update))

    assert recorder.calls == [("admin", 55, "plug", 111, 93, 1)]
