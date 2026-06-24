from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from amo_bot.db.base import Base
from amo_bot.db.init_db import init_db
from amo_bot.auth.roles import Role
from amo_bot.db.models import AuditEvent, ChatUserRole, TelegramChat, TelegramTopic, User
from amo_bot.db.repositories import ChatScopedRoleRepository, ChatTopicRepository, UserRoleRepository


def _session_factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        from amo_bot.db.models import DEFAULT_ROLES, DbRole

        for role, prio in DEFAULT_ROLES:
            session.add(DbRole(name=role.value, priority=prio))
        session.commit()
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


def test_upsert_topic_recovers_once_when_postgres_topic_sequence_lags(monkeypatch) -> None:
    factory = _session_factory()
    with factory() as session:
        repo = ChatTopicRepository(session)
        repo.upsert_chat(chat_id=1, chat_type="supergroup")

        original_commit = session.commit
        commit_calls = 0
        sync_calls: list[tuple[str, str]] = []

        def _commit_with_first_topic_integrity_error() -> None:
            nonlocal commit_calls
            commit_calls += 1
            if commit_calls == 1:
                raise IntegrityError(
                    "INSERT INTO telegram_topics ...",
                    {},
                    Exception('duplicate key value violates unique constraint "telegram_topics_pkey"'),
                )
            original_commit()

        monkeypatch.setattr(session, "commit", _commit_with_first_topic_integrity_error)
        monkeypatch.setattr(repo, "_can_recover_topic_sequence_integrity_error", lambda _exc: True)
        monkeypatch.setattr(
            repo,
            "_sync_postgresql_topic_id_sequence",
            lambda: sync_calls.append(("telegram_topics", "id")),
        )

        topic = repo.upsert_topic(chat_id=1, message_thread_id=104305, telegram_topic_name=None)

        assert topic.chat_id == 1
        assert topic.message_thread_id == 104305
        assert sync_calls == [("telegram_topics", "id")]
        assert commit_calls == 2
        assert session.query(TelegramTopic).count() == 1


def test_upsert_discovered_user_recovers_once_when_postgres_user_sequence_lags(monkeypatch) -> None:
    factory = _session_factory()
    with factory() as session:
        repo = UserRoleRepository(session)

        original_commit = session.commit
        commit_calls = 0
        sync_calls: list[tuple[str, str]] = []

        def _commit_with_first_user_integrity_error() -> None:
            nonlocal commit_calls
            commit_calls += 1
            if commit_calls == 1:
                raise IntegrityError(
                    "INSERT INTO users ...",
                    {},
                    Exception('duplicate key value violates unique constraint "users_pkey"'),
                )
            original_commit()

        monkeypatch.setattr(session, "commit", _commit_with_first_user_integrity_error)
        monkeypatch.setattr(repo, "_can_recover_user_sequence_integrity_error", lambda _exc: True)
        monkeypatch.setattr(
            repo,
            "_sync_postgresql_user_id_sequence",
            lambda: sync_calls.append(("users", "id")),
        )

        user = repo.upsert_discovered_user(
            telegram_user_id=7402724955,
            username=None,
            first_name="T",
            last_name=None,
        )

        assert user.telegram_user_id == 7402724955
        assert user.first_name == "T"
        assert user.role.name == Role.NORMAL.value
        assert sync_calls == [("users", "id")]
        assert commit_calls == 2
        assert session.query(User).count() == 1


def test_upsert_discovered_user_does_not_recover_other_integrity_errors(monkeypatch) -> None:
    factory = _session_factory()
    with factory() as session:
        repo = UserRoleRepository(session)
        sync_calls: list[tuple[str, str]] = []

        def _commit_with_non_sequence_integrity_error() -> None:
            raise IntegrityError(
                "INSERT INTO users ...",
                {},
                Exception('duplicate key value violates unique constraint "users_telegram_user_id_key"'),
            )

        monkeypatch.setattr(session, "commit", _commit_with_non_sequence_integrity_error)
        monkeypatch.setattr(repo, "_can_recover_user_sequence_integrity_error", lambda _exc: False)
        monkeypatch.setattr(
            repo,
            "_sync_postgresql_user_id_sequence",
            lambda: sync_calls.append(("users", "id")),
        )

        with pytest.raises(IntegrityError):
            repo.upsert_discovered_user(
                telegram_user_id=7402724955,
                username=None,
                first_name="T",
                last_name=None,
            )

        assert sync_calls == []


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


def test_chat_scoped_role_update_updates_timestamp_and_bulk_lookup() -> None:
    factory = _session_factory()
    with factory() as session:
        topic_repo = ChatTopicRepository(session)
        topic_repo.upsert_chat(chat_id=1, chat_type="supergroup")
        topic_repo.upsert_chat(chat_id=2, chat_type="supergroup")

        user_repo = UserRoleRepository(session)
        user_repo.set_user_role(actor_telegram_user_id=1, target_telegram_user_id=101, role=Role.NORMAL)
        user_repo.set_user_role(actor_telegram_user_id=1, target_telegram_user_id=202, role=Role.NORMAL)

        scoped_repo = ChatScopedRoleRepository(session)
        scoped_repo.set_group_role(chat_id=1, telegram_user_id=101, role=Role.VIP)

        changed_at = datetime(2026, 2, 1, tzinfo=timezone.utc)
        result = scoped_repo.set_group_role(chat_id=1, telegram_user_id=101, role=Role.ADMIN, changed_at=changed_at)
        assert result.changed is True

        mapped = scoped_repo.list_group_roles_for_users(chat_ids=[1, 2], telegram_user_ids=[101, 202])
        assert mapped[(1, 101)] == Role.ADMIN
        assert (2, 101) not in mapped
        assert (1, 202) not in mapped

        role_row = session.scalar(select(ChatUserRole).where(ChatUserRole.chat_id == 1, ChatUserRole.user_id.is_not(None)))
        assert role_row is not None
        # SQLite returns TZ-naive datetimes even for timezone=True columns.
        assert role_row.updated_at == changed_at.replace(tzinfo=None)


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
