from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from amo_bot.db.base import Base
from amo_bot.db.models import AuditEvent, TelegramChat, TelegramTopic
from amo_bot.db.repositories import ChatTopicRepository


def _session_factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


def test_upsert_chat_create_and_update() -> None:
    factory = _session_factory()
    with factory() as session:
        repo = ChatTopicRepository(session)

        first_seen = datetime(2026, 1, 1, tzinfo=timezone.utc)
        chat = repo.upsert_chat(chat_id=123, chat_type="supergroup", title="A", username="u1", seen_at=first_seen)
        assert chat.chat_id == 123
        assert chat.chat_type == "supergroup"
        assert chat.title == "A"
        assert chat.username == "u1"
        assert chat.first_seen_at == first_seen
        assert chat.last_seen_at == first_seen

        second_seen = datetime(2026, 1, 2, tzinfo=timezone.utc)
        updated = repo.upsert_chat(chat_id=123, chat_type="group", title="B", username="u2", seen_at=second_seen)
        assert updated.chat_id == 123
        assert updated.chat_type == "group"
        assert updated.title == "B"
        assert updated.username == "u2"
        assert updated.first_seen_at == first_seen
        assert updated.last_seen_at == second_seen

        count = session.query(TelegramChat).count()
        assert count == 1


def test_upsert_topic_create_update_no_duplicate() -> None:
    factory = _session_factory()
    with factory() as session:
        repo = ChatTopicRepository(session)
        repo.upsert_chat(chat_id=1, chat_type="supergroup")

        first_seen = datetime(2026, 1, 1, tzinfo=timezone.utc)
        topic = repo.upsert_topic(chat_id=1, message_thread_id=10, telegram_topic_name="Topic A", seen_at=first_seen)
        assert topic.chat_id == 1
        assert topic.message_thread_id == 10
        assert topic.telegram_topic_name == "Topic A"
        assert topic.first_seen_at == first_seen
        assert topic.last_seen_at == first_seen

        second_seen = datetime(2026, 1, 2, tzinfo=timezone.utc)
        unchanged_name = repo.upsert_topic(chat_id=1, message_thread_id=10, telegram_topic_name=None, seen_at=second_seen)
        assert unchanged_name.telegram_topic_name == "Topic A"
        assert unchanged_name.last_seen_at == second_seen

        third_seen = datetime(2026, 1, 3, tzinfo=timezone.utc)
        updated = repo.upsert_topic(chat_id=1, message_thread_id=10, telegram_topic_name="Topic B", seen_at=third_seen)
        assert updated.chat_id == 1
        assert updated.message_thread_id == 10
        assert updated.telegram_topic_name == "Topic B"
        assert updated.first_seen_at == first_seen
        assert updated.last_seen_at == third_seen

        count = session.query(TelegramTopic).count()
        assert count == 1


def test_list_chats_and_topics() -> None:
    factory = _session_factory()
    with factory() as session:
        repo = ChatTopicRepository(session)
        repo.upsert_chat(chat_id=2, chat_type="group", title="B")
        repo.upsert_chat(chat_id=1, chat_type="supergroup", title="A")
        repo.upsert_topic(chat_id=1, message_thread_id=200, telegram_topic_name="T2")
        repo.upsert_topic(chat_id=1, message_thread_id=100, telegram_topic_name="T1")
        repo.upsert_topic(chat_id=2, message_thread_id=999, telegram_topic_name="T3")

        chats = repo.list_chats()
        assert [c.chat_id for c in chats] == [1, 2]

        topics = repo.list_topics(chat_id=1)
        assert [t.message_thread_id for t in topics] == [100, 200]


def test_update_topic_metadata() -> None:
    factory = _session_factory()
    with factory() as session:
        repo = ChatTopicRepository(session)
        repo.upsert_chat(chat_id=1, chat_type="supergroup")
        repo.upsert_topic(chat_id=1, message_thread_id=10, telegram_topic_name="Raw")

        updated = repo.update_topic_metadata(
            chat_id=1,
            message_thread_id=10,
            display_name="Friendly",
            notes="Pinned for onboarding",
            enabled=False,
            actor_telegram_user_id=42,
        )

        assert updated.display_name == "Friendly"
        assert updated.notes == "Pinned for onboarding"
        assert updated.enabled is False

        events = session.query(AuditEvent).all()
        assert len(events) == 1
        assert events[0].event_type == "topic_metadata_update"
        assert events[0].actor_telegram_user_id == 42
