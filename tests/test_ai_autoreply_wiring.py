from __future__ import annotations

import asyncio

from sqlalchemy import select

from amo_bot.db.base import create_session_factory
from amo_bot.db.init_db import init_db
from amo_bot.auth.roles import Role
from amo_bot.db.models import AuditEvent, DbRole, User
from amo_bot.db.repositories import ChatScopedRoleRepository, TopicAgentMemoryRepository
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


def _mk_update(*, uid: int, chat_id: int, chat_type: str, text: str, update_id: int, message_thread_id: int | None = None, reply_to_is_bot: bool = False) -> dict[str, object]:
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
        m["reply_to_message"] = {"from": {"id": 999, "is_bot": True, "first_name": "Bot"}}
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
    assert ai.prompts == ["hi @AmoBot", "followup"]


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


def test_autoreply_role_and_consent_denials_are_silent_and_audited(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'ai_autoreply_denied.db'}"
    init_db(db_url)
    _seed_user(db_url, user_id=2002, role="normal", consent="accepted")
    _seed_user(db_url, user_id=2003, role="vip", consent="declined")

    sf = create_session_factory(db_url)
    with sf() as session:
        repo = TopicAgentMemoryRepository(session)
        repo.upsert_config(scope_type="private_user", user_id=2002, ai_enabled=True)
        repo.upsert_config(scope_type="private_user", user_id=2003, ai_enabled=True)

    ai = FakeAIService(answer="ai-answer")
    sender = Sender()
    dispatcher = _mk_dispatcher(db_url, ai, sender)

    asyncio.run(dispatcher.handle_raw_update(_mk_update(uid=2002, chat_id=2002, chat_type="private", text="hi @AmoBot", update_id=1)))
    asyncio.run(dispatcher.handle_raw_update(_mk_update(uid=2003, chat_id=2003, chat_type="private", text="hi @AmoBot", update_id=2)))

    assert sender.sent == []
    assert ai.prompts == []

    with sf() as session:
        events = session.scalars(select(AuditEvent).where(AuditEvent.event_type == "ai_autoreply_denied")).all()
        import json

        reasons = sorted(json.loads(event.payload_json).get("reason") for event in events)
        assert reasons == ["consent_denied", "role_denied"]
