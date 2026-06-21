from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

from amo_bot.ai.router import AIRouterContextV1, AIRouterDecision, AIRouterReasonCode

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
    def __init__(self, answer: str = "ai-ok", error: Exception | None = None) -> None:
        self.answer = answer
        self.error = error
        self.prompts: list[str] = []

    async def ask(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        return self.answer


class Sender:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str, int | None]] = []

    async def send_text(self, chat_id: int, text: str, message_thread_id: int | None = None) -> object:
        self.sent.append((chat_id, text, message_thread_id))
        return {"ok": True}


def _mk_update(*, uid: int, chat_id: int, chat_type: str, text: str, update_id: int, message_thread_id: int | None = None, reply_to_is_bot: bool = False, reply_to_message_id: int | None = None, reply_to_bot_username: str = "AmoBot", reply_to_text: str = "") -> dict[str, object]:
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
        if reply_to_text:
            m["reply_to_message"]["text"] = reply_to_text
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
    assert "Reply as the Telegram topic assistant" not in ai.prompts[0]
    assert "@AmoBot" not in ai.prompts[0]
    assert "Do not claim to be the underlying model/provider unless explicitly asked." in ai.prompts[0]
    assert "Antworte standardmäßig auf Deutsch" in ai.prompts[0]
    assert "Wenn der Nutzer klar eine andere Sprache nutzt" in ai.prompts[0]
    assert "system-provided" not in ai.prompts[0]
    assert "higher priority" not in ai.prompts[0]
    assert "User message:\nfollowup" in ai.prompts[1]


def test_dynamic_bot_username_is_removed_from_visible_prompt(tmp_path) -> None:
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
    assert "Reply as the Telegram topic assistant" not in ai.prompts[0]
    assert "@SomeDynamicBot" not in ai.prompts[0]
    assert "User message:\nTest" in ai.prompts[0]



def test_reply_context_bot_username_is_not_action_target_in_ai_prompt(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'ai_autoreply_reply_context_bot_identity.db'}"
    init_db(db_url)
    _seed_user(db_url, user_id=2113, role="vip", consent="accepted")

    sf = create_session_factory(db_url)
    with sf() as session:
        repo = TopicAgentMemoryRepository(session)
        repo.upsert_config(scope_type="private_user", user_id=2113, ai_enabled=True)

    ai = FakeAIService(answer="chart-analysis")
    sender = Sender()
    dispatcher = _mk_dispatcher(db_url, ai, sender)

    asyncio.run(
        dispatcher.handle_raw_update(
            _mk_update(
                uid=2113,
                chat_id=2113,
                chat_type="private",
                text="Analysiere bitte das Chart",
                update_id=12,
                reply_to_is_bot=True,
                reply_to_bot_username="TsubasaOzora_bot",
                reply_to_text="@TsubasaOzora_bot was zeigt dieses Chart?",
            )
        )
    )

    assert sender.sent == [(2113, "chart-analysis", None)]
    assert len(ai.prompts) == 1
    prompt = ai.prompts[0]
    assert "User message:\nAnalysiere bitte das Chart" in prompt
    assert "Reply as the Telegram topic assistant" not in prompt
    assert "@AmoBot" not in prompt
    assert "@TsubasaOzora_bot" not in prompt
    assert "contact" not in prompt.casefold()
    assert "tag" not in prompt.casefold()
    assert "external platform" not in prompt.casefold()


def test_bot_like_handles_are_removed_from_current_autoreply_prompt() -> None:
    sanitized, changed = Dispatcher._sanitize_prompt_for_autoreply(
        text="@TsubasaOzora_bot analysiere das Bild",
        bot_username="AmoBot",
    )

    assert changed is True
    assert sanitized == "analysiere das Bild"

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


def test_ai_mention_trigger_error_sends_fallback_and_audits_error(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'ai_autoreply_mention_error_fallback.db'}"
    init_db(db_url)
    _seed_user(
        db_url,
        user_id=2401,
        role="vip",
        consent="accepted",
        group_chat_id=-1401,
        group_role="vip",
    )

    sf = create_session_factory(db_url)
    with sf() as session:
        TopicAgentMemoryRepository(session).upsert_config(scope_type="topic", chat_id=-1401, topic_id=81, ai_enabled=True)

    ai = FakeAIService(error=RuntimeError("timeout"))
    sender = Sender()
    dispatcher = _mk_dispatcher(db_url, ai, sender)

    asyncio.run(
        dispatcher.handle_raw_update(
            _mk_update(uid=2401, chat_id=-1401, chat_type="supergroup", text="hi @AmoBot", update_id=41, message_thread_id=81)
        )
    )

    assert sender.sent == [(-1401, "Ich konnte gerade keine KI-Antwort erzeugen. Bitte versuch es gleich nochmal.", 81)]

    with sf() as session:
        events = session.scalars(select(AuditEvent).where(AuditEvent.event_type == "ai_autoreply_error")).all()
        assert len(events) == 1
        import json

        payload = json.loads(events[0].payload_json)
        assert payload["router_reason"] == "mention_in_active_scope"


def test_ai_reply_to_bot_trigger_error_sends_fallback(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'ai_autoreply_reply_error_fallback.db'}"
    init_db(db_url)
    _seed_user(db_url, user_id=2402, role="vip", consent="accepted")

    sf = create_session_factory(db_url)
    with sf() as session:
        TopicAgentMemoryRepository(session).upsert_config(scope_type="private_user", user_id=2402, ai_enabled=True)

    ai = FakeAIService(error=RuntimeError("503"))
    sender = Sender()
    dispatcher = _mk_dispatcher(db_url, ai, sender)

    asyncio.run(
        dispatcher.handle_raw_update(
            _mk_update(uid=2402, chat_id=2402, chat_type="private", text="followup", update_id=42, reply_to_is_bot=True)
        )
    )

    assert sender.sent == [(2402, "Ich konnte gerade keine KI-Antwort erzeugen. Bitte versuch es gleich nochmal.", None)]


def test_non_explicit_private_scope_error_does_not_send_fallback(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'ai_autoreply_scope_error_no_fallback.db'}"
    init_db(db_url)
    _seed_user(db_url, user_id=2403, role="vip", consent="accepted")

    sf = create_session_factory(db_url)
    with sf() as session:
        TopicAgentMemoryRepository(session).upsert_config(scope_type="private_user", user_id=2403, ai_enabled=True)

    ai = FakeAIService(error=RuntimeError("down"))
    sender = Sender()
    dispatcher = _mk_dispatcher(db_url, ai, sender)

    asyncio.run(
        dispatcher.handle_raw_update(
            _mk_update(uid=2403, chat_id=2403, chat_type="private", text="plain text", update_id=43)
        )
    )

    assert sender.sent == []


def test_ai_prompt_includes_router_context_sections_and_deduplicates_current_message(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'ai_autoreply_prompt_context_sections.db'}"
    init_db(db_url)
    _seed_user(
        db_url,
        user_id=2501,
        role="vip",
        consent="accepted",
        group_chat_id=-1501,
        group_role="vip",
    )

    sf = create_session_factory(db_url)
    with sf() as session:
        TopicAgentMemoryRepository(session).upsert_config(scope_type="topic", chat_id=-1501, topic_id=11, ai_enabled=True)

    ai = FakeAIService(answer="ai-answer")
    sender = Sender()
    dispatcher = _mk_dispatcher(db_url, ai, sender)

    router_context = AIRouterContextV1(
        scope_type="topic",
        scope_chat_id=-1501,
        scope_topic_id=11,
        user_id=2501,
        message_text="aktuelle frage",
        route_reason=AIRouterReasonCode.MENTION_IN_ACTIVE_SCOPE,
        flag_ai_scope_active=True,
        flag_bot_mention=True,
        flag_reply_to_bot=False,
        recent_messages_text="u1: vorherige relevante nachricht\nu1: @AmoBot aktuelle frage",
        current_time_context_text="Context:\nCurrent date: 2026-06-03\nTimezone: Europe/Berlin\nWhen answering about current events or live facts, prefer available web research over prior knowledge.",
        assembled_soul_text="Sei präzise.",
        daily_memory_text="Heute: wichtige Info.",
        long_memory_text="Langzeit: Präferenz X.",
    )
    forced_decision = AIRouterDecision(
        passthrough=True,
        eligible=True,
        reason_code=AIRouterReasonCode.MENTION_IN_ACTIVE_SCOPE,
        context=router_context,
    )

    with patch("amo_bot.telegram.dispatcher.AIRouter.decide", return_value=forced_decision):
        asyncio.run(
            dispatcher.handle_raw_update(
                _mk_update(uid=2501, chat_id=-1501, chat_type="supergroup", text="@AmoBot aktuelle frage", update_id=53, message_thread_id=11)
            )
        )

    assert sender.sent == [(-1501, "ai-answer", 11)]
    assert len(ai.prompts) == 1

    prompt = ai.prompts[0]
    assert "Current message:\naktuelle frage" in prompt
    assert "Source classes for answer synthesis:" in prompt
    assert "verified_external_evidence: checked current external evidence" in prompt
    assert "Current message source class: user_claim" in prompt
    assert "Current date: 2026-06-03" in prompt
    assert "Timezone: Europe/Berlin" in prompt
    assert "may be stale, irrelevant, or inaccurate" in prompt
    assert "Antworte standardmäßig auf Deutsch" in prompt
    assert "Relevant recent chat context:" in prompt
    assert "[source_class=user_claim;" in prompt
    assert "do not promote it to fact without evidence" in prompt
    assert "u1: vorherige relevante nachricht" in prompt
    assert "u1: aktuelle frage" not in prompt
    assert "Assistant behavior context:" in prompt
    assert "Sei präzise." in prompt
    assert "[source_class=model_prior;" in prompt
    assert "Daily memory context:" in prompt
    assert "Heute: wichtige Info." in prompt
    assert "[source_class=topic_summary;" in prompt
    assert "Long-term memory context:" in prompt
    assert "Langzeit: Präferenz X." in prompt
    assert "[source_class=semantic_memory;" in prompt
    assert "User message:\naktuelle frage" in prompt
    assert prompt.index("Current message:\naktuelle frage") < prompt.index("Relevant recent chat context")


def test_group_non_trigger_stays_silent_with_recent_context_present(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'ai_autoreply_group_non_trigger_guard.db'}"
    init_db(db_url)
    _seed_user(
        db_url,
        user_id=2502,
        role="vip",
        consent="accepted",
        group_chat_id=-1502,
        group_role="vip",
    )

    sf = create_session_factory(db_url)
    with sf() as session:
        repo = TopicAgentMemoryRepository(session)
        repo.upsert_config(scope_type="topic", chat_id=-1502, topic_id=21, ai_enabled=True)

    ai = FakeAIService(answer="ai-answer")
    sender = Sender()
    dispatcher = _mk_dispatcher(db_url, ai, sender)

    asyncio.run(
        dispatcher.handle_raw_update(
            _mk_update(uid=2502, chat_id=-1502, chat_type="supergroup", text="historie", update_id=61, message_thread_id=21)
        )
    )
    asyncio.run(
        dispatcher.handle_raw_update(
            _mk_update(uid=2502, chat_id=-1502, chat_type="supergroup", text="kein trigger", update_id=62, message_thread_id=21)
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


def test_reply_to_persisted_bot_message_includes_reply_context(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'ai_reply_context_bot.db'}"
    init_db(db_url)
    _seed_user(
        db_url,
        user_id=2601,
        role="vip",
        consent="accepted",
        group_chat_id=-1601,
        group_role="vip",
    )

    sf = create_session_factory(db_url)
    with sf() as session:
        TopicAgentMemoryRepository(session).upsert_config(scope_type="topic", chat_id=-1601, topic_id=31, ai_enabled=True)
        TopicAgentMemoryRepository(session).add_message(
            scope_type="topic",
            chat_id=-1601,
            topic_id=31,
            message_text="Bot sample answer for reply context",
            telegram_message_id=7001,
            telegram_author_user_id=0,
            telegram_author_username="AmoBot",
            telegram_author_is_bot=True,
            source="bot",
        )
        session.commit()

    ai = FakeAIService(answer="ai-answer")
    sender = Sender()
    dispatcher = _mk_dispatcher(db_url, ai, sender)

    asyncio.run(
        dispatcher.handle_raw_update(
            _mk_update(
                uid=2601,
                chat_id=-1601,
                chat_type="supergroup",
                text="Was meinst du damit?",
                update_id=71,
                message_thread_id=31,
                reply_to_is_bot=True,
                reply_to_message_id=7001,
            )
        )
    )

    assert sender.sent == [(-1601, "ai-answer", 31)]
    assert len(ai.prompts) == 1
    prompt = ai.prompts[0]
    assert "Telegram reply context:" in prompt
    assert "Replied-to source type: bot" in prompt
    assert "[source_class=bot_claim;" in prompt
    assert "Prior bot answers are conversation context only and are not evidence." in prompt
    assert "@AmoBot" not in prompt
    assert "Bot sample answer for reply context" in prompt
    assert "User message:\nWas meinst du damit?" in prompt


def test_autoreply_writes_context_snapshot_audit_for_mixed_context_incident_fixture(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'ai_context_snapshot_mixed_context.db'}"
    init_db(db_url)
    _seed_user(
        db_url,
        user_id=224601,
        role="vip",
        consent="accepted",
        group_chat_id=-1003997137641,
        group_role="vip",
    )

    sf = create_session_factory(db_url)
    with sf() as session:
        repo = TopicAgentMemoryRepository(session)
        repo.upsert_config(
            scope_type="topic",
            chat_id=-1003997137641,
            topic_id=2246,
            ai_enabled=True,
            recent_context_window_size=10,
        )
        repo.add_message(
            scope_type="topic",
            chat_id=-1003997137641,
            topic_id=2246,
            message_text="Die Taverne ist voller Orks und Magie.",
            telegram_author_user_id=224601,
            source="user",
        )
        repo.add_message(
            scope_type="topic",
            chat_id=-1003997137641,
            topic_id=2246,
            message_text="Unser Fantasy-Charakter sucht eine Quest im Koenigreich.",
            telegram_author_user_id=224601,
            source="user",
        )
        session.commit()

    ai = FakeAIService(answer="ai-answer")
    sender = Sender()
    dispatcher = _mk_dispatcher(db_url, ai, sender)

    asyncio.run(
        dispatcher.handle_raw_update(
            _mk_update(
                uid=224601,
                chat_id=-1003997137641,
                chat_type="supergroup",
                text="@AmoBot Was ist der aktuelle echte Kurs von BTC?",
                update_id=2246,
                message_thread_id=2246,
            )
        )
    )

    assert sender.sent == [(-1003997137641, "ai-answer", 2246)]
    assert len(ai.prompts) == 1
    assert "Structured runtime context snapshot" in ai.prompts[0]
    assert '"schema_version": "context_snapshot_v1"' in ai.prompts[0]
    assert '"frame": "current_turn"' in ai.prompts[0]
    assert '"frame": "recent_chat_context"' in ai.prompts[0]

    with sf() as session:
        snapshot_event = (
            session.query(AuditEvent)
            .filter(AuditEvent.event_type == "ai_context_snapshot")
            .one()
        )
        payload = json.loads(snapshot_event.payload_json)

    snapshot = payload["context_snapshot"]
    assert snapshot["schema_version"] == "context_snapshot_v1"
    assert snapshot["requires_current_info"] is True
    frames = {candidate["frame"] for candidate in snapshot["frame_candidates"]}
    assert {"current_turn", "recent_chat_context"} <= frames
    assert [conflict["conflict_type"] for conflict in snapshot["conflicts"]] == ["source_frame_boundary"]
    assert snapshot["conflicts"][0]["frames"] == ["current_turn", "background_context"]
    assert "source_frame_boundary_needs_resolution" in snapshot["uncertainty"]


def test_reply_to_user_message_uses_safe_inline_quote_context(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'ai_reply_context_user.db'}"
    init_db(db_url)
    _seed_user(
        db_url,
        user_id=2602,
        role="vip",
        consent="accepted",
        group_chat_id=-1602,
        group_role="vip",
    )

    sf = create_session_factory(db_url)
    with sf() as session:
        TopicAgentMemoryRepository(session).upsert_config(scope_type="topic", chat_id=-1602, topic_id=32, ai_enabled=True)

    ai = FakeAIService(answer="ai-answer")
    sender = Sender()
    dispatcher = _mk_dispatcher(db_url, ai, sender)
    raw = _mk_update(
        uid=2602,
        chat_id=-1602,
        chat_type="supergroup",
        text="@AmoBot erklär das bitte",
        update_id=72,
        message_thread_id=32,
    )
    message = raw["message"]
    assert isinstance(message, dict)
    message["reply_to_message"] = {
        "message_id": 7002,
        "from": {"id": 3602, "is_bot": False, "first_name": "Other", "username": "other"},
        "text": "User sample statement for reply context",
    }

    asyncio.run(dispatcher.handle_raw_update(raw))

    assert sender.sent == [(-1602, "ai-answer", 32)]
    assert len(ai.prompts) == 1
    prompt = ai.prompts[0]
    assert "Telegram reply context:" in prompt
    assert "Replied-to source type: user" in prompt
    assert "[source_class=user_claim;" in prompt
    assert "not verified evidence" in prompt
    assert "@other" not in prompt
    assert "User sample statement for reply context" in prompt


def test_unresolvable_reply_context_does_not_crash(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'ai_reply_context_missing.db'}"
    init_db(db_url)
    _seed_user(
        db_url,
        user_id=2603,
        role="vip",
        consent="accepted",
        group_chat_id=-1603,
        group_role="vip",
    )

    sf = create_session_factory(db_url)
    with sf() as session:
        TopicAgentMemoryRepository(session).upsert_config(scope_type="topic", chat_id=-1603, topic_id=33, ai_enabled=True)

    ai = FakeAIService(answer="ai-answer")
    sender = Sender()
    dispatcher = _mk_dispatcher(db_url, ai, sender)

    asyncio.run(
        dispatcher.handle_raw_update(
            _mk_update(
                uid=2603,
                chat_id=-1603,
                chat_type="supergroup",
                text="Weiter bitte",
                update_id=73,
                message_thread_id=33,
                reply_to_is_bot=True,
                reply_to_message_id=99999,
            )
        )
    )

    assert sender.sent == [(-1603, "ai-answer", 33)]
    assert len(ai.prompts) == 1
    assert "Telegram reply context:" not in ai.prompts[0]
    assert "User message:\nWeiter bitte" in ai.prompts[0]


def test_bot_authored_incoming_update_still_ignored_for_loop_protection(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'ai_reply_context_loop_protection.db'}"
    init_db(db_url)
    ai = FakeAIService(answer="ai-answer")
    sender = Sender()
    dispatcher = _mk_dispatcher(db_url, ai, sender)

    asyncio.run(
        dispatcher.handle_raw_update(
            {
                "update_id": 74,
                "message": {
                    "message_id": 174,
                    "from": {"id": 999, "is_bot": True, "first_name": "Amo", "username": "AmoBot"},
                    "chat": {"id": -1604, "type": "supergroup"},
                    "text": "@AmoBot loop?",
                },
            }
        )
    )

    assert sender.sent == []
    assert ai.prompts == []
