import asyncio

from amo_bot.auth.roles import Role
from amo_bot.telegram.commands import create_builtin_registry
from amo_bot.telegram.dispatcher import Dispatcher, MessagePersistence
from amo_bot.telegram.role_resolver import InMemoryRoleResolver


def test_dispatcher_routes_command_and_calls_send() -> None:
    sent: list[tuple[int, str]] = []

    async def fake_send(chat_id: int, text: str, message_thread_id: int | None = None) -> object:
        sent.append((chat_id, text, message_thread_id))
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

    assert sent == [(99, "pong", None)]


def test_dispatcher_ignores_non_message_updates() -> None:
    sent: list[tuple[int, str]] = []

    async def fake_send(chat_id: int, text: str, message_thread_id: int | None = None) -> object:
        sent.append((chat_id, text, message_thread_id))
        return {"ok": True}

    dispatcher = Dispatcher(
        command_registry=create_builtin_registry(),
        role_resolver=InMemoryRoleResolver({42: Role.NORMAL}),
        send_text=fake_send,
        bot_username="BotName",
    )

    asyncio.run(dispatcher.handle_raw_update({"update_id": 8}))
    assert sent == []


def test_dispatcher_handles_test_command_with_markup_sender() -> None:
    sent_text: list[tuple[int, str, int | None]] = []
    sent_markup: list[tuple[int, str, dict[str, object], int | None]] = []

    async def fake_send(chat_id: int, text: str, message_thread_id: int | None = None) -> object:
        sent_text.append((chat_id, text, message_thread_id))
        return {"ok": True}

    async def fake_send_markup(
        chat_id: int,
        text: str,
        reply_markup: dict[str, object],
        message_thread_id: int | None = None,
    ) -> object:
        sent_markup.append((chat_id, text, reply_markup, message_thread_id))
        return {"ok": True}

    dispatcher = Dispatcher(
        command_registry=create_builtin_registry(),
        role_resolver=InMemoryRoleResolver({42: Role.ADMIN}),
        send_text=fake_send,
        send_markup=fake_send_markup,
        bot_username="BotName",
    )

    raw_update = {
        "update_id": 80,
        "message": {
            "message_id": 12,
            "from": {"id": 42, "is_bot": False, "first_name": "T", "username": "tester"},
            "chat": {"id": 99, "type": "private"},
            "text": "/test",
        },
    }

    asyncio.run(dispatcher.handle_raw_update(raw_update))

    assert sent_text == []
    assert sent_markup == [
        (
            99,
            "Inline-Button-Test: Bitte klicken.",
            {"inline_keyboard": [[{"text": "✅ Test Button", "callback_data": "test:ok"}]]},
            None,
        )
    ]


def test_dispatcher_handles_test_command_in_group_by_sending_private_markup() -> None:
    sent_text: list[tuple[int, str, int | None]] = []
    sent_private_markup: list[tuple[int, str, dict[str, object]]] = []

    async def fake_send(chat_id: int, text: str, message_thread_id: int | None = None) -> object:
        sent_text.append((chat_id, text, message_thread_id))
        return {"ok": True}

    async def fake_send_private_markup(chat_id: int, text: str, reply_markup: dict[str, object]) -> object:
        sent_private_markup.append((chat_id, text, reply_markup))
        return {"ok": True}

    dispatcher = Dispatcher(
        command_registry=create_builtin_registry(),
        role_resolver=InMemoryRoleResolver({42: Role.ADMIN}),
        send_text=fake_send,
        send_markup=None,
        send_private_markup=fake_send_private_markup,
        bot_username="BotName",
    )

    raw_update = {
        "update_id": 801,
        "message": {
            "message_id": 12,
            "from": {"id": 42, "is_bot": False, "first_name": "T", "username": "tester"},
            "chat": {"id": -1001, "type": "group"},
            "text": "/test",
        },
    }

    asyncio.run(dispatcher.handle_raw_update(raw_update))

    assert sent_private_markup == [
        (
            42,
            "Inline-Button-Test: Bitte klicken.",
            {"inline_keyboard": [[{"text": "✅ Test Button", "callback_data": "test:ok"}]]},
        )
    ]
    assert sent_text == [(-1001, "Ich habe dir den Button-Test privat geschickt.", None)]


def test_dispatcher_handles_test_command_in_forum_supergroup_by_sending_private_markup() -> None:
    sent_text: list[tuple[int, str, int | None]] = []
    sent_private_markup: list[tuple[int, str, dict[str, object]]] = []

    async def fake_send(chat_id: int, text: str, message_thread_id: int | None = None) -> object:
        sent_text.append((chat_id, text, message_thread_id))
        return {"ok": True}

    async def fake_send_private_markup(chat_id: int, text: str, reply_markup: dict[str, object]) -> object:
        sent_private_markup.append((chat_id, text, reply_markup))
        return {"ok": True}

    dispatcher = Dispatcher(
        command_registry=create_builtin_registry(),
        role_resolver=InMemoryRoleResolver({900000001: Role.ADMIN}),
        send_text=fake_send,
        send_markup=None,
        send_private_markup=fake_send_private_markup,
        bot_username="BotName",
    )

    raw_update = {
        "update_id": 8011,
        "message": {
            "message_id": 12,
            "message_thread_id": 872,
            "from": {"id": 900000001, "is_bot": False, "first_name": "Matze", "username": "tester"},
            "chat": {"id": -1003997137641, "type": "supergroup"},
            "text": "/test",
        },
    }

    asyncio.run(dispatcher.handle_raw_update(raw_update))

    assert sent_private_markup == [
        (
            900000001,
            "Inline-Button-Test: Bitte klicken.",
            {"inline_keyboard": [[{"text": "✅ Test Button", "callback_data": "test:ok"}]]},
        )
    ]
    assert sent_text == [(-1003997137641, "Ich habe dir den Button-Test privat geschickt.", 872)]


def test_dispatcher_handles_test_command_in_group_when_private_dm_fails() -> None:
    sent_text: list[tuple[int, str, int | None]] = []

    async def fake_send(chat_id: int, text: str, message_thread_id: int | None = None) -> object:
        sent_text.append((chat_id, text, message_thread_id))
        return {"ok": True}

    async def fake_send_private_markup(chat_id: int, text: str, reply_markup: dict[str, object]) -> object:
        raise RuntimeError("Forbidden: bot was blocked by the user")

    dispatcher = Dispatcher(
        command_registry=create_builtin_registry(),
        role_resolver=InMemoryRoleResolver({42: Role.ADMIN}),
        send_text=fake_send,
        send_markup=None,
        send_private_markup=fake_send_private_markup,
        bot_username="BotName",
    )

    raw_update = {
        "update_id": 802,
        "message": {
            "message_id": 12,
            "from": {"id": 42, "is_bot": False, "first_name": "T", "username": "tester"},
            "chat": {"id": -1001, "type": "supergroup"},
            "text": "/test",
        },
    }

    asyncio.run(dispatcher.handle_raw_update(raw_update))

    assert sent_text == [
        (
            -1001,
            "Ich kann dir aktuell keine private Nachricht senden. Bitte starte den Bot zuerst privat mit /start.",
            None,
        )
    ]


def test_dispatcher_handles_test_callback_with_answer_callback() -> None:
    callback_answers: list[tuple[str, str | None]] = []

    async def fake_send(chat_id: int, text: str, message_thread_id: int | None = None) -> object:
        return {"ok": True}

    async def fake_answer_callback(callback_query_id: str, text: str | None = None) -> object:
        callback_answers.append((callback_query_id, text))
        return True

    dispatcher = Dispatcher(
        command_registry=create_builtin_registry(),
        role_resolver=InMemoryRoleResolver({42: Role.ADMIN}),
        send_text=fake_send,
        answer_callback=fake_answer_callback,
        bot_username="BotName",
    )

    raw_update = {
        "update_id": 81,
        "callback_query": {
            "id": "cb-1",
            "from": {"id": 42, "is_bot": False, "first_name": "T", "username": "tester"},
            "message": {
                "message_id": 20,
                "chat": {"id": 99, "type": "private"},
                "from": {"id": 99, "is_bot": True, "first_name": "Bot"},
                "text": "Inline-Button-Test: Bitte klicken.",
            },
            "data": "test:ok",
        },
    }

    asyncio.run(dispatcher.handle_raw_update(raw_update))

    assert callback_answers == [("cb-1", "Button test ok")]


def test_dispatcher_ignores_unknown_callback_data() -> None:
    sent: list[tuple[int, str, int | None]] = []
    callback_answers: list[tuple[str, str | None]] = []

    async def fake_send(chat_id: int, text: str, message_thread_id: int | None = None) -> object:
        sent.append((chat_id, text, message_thread_id))
        return {"ok": True}

    async def fake_answer_callback(callback_query_id: str, text: str | None = None) -> object:
        callback_answers.append((callback_query_id, text))
        return True

    dispatcher = Dispatcher(
        command_registry=create_builtin_registry(),
        role_resolver=InMemoryRoleResolver({42: Role.ADMIN}),
        send_text=fake_send,
        answer_callback=fake_answer_callback,
        bot_username="BotName",
    )

    raw_update = {
        "update_id": 82,
        "callback_query": {
            "id": "cb-2",
            "from": {"id": 42, "is_bot": False, "first_name": "T", "username": "tester"},
            "message": {
                "message_id": 21,
                "chat": {"id": 99, "type": "private"},
                "from": {"id": 99, "is_bot": True, "first_name": "Bot"},
                "text": "Inline-Button-Test: Bitte klicken.",
            },
            "data": "unknown:x",
        },
    }

    asyncio.run(dispatcher.handle_raw_update(raw_update))

    assert sent == []
    assert callback_answers == []


def test_dispatcher_blocks_ignore_role() -> None:
    sent: list[tuple[int, str]] = []

    async def fake_send(chat_id: int, text: str, message_thread_id: int | None = None) -> object:
        sent.append((chat_id, text, message_thread_id))
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

    async def fake_send(chat_id: int, text: str, message_thread_id: int | None = None) -> object:
        sent.append((chat_id, text, message_thread_id))
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

    async def fake_send(chat_id: int, text: str, message_thread_id: int | None = None) -> object:
        sent.append((chat_id, text, message_thread_id))
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
    assert sent == [(99, "pong", None)]


class _FailingPersistence(MessagePersistence):
    async def persist_message(self, message: object) -> None:
        raise RuntimeError("db down")


def test_dispatcher_continues_when_message_persistence_fails() -> None:
    sent: list[tuple[int, str]] = []

    async def fake_send(chat_id: int, text: str, message_thread_id: int | None = None) -> object:
        sent.append((chat_id, text, message_thread_id))
        return {"ok": True}

    dispatcher = Dispatcher(
        command_registry=create_builtin_registry(),
        role_resolver=InMemoryRoleResolver({42: Role.NORMAL}),
        send_text=fake_send,
        bot_username="ConfiguredBot",
        message_persistence=_FailingPersistence(),
    )

    raw_update = {
        "update_id": 12,
        "message": {
            "message_id": 16,
            "from": {"id": 42, "is_bot": False, "first_name": "T", "username": "tester"},
            "chat": {"id": 99, "type": "private"},
            "text": "/ping",
        },
    }

    asyncio.run(dispatcher.handle_raw_update(raw_update))
    assert sent == [(99, "pong", None)]


def test_dispatcher_ignores_suffixed_command_for_other_bot_username() -> None:
    sent: list[tuple[int, str]] = []

    async def fake_send(chat_id: int, text: str, message_thread_id: int | None = None) -> object:
        sent.append((chat_id, text, message_thread_id))
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


def test_dispatcher_passes_message_thread_id_to_send() -> None:
    sent: list[tuple[int, str, int | None]] = []

    async def fake_send(chat_id: int, text: str, message_thread_id: int | None = None) -> object:
        sent.append((chat_id, text, message_thread_id))
        return {"ok": True}

    dispatcher = Dispatcher(
        command_registry=create_builtin_registry(),
        role_resolver=InMemoryRoleResolver({42: Role.NORMAL}),
        send_text=fake_send,
        bot_username="BotName",
    )

    raw_update = {
        "update_id": 13,
        "message": {
            "message_id": 17,
            "message_thread_id": 872,
            "from": {"id": 42, "is_bot": False, "first_name": "T", "username": "tester"},
            "chat": {"id": -1003997137641, "type": "supergroup"},
            "text": "/ping",
        },
    }

    asyncio.run(dispatcher.handle_raw_update(raw_update))

    assert sent == [(-1003997137641, "pong", 872)]

def test_dispatcher_ignores_messages_from_bot_users() -> None:
    sent: list[tuple[int, str]] = []

    async def fake_send(chat_id: int, text: str, message_thread_id: int | None = None) -> object:
        sent.append((chat_id, text, message_thread_id))
        return {"ok": True}

    dispatcher = Dispatcher(
        command_registry=create_builtin_registry(),
        role_resolver=InMemoryRoleResolver({42: Role.NORMAL}),
        send_text=fake_send,
        bot_username="BotName",
    )

    raw_update = {
        "update_id": 14,
        "message": {
            "message_id": 18,
            "from": {"id": 42, "is_bot": True, "first_name": "T", "username": "tester"},
            "chat": {"id": 99, "type": "private"},
            "text": "/ping",
        },
    }

    asyncio.run(dispatcher.handle_raw_update(raw_update))

    assert sent == []


def test_consent_buttons_notify_owner(tmp_path) -> None:
    from amo_bot.db.init_db import init_db
    from amo_bot.db.base import create_session_factory
    from amo_bot.db.repositories import UserRoleRepository
    from amo_bot.telegram.owner_notify import OwnerNotifier

    db_url = f"sqlite:///{tmp_path / 'dispatcher_owner_notify_buttons.db'}"
    init_db(db_url)
    sf = create_session_factory(db_url)
    with sf() as session:
        UserRoleRepository(session).upsert_discovered_user(
            telegram_user_id=4201,
            username="u4201",
            first_name="U",
            last_name=None,
        )

    sent_owner: list[str] = []

    async def fake_send(chat_id: int, text: str, message_thread_id: int | None = None) -> object:
        return {"ok": True}

    async def fake_owner_send(_chat_id: int, text: str) -> object:
        sent_owner.append(text)
        return {"ok": True}

    async def fake_answer_callback(callback_query_id: str, text: str | None = None) -> object:
        return True

    dispatcher = Dispatcher(
        command_registry=create_builtin_registry(database_url=db_url),
        role_resolver=InMemoryRoleResolver({4201: Role.NORMAL}),
        send_text=fake_send,
        answer_callback=fake_answer_callback,
        bot_username="BotName",
        database_url=db_url,
        owner_notifier=OwnerNotifier(owner_telegram_user_id=9999, send_private_text=fake_owner_send),
    )

    raw_accept = {
        "update_id": 901,
        "callback_query": {
            "id": "cb-accept",
            "from": {"id": 4201, "is_bot": False, "first_name": "T", "username": "tester"},
            "message": {
                "message_id": 20,
                "chat": {"id": 4201, "type": "private"},
                "from": {"id": 99, "is_bot": True, "first_name": "Bot"},
                "text": "consent",
            },
            "data": "consent:accept",
        },
    }
    raw_decline = {
        "update_id": 902,
        "callback_query": {
            "id": "cb-decline",
            "from": {"id": 4201, "is_bot": False, "first_name": "T", "username": "tester"},
            "message": {
                "message_id": 21,
                "chat": {"id": 4201, "type": "private"},
                "from": {"id": 99, "is_bot": True, "first_name": "Bot"},
                "text": "consent",
            },
            "data": "consent:decline",
        },
    }

    asyncio.run(dispatcher.handle_raw_update(raw_accept))
    asyncio.run(dispatcher.handle_raw_update(raw_decline))

    assert len(sent_owner) == 2
    assert "Consent akzeptiert" in sent_owner[0]
    assert "Consent abgelehnt" in sent_owner[1]


def test_dispatcher_help_supports_explicit_locale_override() -> None:
    sent: list[tuple[int, str, int | None]] = []

    async def fake_send(chat_id: int, text: str, message_thread_id: int | None = None) -> object:
        sent.append((chat_id, text, message_thread_id))
        return {"ok": True}

    dispatcher = Dispatcher(
        command_registry=create_builtin_registry(),
        role_resolver=InMemoryRoleResolver({42: Role.NORMAL, 43: Role.NORMAL}),
        send_text=fake_send,
        bot_username="BotName",
    )

    raw_update_de = {
        "update_id": 1400,
        "message": {
            "message_id": 17,
            "from": {"id": 42, "is_bot": False, "first_name": "T", "username": "tester", "language_code": "en"},
            "chat": {"id": 98, "type": "private"},
            "text": "/help de",
        },
    }
    raw_update_en = {
        "update_id": 14001,
        "message": {
            "message_id": 18,
            "from": {"id": 43, "is_bot": False, "first_name": "T", "username": "tester", "language_code": "de"},
            "chat": {"id": 99, "type": "private"},
            "text": "/help en",
        },
    }

    asyncio.run(dispatcher.handle_raw_update(raw_update_de))
    asyncio.run(dispatcher.handle_raw_update(raw_update_en))

    assert sent[0][0] == 98
    assert sent[0][1].startswith("Verfügbare Befehle:")
    assert "/ping - Bot-Erreichbarkeit prüfen" in sent[0][1]

    assert sent[1][0] == 99
    assert sent[1][1].startswith("available commands:")
    assert "/ping - Check bot health" in sent[1][1]


def test_dispatcher_sends_bilingual_unknown_command_reply() -> None:
    sent: list[tuple[int, str, int | None]] = []

    async def fake_send(chat_id: int, text: str, message_thread_id: int | None = None) -> object:
        sent.append((chat_id, text, message_thread_id))
        return {"ok": True}

    dispatcher = Dispatcher(
        command_registry=create_builtin_registry(),
        role_resolver=InMemoryRoleResolver({42: Role.NORMAL, 43: Role.NORMAL}),
        send_text=fake_send,
        bot_username="BotName",
    )

    raw_update_de = {
        "update_id": 1401,
        "message": {
            "message_id": 18,
            "from": {"id": 42, "is_bot": False, "first_name": "T", "username": "tester", "language_code": "de"},
            "chat": {"id": 99, "type": "private"},
            "text": "/doesnotexist",
        },
    }
    raw_update_en = {
        "update_id": 1402,
        "message": {
            "message_id": 19,
            "from": {"id": 43, "is_bot": False, "first_name": "T", "username": "tester", "language_code": "en"},
            "chat": {"id": 100, "type": "private"},
            "text": "/doesnotexist",
        },
    }

    asyncio.run(dispatcher.handle_raw_update(raw_update_de))
    asyncio.run(dispatcher.handle_raw_update(raw_update_en))

    assert sent == [
        (99, "Unbekannter Befehl: /doesnotexist. Nutze /help für verfügbare Befehle.", None),
        (100, "Unknown command: /doesnotexist. Use /help for available commands.", None),
    ]


def test_dispatcher_does_not_send_unknown_fallback_for_handled_plugin_command() -> None:
    sent: list[tuple[int, str, int | None]] = []
    plugin_calls: list[tuple[str, str | None, int, int, int | None]] = []

    async def fake_send(chat_id: int, text: str, message_thread_id: int | None = None) -> object:
        sent.append((chat_id, text, message_thread_id))
        return {"ok": True}

    class _HandledPluginExecutor:
        async def execute(self, *, actor, invocation):
            plugin_calls.append(
                (
                    invocation.command_name,
                    invocation.argument,
                    invocation.chat_id,
                    invocation.message_id,
                    invocation.message_thread_id,
                )
            )
            return True

    dispatcher = Dispatcher(
        command_registry=create_builtin_registry(),
        role_resolver=InMemoryRoleResolver({42: Role.NORMAL}),
        send_text=fake_send,
        bot_username="BotName",
        plugin_command_executor=_HandledPluginExecutor(),
    )

    raw_update = {
        "update_id": 1501,
        "message": {
            "message_id": 77,
            "from": {"id": 42, "is_bot": False, "first_name": "T", "username": "tester", "language_code": "en"},
            "chat": {"id": 101, "type": "private"},
            "text": "/plugincmd arg1",
        },
    }

    asyncio.run(dispatcher.handle_raw_update(raw_update))

    assert plugin_calls == [("plugincmd", "arg1", 101, 77, None)]
    assert sent == []


def test_dispatcher_sends_unknown_fallback_for_falsey_plugin_result() -> None:
    sent: list[tuple[int, str, int | None]] = []

    async def fake_send(chat_id: int, text: str, message_thread_id: int | None = None) -> object:
        sent.append((chat_id, text, message_thread_id))
        return {"ok": True}

    class _FalseyPluginExecutor:
        async def execute(self, *, actor, invocation):
            return None

    dispatcher = Dispatcher(
        command_registry=create_builtin_registry(),
        role_resolver=InMemoryRoleResolver({42: Role.NORMAL}),
        send_text=fake_send,
        bot_username="BotName",
        plugin_command_executor=_FalseyPluginExecutor(),
    )

    raw_update = {
        "update_id": 1503,
        "message": {
            "message_id": 79,
            "from": {"id": 42, "is_bot": False, "first_name": "T", "username": "tester", "language_code": "en"},
            "chat": {"id": 101, "type": "private"},
            "text": "/plugincmd arg1",
        },
    }

    asyncio.run(dispatcher.handle_raw_update(raw_update))

    assert sent == [(101, "Unknown command: /plugincmd. Use /help for available commands.", None)]


def test_dispatcher_ping_uses_locale_neutral_pong_for_de_and_en() -> None:
    sent: list[tuple[int, str, int | None]] = []

    async def fake_send(chat_id: int, text: str, message_thread_id: int | None = None) -> object:
        sent.append((chat_id, text, message_thread_id))
        return {"ok": True}

    dispatcher = Dispatcher(
        command_registry=create_builtin_registry(),
        role_resolver=InMemoryRoleResolver({42: Role.NORMAL, 43: Role.NORMAL}),
        send_text=fake_send,
        bot_username="BotName",
    )

    raw_update_de = {
        "update_id": 1601,
        "message": {
            "message_id": 81,
            "from": {"id": 42, "is_bot": False, "first_name": "T", "username": "tester", "language_code": "de"},
            "chat": {"id": 201, "type": "private"},
            "text": "/ping",
        },
    }
    raw_update_en = {
        "update_id": 1602,
        "message": {
            "message_id": 82,
            "from": {"id": 43, "is_bot": False, "first_name": "T", "username": "tester", "language_code": "en"},
            "chat": {"id": 202, "type": "private"},
            "text": "/ping",
        },
    }

    asyncio.run(dispatcher.handle_raw_update(raw_update_de))
    asyncio.run(dispatcher.handle_raw_update(raw_update_en))

    assert sent == [
        (201, "pong", None),
        (202, "pong", None),
    ]
