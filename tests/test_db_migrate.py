from __future__ import annotations

from io import StringIO

import pytest
from sqlalchemy import create_engine, func, select, text

from amo_bot.db.base import Base
from amo_bot.db.init_db import init_db
from amo_bot.db.migrate import MigrationSafetyError, migrate_database
from amo_bot.db.models import AuditEvent, DbRole, TelegramChat, TopicRecentMessage, User


SECRET_TEXT = "do-not-leak-message-content"
SECRET_USERNAME = "secret-user-name"


def _sqlite_url(path) -> str:  # noqa: ANN001 - pytest tmp_path Path-like
    return f"sqlite:///{path}"


def _seed_source(database_url: str) -> None:
    init_db(database_url)
    engine = create_engine(database_url, future=True)
    with engine.begin() as connection:
        owner_role_id = connection.scalar(select(DbRole.id).where(DbRole.name == "owner"))
        assert owner_role_id is not None
        connection.execute(
            User.__table__.insert().values(
                id=42,
                telegram_user_id=123456,
                username=SECRET_USERNAME,
                role_id=owner_role_id,
            )
        )
        connection.execute(
            TelegramChat.__table__.insert().values(
                chat_id=-1001,
                chat_type="supergroup",
                title="private chat title",
            )
        )
        connection.execute(
            TopicRecentMessage.__table__.insert().values(
                id=77,
                scope_type="topic",
                chat_id=-1001,
                topic_id=5,
                user_id=123456,
                message_text=SECRET_TEXT,
            )
        )
        connection.execute(
            AuditEvent.__table__.insert().values(
                id=88,
                actor_telegram_user_id=123456,
                event_type="test",
                payload_json='{"secret":"hidden"}',
            )
        )
    engine.dispose()


def test_dry_run_reports_counts_without_copying_or_leaking_content(tmp_path) -> None:
    source_url = _sqlite_url(tmp_path / "source.db")
    target_url = _sqlite_url(tmp_path / "target.db")
    _seed_source(source_url)

    out = StringIO()
    result = migrate_database(source_url=source_url, target_url=target_url, dry_run=True, out=out)

    output = out.getvalue()
    assert result.dry_run is True
    assert "mode=DRY-RUN" in output
    assert "table=users" in output
    assert "table=topic_recent_messages" in output
    assert SECRET_TEXT not in output
    assert SECRET_USERNAME not in output

    engine = create_engine(target_url, future=True)
    with engine.connect() as connection:
        assert connection.scalar(select(User.id).where(User.id == 42)) is None
        assert connection.scalar(select(TopicRecentMessage.id).where(TopicRecentMessage.id == 77)) is None
    engine.dispose()


def test_actual_copy_preserves_counts_primary_keys_and_metadata_only_output(tmp_path) -> None:
    source_url = _sqlite_url(tmp_path / "source.db")
    target_url = _sqlite_url(tmp_path / "target.db")
    _seed_source(source_url)

    out = StringIO()
    result = migrate_database(source_url=source_url, target_url=target_url, out=out)

    output = out.getvalue()
    assert result.dry_run is False
    assert "table=users" in output
    assert "copied=" in output
    assert SECRET_TEXT not in output
    assert SECRET_USERNAME not in output

    source_engine = create_engine(source_url, future=True)
    target_engine = create_engine(target_url, future=True)
    with source_engine.connect() as source, target_engine.connect() as target:
        for table in Base.metadata.sorted_tables:
            source_count = source.scalar(select(func.count()).select_from(table))
            target_count = target.scalar(select(func.count()).select_from(table))
            assert target_count == source_count, table.name

        assert target.scalar(select(User.id).where(User.id == 42)) == 42
        assert target.scalar(select(TopicRecentMessage.id).where(TopicRecentMessage.id == 77)) == 77
        assert target.scalar(select(AuditEvent.id).where(AuditEvent.id == 88)) == 88
    source_engine.dispose()
    target_engine.dispose()


def test_copy_uses_target_server_default_for_legacy_null_in_not_null_column(tmp_path) -> None:
    source_url = _sqlite_url(tmp_path / "source.db")
    target_url = _sqlite_url(tmp_path / "target.db")
    _seed_source(source_url)

    source_engine = create_engine(source_url, future=True)
    with source_engine.begin() as connection:
        connection.execute(text("PRAGMA legacy_alter_table=ON"))
        connection.execute(text("ALTER TABLE users RENAME TO users_current"))
        connection.execute(
            text(
                """
                CREATE TABLE users (
                    id INTEGER NOT NULL PRIMARY KEY,
                    telegram_user_id BIGINT NOT NULL UNIQUE,
                    username VARCHAR(255),
                    first_name VARCHAR(255),
                    last_name VARCHAR(255),
                    display_name VARCHAR(255),
                    first_seen_at DATETIME,
                    last_seen_at DATETIME,
                    role_id INTEGER NOT NULL,
                    created_at DATETIME,
                    updated_at DATETIME,
                    consent_status VARCHAR(32) NOT NULL DEFAULT 'accepted',
                    consent_updated_at DATETIME,
                    consent_prompted_at DATETIME,
                    consent_prompt_count INTEGER NOT NULL DEFAULT 0
                )
                """
            )
        )
        connection.execute(text("INSERT INTO users SELECT * FROM users_current"))
        connection.execute(text("UPDATE users SET first_seen_at = NULL WHERE id = 42"))
        connection.execute(text("DROP TABLE users_current"))
    source_engine.dispose()

    out = StringIO()
    migrate_database(source_url=source_url, target_url=target_url, out=out)

    output = out.getvalue()
    assert "table=users" in output
    assert SECRET_TEXT not in output
    assert SECRET_USERNAME not in output

    target_engine = create_engine(target_url, future=True)
    with target_engine.connect() as connection:
        assert connection.scalar(select(User.id).where(User.id == 42)) == 42
        assert connection.scalar(select(User.first_seen_at).where(User.id == 42)) is not None
    target_engine.dispose()


def test_nonempty_target_refusal_happens_before_copy(tmp_path) -> None:
    source_url = _sqlite_url(tmp_path / "source.db")
    target_url = _sqlite_url(tmp_path / "target.db")
    _seed_source(source_url)
    init_db(target_url)

    out = StringIO()
    with pytest.raises(MigrationSafetyError, match="target database is not empty"):
        migrate_database(source_url=source_url, target_url=target_url, out=out)

    assert out.getvalue() == ""
    engine = create_engine(target_url, future=True)
    with engine.connect() as connection:
        assert connection.scalar(select(User.id).where(User.id == 42)) is None
    engine.dispose()


def test_same_source_target_refusal(tmp_path) -> None:
    source_url = _sqlite_url(tmp_path / "source.db")
    _seed_source(source_url)

    with pytest.raises(MigrationSafetyError, match="same database"):
        migrate_database(source_url=source_url, target_url=source_url, dry_run=True, out=StringIO())
