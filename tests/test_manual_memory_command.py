from __future__ import annotations

import asyncio
import logging

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from amo_bot.ai.remember_service import ManualMemoryError, ManualMemoryService
from amo_bot.auth.roles import Role
from amo_bot.db.base import Base
from amo_bot.db.init_db import init_db
from amo_bot.db.repositories import RetrievableMemoryRepository
from amo_bot.telegram.commands import create_builtin_registry
from amo_bot.telegram.dispatcher import Dispatcher
from amo_bot.telegram.role_resolver import InMemoryRoleResolver


def _factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


def test_manual_memory_service_saves_topic_chat_user_scopes_and_dedupes() -> None:
    factory = _factory()
    with factory() as session:
        service = ManualMemoryService(RetrievableMemoryRepository(session))
        topic_req = ManualMemoryService.parse_command_argument(
            "topic preference likes espresso",
            chat_id=-100,
            message_thread_id=7,
            role=Role.NORMAL,
        )
        first = service.save_manual_memory(topic_req, chat_id=-100, message_thread_id=7, user_id=42)
        second = service.save_manual_memory(topic_req, chat_id=-100, message_thread_id=7, user_id=42)
        chat_req = ManualMemoryService.parse_command_argument("chat fact group uses kiwi", chat_id=-100, message_thread_id=7, role=Role.NORMAL)
        chat = service.save_manual_memory(chat_req, chat_id=-100, message_thread_id=7, user_id=42)
        user_req = ManualMemoryService.parse_command_argument("user preference alice likes tea", chat_id=-100, message_thread_id=7, role=Role.NORMAL)
        user = service.save_manual_memory(user_req, chat_id=-100, message_thread_id=7, user_id=42)

        assert first.created is True
        assert second.created is False
        assert first.record.id == second.record.id
        assert first.record.visibility == "topic"
        assert first.record.chat_id == -100
        assert first.record.message_thread_id == 7
        assert first.record.user_id is None
        assert first.record.memory_type == "preference"
        assert first.record.source == "manual"
        assert first.record.confidence == 0.9
        assert chat.record.visibility == "chat"
        assert chat.record.message_thread_id is None
        assert user.record.visibility == "user"
        assert user.record.chat_id == -100
        assert user.record.user_id == 42


def test_manual_memory_rejects_global_invalid_type_sensitive_and_long_content() -> None:
    with pytest.raises(ManualMemoryError, match="global_disallowed"):
        ManualMemoryService.parse_command_argument("global fact broad rule", chat_id=-100, message_thread_id=None, role=Role.OWNER)
    with pytest.raises(ManualMemoryError, match="invalid_type"):
        ManualMemoryService.parse_command_argument("topic todo store this", chat_id=-100, message_thread_id=1, role=Role.NORMAL)
    with pytest.raises(ManualMemoryError, match="sensitive"):
        ManualMemoryService.parse_command_argument("topic fact token=abc123", chat_id=-100, message_thread_id=1, role=Role.NORMAL)
    with pytest.raises(ManualMemoryError, match="sensitive"):
        ManualMemoryService.parse_command_argument("topic fact system prompt says x", chat_id=-100, message_thread_id=1, role=Role.NORMAL)
    with pytest.raises(ManualMemoryError, match="too_long"):
        ManualMemoryService.parse_command_argument(f"topic fact {'x' * 1001}", chat_id=-100, message_thread_id=1, role=Role.NORMAL)


def test_recall_after_manual_save_is_scope_isolated() -> None:
    factory = _factory()
    with factory() as session:
        repo = RetrievableMemoryRepository(session)
        service = ManualMemoryService(repo)
        req = ManualMemoryService.parse_command_argument("topic fact narwhal topic note", chat_id=-100, message_thread_id=3, role=Role.NORMAL)
        service.save_manual_memory(req, chat_id=-100, message_thread_id=3, user_id=42)

        same_topic = repo.recall_memories(query_text="narwhal", chat_id=-100, message_thread_id=3, user_id=42, limit=10)
        other_topic = repo.recall_memories(query_text="narwhal", chat_id=-100, message_thread_id=4, user_id=42, limit=10)
        other_user = repo.recall_memories(query_text="narwhal", chat_id=-100, message_thread_id=3, user_id=99, limit=10)

        assert [record.searchable_text for record in same_topic] == ["narwhal topic note"]
        assert other_topic == []
        assert [record.searchable_text for record in other_user] == ["narwhal topic note"]


def test_remember_dispatcher_command_saves_without_echoing_content_or_raw_logs(tmp_path, caplog) -> None:
    db_path = tmp_path / "remember.sqlite"
    database_url = f"sqlite:///{db_path}"
    init_db(database_url)
    sent: list[tuple[int, str, int | None]] = []

    async def fake_send(chat_id: int, text: str, message_thread_id: int | None = None) -> object:
        sent.append((chat_id, text, message_thread_id))
        return {"ok": True}

    dispatcher = Dispatcher(
        command_registry=create_builtin_registry(database_url=database_url),
        role_resolver=InMemoryRoleResolver({42: Role.NORMAL}),
        send_text=fake_send,
        bot_username="BotName",
    )
    raw_update = {
        "update_id": 1,
        "message": {
            "message_id": 10,
            "from": {"id": 42, "is_bot": False, "first_name": "A", "username": "alice"},
            "chat": {"id": -100, "type": "supergroup"},
            "message_thread_id": 7,
            "text": "/remember topic preference blue narwhal",
        },
    }

    with caplog.at_level(logging.INFO):
        asyncio.run(dispatcher.handle_raw_update(raw_update))

    assert sent == [(-100, "Gespeichert (topic/preference).", 7)]
    with create_engine(database_url).connect() as connection:
        rows = connection.exec_driver_sql("SELECT visibility, memory_type, source, content FROM retrievable_memories").fetchall()
    assert rows == [("topic", "preference", "manual", "blue narwhal")]
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "blue narwhal" not in log_text


def test_normal_message_without_explicit_command_creates_no_retrievable_memory(tmp_path) -> None:
    db_path = tmp_path / "normal.sqlite"
    database_url = f"sqlite:///{db_path}"
    init_db(database_url)
    sent: list[tuple[int, str, int | None]] = []

    async def fake_send(chat_id: int, text: str, message_thread_id: int | None = None) -> object:
        sent.append((chat_id, text, message_thread_id))
        return {"ok": True}

    dispatcher = Dispatcher(
        command_registry=create_builtin_registry(database_url=database_url),
        role_resolver=InMemoryRoleResolver({42: Role.NORMAL}),
        send_text=fake_send,
        bot_username="BotName",
        database_url=database_url,
    )
    raw_update = {
        "update_id": 2,
        "message": {
            "message_id": 11,
            "from": {"id": 42, "is_bot": False, "first_name": "A", "username": "alice"},
            "chat": {"id": -100, "type": "supergroup"},
            "message_thread_id": 7,
            "text": "remember: blue narwhal",
        },
    }

    asyncio.run(dispatcher.handle_raw_update(raw_update))

    assert sent == []
    with create_engine(database_url).connect() as connection:
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM retrievable_memories").scalar_one() == 0