from __future__ import annotations

import os

from sqlalchemy import select

from amo_bot.config.settings import Settings
from amo_bot.db.base import create_session_factory
from amo_bot.db.init_db import init_db
from amo_bot.db.models import TelegramChat, TelegramTopic
from amo_bot.db.repositories import ChatTopicRepository
from amo_bot.webui.flask_app import create_flask_app


def _make_settings(database_url: str, password: str = "test-secret", owner_id: int | None = None) -> Settings:
    payload = {
        "BOT_TOKEN": "dummy-token",
        "TELEGRAM_API_BASE": "https://api.telegram.org",
        "POLL_TIMEOUT_SECONDS": 30,
        "POLL_LIMIT": 100,
        "POLL_RETRY_MAX_SECONDS": 30,
        "OFFSET_STATE_FILE": ".state/offset.json",
        "DATABASE_URL": database_url,
        "AMO_PLUGIN_DIR": "./plugins",
        "WEBUI_HOST": "127.0.0.1",
        "WEBUI_PORT": 8080,
        "WEBUI_PASSWORD": password,
    }
    if owner_id is not None:
        payload["WEBUI_OWNER_TELEGRAM_ID"] = owner_id
    else:
        os.environ.pop("WEBUI_OWNER_TELEGRAM_ID", None)
    return Settings(_env_file=None, **payload)


def _extract_csrf_token(html: str) -> str:
    marker = 'name="csrf_token" type="hidden" value="'
    start = html.find(marker)
    assert start != -1, "csrf token field missing"
    start += len(marker)
    end = html.find('"', start)
    assert end != -1
    return html[start:end]


def _login(client, password: str) -> None:
    page = client.get("/login")
    token = _extract_csrf_token(page.get_data(as_text=True))
    resp = client.post("/login", data={"password": password, "csrf_token": token}, follow_redirects=False)
    assert resp.status_code == 302


def _seed_chat_topic(db_url: str, chat_id: int, thread_id: int) -> None:
    sf = create_session_factory(db_url)
    with sf() as s:
        repo = ChatTopicRepository(s)
        repo.upsert_chat(
            chat_id=chat_id,
            chat_type="supergroup",
            title="Test Group",
            username="testgroup",
        )
        repo.upsert_topic(chat_id=chat_id, message_thread_id=thread_id, telegram_topic_name="General")


def test_groups_requires_login_redirects_to_login(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'groups1.db'}"
    init_db(db_url)
    settings = _make_settings(db_url)
    app = create_flask_app(settings=settings)

    with app.test_client() as client:
        response = client.get("/groups", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_groups_with_login_renders_page_and_empty_state(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'groups2.db'}"
    init_db(db_url)
    settings = _make_settings(db_url)
    app = create_flask_app(settings=settings)

    with app.test_client() as client:
        _login(client, "test-secret")
        response = client.get("/groups")
        html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Groups" in html
    assert "Keine Gruppen vorhanden." in html


def test_groups_lists_seeded_chat_and_topic(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'groups3.db'}"
    init_db(db_url)
    _seed_chat_topic(db_url, -100123, 77)

    settings = _make_settings(db_url)
    app = create_flask_app(settings=settings)

    with app.test_client() as client:
        _login(client, "test-secret")
        response = client.get("/groups")
        html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "-100123" in html
    assert "Test Group" in html
    assert "77" in html


def test_topic_metadata_update_with_owner_id_persists(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'groups4.db'}"
    init_db(db_url)
    _seed_chat_topic(db_url, -100200, 88)

    settings = _make_settings(db_url, owner_id=777)
    app = create_flask_app(settings=settings)

    with app.test_client() as client:
        _login(client, "test-secret")
        page = client.get("/groups")
        token = _extract_csrf_token(page.get_data(as_text=True))
        response = client.post(
            "/groups/-100200/topics/88",
            data={"display_name": "Ops", "notes": "Runbook", "enabled": "", "csrf_token": token},
            follow_redirects=False,
        )

    assert response.status_code == 302

    sf = create_session_factory(db_url)
    with sf() as s:
        topic = s.scalar(
            select(TelegramTopic).where(TelegramTopic.chat_id == -100200, TelegramTopic.message_thread_id == 88)
        )
        assert topic is not None
        assert topic.display_name == "Ops"
        assert topic.notes == "Runbook"
        assert topic.enabled is False


def test_topic_metadata_update_without_owner_id_blocked(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'groups5.db'}"
    init_db(db_url)
    _seed_chat_topic(db_url, -100201, 89)

    settings = _make_settings(db_url)
    app = create_flask_app(settings=settings)

    with app.test_client() as client:
        _login(client, "test-secret")
        page = client.get("/groups")
        token = _extract_csrf_token(page.get_data(as_text=True))
        response = client.post(
            "/groups/-100201/topics/89",
            data={"display_name": "X", "notes": "Y", "enabled": "y", "csrf_token": token},
            follow_redirects=False,
        )

    assert response.status_code == 403


def test_topic_metadata_update_requires_csrf(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'groups6.db'}"
    init_db(db_url)
    _seed_chat_topic(db_url, -100202, 90)

    settings = _make_settings(db_url, owner_id=777)
    app = create_flask_app(settings=settings)

    with app.test_client() as client:
        _login(client, "test-secret")
        response = client.post(
            "/groups/-100202/topics/90",
            data={"display_name": "X", "notes": "Y", "enabled": "y"},
            follow_redirects=False,
        )

    assert response.status_code == 400


def test_topic_metadata_update_missing_topic_returns_404(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'groups7.db'}"
    init_db(db_url)

    sf = create_session_factory(db_url)
    with sf() as s:
        s.add(
            TelegramChat(
                chat_id=-100203,
                chat_type="supergroup",
                title="Only Chat",
                username="onlychat",
            )
        )
        s.commit()

    settings = _make_settings(db_url, owner_id=777)
    app = create_flask_app(settings=settings)

    with app.test_client() as client:
        _login(client, "test-secret")
        page = client.get("/groups")
        token = _extract_csrf_token(page.get_data(as_text=True))
        response = client.post(
            "/groups/-100203/topics/999",
            data={"display_name": "X", "notes": "Y", "enabled": "y", "csrf_token": token},
            follow_redirects=False,
        )

    assert response.status_code == 404
