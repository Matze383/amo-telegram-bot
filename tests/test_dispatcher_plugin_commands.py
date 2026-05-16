from __future__ import annotations

import asyncio

from amo_bot.auth.roles import Role
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
            )
        )
        return True


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
    assert recorder.calls == [("admin", 55, "plug", 111, 88)]
