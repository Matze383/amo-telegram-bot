from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from amo_bot.db.base import Base
from amo_bot.db.models import DbRole, Role, TelegramChat
from amo_bot.db.repositories import ChatSeenUserRepository


def _setup_session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add(DbRole(name=Role.NORMAL.value, priority=30))
    session.add(TelegramChat(chat_id=-1001, chat_type="supergroup"))
    session.commit()
    return session


def test_mark_seen_insert_and_update_preserves_first_seen() -> None:
    session = _setup_session()
    repo = ChatSeenUserRepository(session)

    first_seen = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    second_seen = first_seen + timedelta(minutes=5)

    created = repo.mark_seen(chat_id=-1001, telegram_user_id=42, seen_at=first_seen)
    assert created.first_seen_at == first_seen
    assert created.last_seen_at == first_seen

    updated = repo.mark_seen(chat_id=-1001, telegram_user_id=42, seen_at=second_seen)
    assert updated.first_seen_at == first_seen
    assert updated.last_seen_at == second_seen


def test_mark_seen_unique_and_list_seen_users_for_chat() -> None:
    session = _setup_session()
    repo = ChatSeenUserRepository(session)

    repo.mark_seen(chat_id=-1001, telegram_user_id=11)
    repo.mark_seen(chat_id=-1001, telegram_user_id=22)
    repo.mark_seen(chat_id=-1001, telegram_user_id=11)

    assert repo.list_seen_users_for_chat(chat_id=-1001) == [11, 22]
