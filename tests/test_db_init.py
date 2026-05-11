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
