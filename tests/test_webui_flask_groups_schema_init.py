from __future__ import annotations

import sqlite3
from pathlib import Path

from amo_bot.config.settings import Settings
from amo_bot.db.base import Base, create_session_factory
from amo_bot.webui.flask_app import create_flask_app


def _make_settings(database_url: str) -> Settings:
    payload = {
        "BOT_TOKEN": "dummy",
        "DATABASE_URL": database_url,
        "AMO_PLUGIN_DIR": "plugins",
        "WEBUI_HOST": "127.0.0.1",
        "WEBUI_PORT": 5010,
        "WEBUI_PASSWORD": "secret",
        "WEBUI_SESSION_TTL_SECONDS": 900,
        "WEBUI_PUBLIC_MODE": False,
        "WEBUI_REQUIRE_HTTPS": False,
        "WEBUI_SESSION_COOKIE_SECURE": False,
        "WEBUI_LOGIN_DELAY_BASE_SECONDS": 0.25,
        "WEBUI_LOGIN_DELAY_MAX_SECONDS": 1.0,
    }
    return Settings(_env_file=None, **payload)


def _create_legacy_db_without_groups_tables(db_path: Path) -> None:
    # Simulate older schema by creating all current tables first,
    # then dropping the newly introduced group/topic tables.
    db_url = f"sqlite:///{db_path}"
    session_factory = create_session_factory(db_url)
    engine = session_factory.kw["bind"]
    Base.metadata.create_all(bind=engine)

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("DROP TABLE IF EXISTS telegram_topics")
        cur.execute("DROP TABLE IF EXISTS telegram_chats")
        conn.commit()
    finally:
        conn.close()


def test_create_flask_app_initializes_missing_group_tables(tmp_path) -> None:
    db_path = tmp_path / "legacy_groups_missing.db"
    _create_legacy_db_without_groups_tables(db_path)
    db_url = f"sqlite:///{db_path}"
    settings = _make_settings(db_url)

    app = create_flask_app(settings=settings)
    app.testing = True

    # Verify tables were created by create_flask_app -> init_db call.
    conn = sqlite3.connect(db_path)
    try:
        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    finally:
        conn.close()

    assert "telegram_chats" in names
    assert "telegram_topics" in names

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["authenticated"] = True

        response = client.get("/groups")

    assert response.status_code == 200
    assert "Keine Gruppen vorhanden." in response.get_data(as_text=True)
