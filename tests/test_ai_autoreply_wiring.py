from __future__ import annotations

import asyncio
from unittest.mock import patch

from sqlalchemy import select

from amo_bot.db.base import create_session_factory
from amo_bot.db.init_db import init_db
from amo_bot.auth.roles import Role
from amo_bot.db.models import AuditEvent, DbRole, User
from amo_bot.db.repositories import ChatScopedRoleRepository, PrivateChatPolicyRepository, TopicAgentMemoryRepository
from amo_bot.telegram.commands import create_builtin_registry
from amo_bot.telegram.dispatcher import Dispatcher
from amo_bot.telegram.role_resolver import DBRoleResolver


class FakeAIService:
    def __init__(self, answer: str = "ai-ok") -> None:
        self.answer = answer
        self.prompts: list[str] = []

    async def ask(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.answer


class Sender:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str, int | None]] = []

    async def send_text(self, chat_id: int, text: str, message_thread_id: int | None = None) -> object:
        self.sent.append((chat_id, text, message_thread_id))
        return {"ok": True}


def _mk_update(*, uid: int, chat_id: int, chat_type: str, text: str, update_id: int, message_thread_id: int | None = None, reply_to_is_bot: bool = False, reply_to_message_id: int | None = None, reply_to_bot_username: str = "AmoBot") -> dict[str, object]:
    m: dict[str, object] = {
        "message_id": update_id + 100,
        "is_topic_message": message_thread_id is not None,
        "from": {"id": uid, "is_bot": False, "first_name": "U", "username": f"u{uid}"},
        "chat": {"id": chat_id, "type": chat_type},
        "text": text,
        "entities": [
            {
                "type": "mention",
                "offset": text.index("@AmoBot"),
                "length": len("@AmoBot"),
            }
        ] if "@AmoBot" in text else [],
    }
    if message_thread_id is not None:
        m["message_thread_id"] = message_thread_id
    if reply_to_is_bot:
        reply_message_id = reply_to_message_id if reply_to_message_id is not None else 999
        m["reply_to_message"] = {
            "message_id": reply_message_id,
            "from": {
                "id": 999,
                "is_bot": True,
                "first_name": "Bot",
                "username": reply_to_bot_username,
            },
        }
    return {"update_id": update_id, "message": m}


def _seed_user(
    db_url: str,
    *,
    user_id: int,
    role: str,
    consent: str = "accepted",
    group_chat_id: int | None = None,
    group_role: str | None = None,
) -> None:
    sf = create_session_factory(db_url)
    with sf() as session:
        role_map = {row.name: row.id for row in session.scalars(select(DbRole)).all()}
        session.add(User(telegram_user_id=user_id, role_id=role_map[role], consent_status=consent))
        session.commit()

    if group_chat_id is not None and group_role is not None:
        with sf() as session:
            ChatScopedRoleRepository(session).set_group_role(
                chat_id=group_chat_id,
                telegram_user_id=user_id,
                role=Role(group_role),
            )


def _mk_dispatcher(db_url: str, ai: FakeAIService, sender: Sender) -> Dispatcher:
    sf = create_session_factory(db_url)
    registry = create_builtin_registry(database_url=db_url, ai_service=ai)
    return Dispatcher(
        command_registry=registry,
        role_resolver=DBRoleResolver(sf),
        send_text=sender.send_text,
        bot_username="AmoBot",
        database_url=db_url,
        ai_service=ai,
    )


def test_active_mention_and_reply_send_ai_response_in_active_scopes(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'ai_autoreply_active.db'}"
    init_db(db_url)
    _seed_user(
        db_url,
        user_id=2000,
        role="vip",
        consent="accepted",
        group_chat_id=-1001,
        group_role="vip",
    )

    sf = create_session_factory(db_url)
    with sf() as session:
        repo = TopicAgentMemoryRepository(session)
        repo.upsert_config(scope_type="topic", chat_id=-1001, topic_id=10, ai_enabled=True)
        repo.upsert_config(scope_type="private_user", user_id=2000, ai_enabled=True)

    ai = FakeAIService(answer="ai-answer")
    sender = Sender()
    dispatcher = _mk_dispatcher(db_url, ai, sender)

    asyncio.run(
        dispatcher.handle_raw_update(
            _mk_update(uid=2000, chat_id=-1001, chat_type="supergroup", text="hi @AmoBot", update_id=1, message_thread_id=10)
        )
    )
    asyncio.run(
        dispatcher.handle_raw_update(
            _mk_update(uid=2000, chat_id=2000, chat_type="private", text="followup", update_id=2, reply_to_is_bot=True)
        )
    )

    assert sender.sent == [(-1001, "ai-answer", 10), (2000, "ai-answer", None)]
    assert len(ai.prompts) == 2
    assert "User message:\nhi" in ai.prompts[0]
    assert "You are the Telegram topic assistant @AmoBot" in ai.prompts[0]
    assert "Do not claim to be the underlying model/provider unless explicitly asked." in ai.prompts[0]
    assert "User message:\nfollowup" in ai.prompts[1]


def test_dynamic_bot_username_is_used_in_identity_prompt(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'ai_autoreply_dynamic_botname.db'}"
    init_db(db_url)
    _seed_user(db_url, user_id=2111, role="vip", consent="accepted")

    sf = create_session_factory(db_url)
    with sf() as session:
        repo = TopicAgentMemoryRepository(session)
        repo.upsert_config(scope_type="private_user", user_id=2111, ai_enabled=True)

    ai = FakeAIService(answer="ai-answer")
    sender = Sender()
    dispatcher = Dispatcher(
        command_registry=create_builtin_registry(database_url=db_url, ai_service=ai),
        role_resolver=DBRoleResolver(sf),
        send_text=sender.send_text,
        bot_username="SomeDynamicBot",
        database_url=db_url,
        ai_service=ai,
    )

    asyncio.run(
        dispatcher.handle_raw_update(
            _mk_update(uid=2111, chat_id=2111, chat_type="private", text="@SomeDynamicBot Test", update_id=3)
        )
    )

    assert sender.sent == [(2111, "ai-answer", None)]
    assert len(ai.prompts) == 1
    assert "@SomeDynamicBot Test" not in ai.prompts[0]
    assert "You are the Telegram topic assistant @SomeDynamicBot" in ai.prompts[0]
    assert "User message:\nTest" in ai.prompts[0]


def test_private_scope_enabled_plain_text_triggers_ai_autoreply(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'ai_autoreply_private_scope_enabled.db'}"
    init_db(db_url)
    _seed_user(db_url, user_id=2112, role="vip", consent="accepted")

    sf = create_session_factory(db_url)
    with sf() as session:
        repo = TopicAgentMemoryRepository(session)
        repo.upsert_config(scope_type="private_user", user_id=2112, ai_enabled=True)

    ai = FakeAIService(answer="ai-answer")
    sender = Sender()
    dispatcher = _mk_dispatcher(db_url, ai, sender)

    asyncio.run(
        dispatcher.handle_raw_update(
            _mk_update(uid=2112, chat_id=2112, chat_type="private", text="Test", update_id=11)
        )
    )

    assert sender.sent == [(2112, "ai-answer", None)]
    assert len(ai.prompts) == 1
    assert "User message:\nTest" in ai.prompts[0]

    with sf() as session:
        events = session.scalars(select(AuditEvent).where(AuditEvent.event_type == "ai_autoreply_sent")).all()
        assert len(events) == 1
        import json

        payload = json.loads(events[0].payload_json)
        assert payload["router_reason"] == "scope_enabled"


def test_topic_scope_enabled_plain_text_without_mention_stays_silent(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'ai_autoreply_topic_scope_enabled_no_mention.db'}"
    init_db(db_url)
    _seed_user(
        db_url,
        user_id=2113,
        role="vip",
        consent="accepted",
        group_chat_id=-1010,
        group_role="vip",
    )

    sf = create_session_factory(db_url)
    with sf() as session:
        repo = TopicAgentMemoryRepository(session)
        repo.upsert_config(scope_type="topic", chat_id=-1010, topic_id=77, ai_enabled=True)

    ai = FakeAIService(answer="ai-answer")
    sender = Sender()
    dispatcher = _mk_dispatcher(db_url, ai, sender)

    asyncio.run(
        dispatcher.handle_raw_update(
            _mk_update(uid=2113, chat_id=-1010, chat_type="supergroup", text="Test", update_id=12, message_thread_id=77)
        )
    )

    assert sender.sent == []
    assert ai.prompts == []


def test_inactive_scopes_stay_silent(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'ai_autoreply_inactive.db'}"
    init_db(db_url)
    _seed_user(db_url, user_id=2001, role="vip", consent="accepted")

    sf = create_session_factory(db_url)
    with sf() as session:
        repo = TopicAgentMemoryRepository(session)
        repo.upsert_config(scope_type="topic", chat_id=-1001, topic_id=10, ai_enabled=False)
        repo.upsert_config(scope_type="private_user", user_id=2001, ai_enabled=False)

    ai = FakeAIService(answer="ai-answer")
    sender = Sender()
    dispatcher = _mk_dispatcher(db_url, ai, sender)

    asyncio.run(
        dispatcher.handle_raw_update(
            _mk_update(uid=2001, chat_id=-1001, chat_type="supergroup", text="hi @AmoBot", update_id=1, message_thread_id=10)
        )
    )
    asyncio.run(
        dispatcher.handle_raw_update(
            _mk_update(uid=2001, chat_id=2001, chat_type="private", text="followup", update_id=2, reply_to_is_bot=True)
        )
    )

    assert sender.sent == []
    assert ai.prompts == []


def test_private_scope_min_ai_role_threshold_enforced(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'ai_autoreply_private_threshold.db'}"
    init_db(db_url)
    _seed_user(db_url, user_id=2201, role="normal", consent="accepted")
    _seed_user(db_url, user_id=2202, role="vip", consent="accepted")

    sf = create_session_factory(db_url)
    with sf() as session:
        repo = TopicAgentMemoryRepository(session)
        repo.upsert_config(scope_type="private_user", user_id=2201, ai_enabled=True)
        repo.upsert_config(scope_type="private_user", user_id=2202, ai_enabled=True)
        PrivateChatPolicyRepository(session).update_policy(
            min_ai_role="vip",
            min_general_command_role="normal",
            min_plugin_command_role="normal",
        )

    ai = FakeAIService(answer="ai-answer")
    sender = Sender()
    dispatcher = _mk_dispatcher(db_url, ai, sender)

    asyncio.run(dispatcher.handle_raw_update(_mk_update(uid=2201, chat_id=2201, chat_type="private", text="Test", update_id=21)))
    asyncio.run(dispatcher.handle_raw_update(_mk_update(uid=2202, chat_id=2202, chat_type="private", text="Test", update_id=22)))

    assert sender.sent == [(2202, "ai-answer", None)]
    assert len(ai.prompts) == 1


def test_private_scope_ignore_role_stays_silent(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'ai_autoreply_private_ignore.db'}"
    init_db(db_url)
    _seed_user(db_url, user_id=2203, role="ignore", consent="accepted")

    sf = create_session_factory(db_url)
    with sf() as session:
        TopicAgentMemoryRepository(session).upsert_config(scope_type="private_user", user_id=2203, ai_enabled=True)
        PrivateChatPolicyRepository(session).update_policy(
            min_ai_role="normal",
            min_general_command_role="normal",
            min_plugin_command_role="normal",
        )

    ai = FakeAIService(answer="ai-answer")
    sender = Sender()
    dispatcher = _mk_dispatcher(db_url, ai, sender)

    asyncio.run(dispatcher.handle_raw_update(_mk_update(uid=2203, chat_id=2203, chat_type="private", text="Test", update_id=23)))

    assert sender.sent == []
    assert ai.prompts == []


def test_group_scope_context_fallback_without_trigger_stays_silent(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'ai_autoreply_group_fallback_no_trigger.db'}"
    init_db(db_url)
    _seed_user(
        db_url,
        user_id=2210,
        role="vip",
        consent="accepted",
        group_chat_id=-1210,
        group_role="vip",
    )

    sf = create_session_factory(db_url)
    with sf() as session:
        repo = TopicAgentMemoryRepository(session)
        repo.upsert_config(scope_type="topic", chat_id=-1210, topic_id=9, ai_enabled=True)

    ai = FakeAIService(answer="ai-answer")
    sender = Sender()
    dispatcher = _mk_dispatcher(db_url, ai, sender)

    with patch(
        "amo_bot.ai.router.TopicAgentMemoryRepository.get_daily_memory",
        side_effect=RuntimeError("simulated context read error"),
    ), patch(
        "amo_bot.ai.router.TopicAgentMemoryRepository.list_long_memories",
        side_effect=RuntimeError("simulated context read error"),
    ):
        asyncio.run(
            dispatcher.handle_raw_update(
                _mk_update(uid=2210, chat_id=-1210, chat_type="supergroup", text="plain text", update_id=25, message_thread_id=9)
            )
        )

    assert sender.sent == []
    assert ai.prompts == []


def test_group_scope_context_fallback_reply_to_current_bot_still_sends(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'ai_autoreply_group_fallback_reply_to_bot.db'}"
    init_db(db_url)
    _seed_user(
        db_url,
        user_id=2211,
        role="vip",
        consent="accepted",
        group_chat_id=-1211,
        group_role="vip",
    )

    sf = create_session_factory(db_url)
    with sf() as session:
        repo = TopicAgentMemoryRepository(session)
        repo.upsert_config(scope_type="topic", chat_id=-1211, topic_id=10, ai_enabled=True)

    ai = FakeAIService(answer="ai-answer")
    sender = Sender()
    dispatcher = _mk_dispatcher(db_url, ai, sender)

    with patch(
        "amo_bot.ai.router.TopicAgentMemoryRepository.get_daily_memory",
        side_effect=RuntimeError("simulated context read error"),
    ), patch(
        "amo_bot.ai.router.TopicAgentMemoryRepository.list_long_memories",
        side_effect=RuntimeError("simulated context read error"),
    ):
        asyncio.run(
            dispatcher.handle_raw_update(
                _mk_update(
                    uid=2211,
                    chat_id=-1211,
                    chat_type="supergroup",
                    text="reply path",
                    update_id=26,
                    message_thread_id=10,
                    reply_to_is_bot=True,
                )
            )
        )

    assert sender.sent == [(-1211, "ai-answer", 10)]
    assert len(ai.prompts) == 1


def test_owner_group_topic_plain_text_without_trigger_does_not_send_ai(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'ai_autoreply_owner_group_plain_no_trigger.db'}"

    init_db(db_url)
    _seed_user(db_url, user_id=2300, role="owner", consent="accepted", group_chat_id=-1300, group_role="owner")

    with create_session_factory(db_url)() as session:
        repo = TopicAgentMemoryRepository(session)
        repo.upsert_config(scope_type="topic", chat_id=-1300, topic_id=70, ai_enabled=True)
        session.commit()

    ai = FakeAIService(answer="ai-answer")
    sender = Sender()
    dispatcher = _mk_dispatcher(db_url, ai, sender)

    asyncio.run(
        dispatcher.handle_raw_update(
            _mk_update(uid=2300, chat_id=-1300, chat_type="supergroup", text="plain text", update_id=31, message_thread_id=70)
        )
    )

    assert sender.sent == []
    assert ai.prompts == []


def test_owner_group_topic_root_context_reply_to_bot_does_not_send_ai(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'ai_autoreply_owner_group_topic_root_context.db'}"

    init_db(db_url)
    _seed_user(db_url, user_id=2399, role="owner", consent="accepted", group_chat_id=-1399, group_role="owner")

    with create_session_factory(db_url)() as session:
        repo = TopicAgentMemoryRepository(session)
        repo.upsert_config(scope_type="topic", chat_id=-1399, topic_id=104, ai_enabled=True)
        session.commit()

    ai = FakeAIService(answer="ai-answer")
    sender = Sender()
    dispatcher = _mk_dispatcher(db_url, ai, sender)

    asyncio.run(
        dispatcher.handle_raw_update(
            _mk_update(
                uid=2399,
                chat_id=-1399,
                chat_type="supergroup",
                text="plain text",
                update_id=320,
                message_thread_id=104,
                reply_to_is_bot=True,
                reply_to_message_id=104,
            )
        )
    )

    assert sender.sent == []
    assert ai.prompts == []


def test_owner_group_topic_mention_allows_ai(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'ai_autoreply_owner_group_mention.db'}"

    init_db(db_url)
    _seed_user(db_url, user_id=2301, role="owner", consent="accepted", group_chat_id=-1301, group_role="owner")

    with create_session_factory(db_url)() as session:
        repo = TopicAgentMemoryRepository(session)
        repo.upsert_config(scope_type="topic", chat_id=-1301, topic_id=71, ai_enabled=True)
        session.commit()

    ai = FakeAIService(answer="ai-answer")
    sender = Sender()
    dispatcher = _mk_dispatcher(db_url, ai, sender)

    asyncio.run(
        dispatcher.handle_raw_update(
            _mk_update(uid=2301, chat_id=-1301, chat_type="supergroup", text="hi @AmoBot", update_id=32, message_thread_id=71)
        )
    )

    assert sender.sent == [(-1301, "ai-answer", 71)]
    assert len(ai.prompts) == 1


def test_owner_group_topic_reply_to_current_bot_allows_ai(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'ai_autoreply_owner_group_reply_to_bot.db'}"

    init_db(db_url)
    _seed_user(db_url, user_id=2302, role="owner", consent="accepted", group_chat_id=-1302, group_role="owner")

    with create_session_factory(db_url)() as session:
        repo = TopicAgentMemoryRepository(session)
        repo.upsert_config(scope_type="topic", chat_id=-1302, topic_id=72, ai_enabled=True)
        session.commit()

    ai = FakeAIService(answer="ai-answer")
    sender = Sender()
    dispatcher = _mk_dispatcher(db_url, ai, sender)

    asyncio.run(
        dispatcher.handle_raw_update(
            _mk_update(
                uid=2302,
                chat_id=-1302,
                chat_type="supergroup",
                text="reply path",
                update_id=33,
                message_thread_id=72,
                reply_to_is_bot=True,
            )
        )
    )

    assert sender.sent == [(-1302, "ai-answer", 72)]
    assert len(ai.prompts) == 1


def test_group_scope_reply_to_other_bot_does_not_trigger_ai(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'ai_autoreply_group_reply_other_bot.db'}"
    init_db(db_url)
    _seed_user(
        db_url,
        user_id=2303,
        role="owner",
        consent="accepted",
        group_chat_id=-1303,
        group_role="owner",
    )

    sf = create_session_factory(db_url)
    with sf() as session:
        repo = TopicAgentMemoryRepository(session)
        repo.upsert_config(scope_type="topic", chat_id=-1303, topic_id=73, ai_enabled=True)

    ai = FakeAIService(answer="ai-answer")
    sender = Sender()
    dispatcher = _mk_dispatcher(db_url, ai, sender)

    asyncio.run(
        dispatcher.handle_raw_update(
            _mk_update(
                uid=2303,
                chat_id=-1303,
                chat_type="supergroup",
                text="reply path",
                update_id=34,
                message_thread_id=73,
                reply_to_is_bot=True,
                reply_to_bot_username="OtherBot",
            )
        )
    )

    assert sender.sent == []
    assert ai.prompts == []


def test_group_scope_unaffected_by_private_min_ai_role(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'ai_autoreply_group_unaffected.db'}"
    init_db(db_url)
    _seed_user(
        db_url,
        user_id=2204,
        role="normal",
        consent="accepted",
        group_chat_id=-1200,
        group_role="vip",
    )

    sf = create_session_factory(db_url)
    with sf() as session:
        repo = TopicAgentMemoryRepository(session)
        repo.upsert_config(scope_type="topic", chat_id=-1200, topic_id=5, ai_enabled=True)
        PrivateChatPolicyRepository(session).update_policy(
            min_ai_role="owner",
            min_general_command_role="normal",
            min_plugin_command_role="normal",
        )

    ai = FakeAIService(answer="ai-answer")
    sender = Sender()
    dispatcher = _mk_dispatcher(db_url, ai, sender)

    asyncio.run(
        dispatcher.handle_raw_update(
            _mk_update(uid=2204, chat_id=-1200, chat_type="supergroup", text="hi @AmoBot", update_id=24, message_thread_id=5)
        )
    )

    assert sender.sent == [(-1200, "ai-answer", 5)]
    assert len(ai.prompts) == 1
