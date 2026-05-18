from __future__ import annotations

import sqlite3

from sqlalchemy import create_engine, inspect, text

from amo_bot.db.init_db import init_db


def test_init_db_adds_consent_columns_and_backfills(tmp_path) -> None:
    db_path = tmp_path / "legacy.sqlite"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            telegram_user_id BIGINT UNIQUE NOT NULL,
            username VARCHAR(255),
            role_id INTEGER NOT NULL,
            created_at DATETIME,
            updated_at DATETIME
        )
        """
    )
    conn.execute(
        "INSERT INTO users (telegram_user_id, username, role_id, created_at, updated_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
        (111, "legacy", 1),
    )
    conn.commit()
    conn.close()

    init_db(f"sqlite:///{db_path}")

    engine = create_engine(f"sqlite:///{db_path}")
    columns = {column["name"] for column in inspect(engine).get_columns("users")}
    assert "consent_status" in columns
    assert "consent_updated_at" in columns
    assert "consent_prompted_at" in columns
    assert "consent_prompt_count" in columns

    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT consent_status, consent_prompt_count FROM users WHERE telegram_user_id = :telegram_user_id"
            ),
            {"telegram_user_id": 111},
        ).first()
    assert row is not None
    assert row[0] == "accepted"
    assert row[1] == 0


def test_init_db_creates_topic_agent_configs_with_recent_context_window_size(tmp_path) -> None:
    db_path = tmp_path / "fresh.sqlite"

    init_db(f"sqlite:///{db_path}")

    engine = create_engine(f"sqlite:///{db_path}")
    columns = {column["name"] for column in inspect(engine).get_columns("topic_agent_configs")}
    assert "recent_context_window_size" in columns


def test_init_db_migrates_topic_agent_configs_adds_recent_context_window_size(tmp_path) -> None:
    db_path = tmp_path / "legacy_topic_cfg.sqlite"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE topic_agent_configs (
            id INTEGER NOT NULL PRIMARY KEY,
            scope_type VARCHAR(32) NOT NULL,
            chat_id BIGINT,
            topic_id BIGINT,
            user_id BIGINT,
            ai_enabled BOOLEAN NOT NULL DEFAULT 0,
            response_mode VARCHAR(32) NOT NULL DEFAULT 'command',
            memory_retention_days INTEGER NOT NULL DEFAULT 30,
            tools_enabled BOOLEAN NOT NULL DEFAULT 0,
            main_soul_text TEXT,
            topic_soul_text TEXT,
            topic_soul_owner_only_edit BOOLEAN NOT NULL DEFAULT 1,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_topic_agent_configs_scope UNIQUE (scope_type, chat_id, topic_id, user_id)
        )
        """
    )
    conn.execute(
        "INSERT INTO topic_agent_configs (scope_type, user_id) VALUES (?, ?)",
        ("private_user", 777),
    )
    conn.commit()
    conn.close()

    init_db(f"sqlite:///{db_path}")

    engine = create_engine(f"sqlite:///{db_path}")
    columns = {column["name"] for column in inspect(engine).get_columns("topic_agent_configs")}
    assert "recent_context_window_size" in columns

    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT recent_context_window_size FROM topic_agent_configs WHERE scope_type = :scope_type AND user_id = :user_id"
            ),
            {"scope_type": "private_user", "user_id": 777},
        ).first()
    assert row is not None
    assert row[0] == 0
