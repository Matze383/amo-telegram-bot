from __future__ import annotations

import os

from sqlalchemy import select

from amo_bot.config.settings import Settings
from amo_bot.db.base import create_session_factory
from amo_bot.db.init_db import init_db
from amo_bot.db.models import User
from amo_bot.db.repositories import UserRoleRepository
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


def _seed_user(db_url: str, telegram_user_id: int, role: str) -> None:
    sf = create_session_factory(db_url)
    with sf() as s:
        repo = UserRoleRepository(s)
        from amo_bot.auth.roles import Role

        repo.set_user_role(actor_telegram_user_id=1, target_telegram_user_id=telegram_user_id, role=Role(role))


def test_users_requires_login_redirects_to_login(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'users1.db'}"
    init_db(db_url)
    settings = _make_settings(db_url)
    app = create_flask_app(settings=settings)

    with app.test_client() as client:
        response = client.get("/users", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_users_with_login_renders_page_and_empty_state(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'users2.db'}"
    init_db(db_url)
    settings = _make_settings(db_url)
    app = create_flask_app(settings=settings)

    with app.test_client() as client:
        _login(client, "test-secret")
        response = client.get("/users")
        html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Users" in html
    assert "Keine User vorhanden." in html


def test_users_lists_seeded_users(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'users3.db'}"
    init_db(db_url)
    _seed_user(db_url, 12345, "vip")

    settings = _make_settings(db_url)
    app = create_flask_app(settings=settings)

    with app.test_client() as client:
        _login(client, "test-secret")
        response = client.get("/users")
        html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "12345" in html
    assert "vip" in html


def test_users_lists_auto_discovered_profile_fields(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'users_discovered.db'}"
    init_db(db_url)

    sf = create_session_factory(db_url)
    with sf() as s:
        from amo_bot.db.repositories import UserRoleRepository

        repo = UserRoleRepository(s)
        repo.upsert_discovered_user(
            telegram_user_id=98765,
            username="autouser",
            first_name="Auto",
            last_name="Discovered",
        )

    settings = _make_settings(db_url)
    app = create_flask_app(settings=settings)

    with app.test_client() as client:
        _login(client, "test-secret")
        response = client.get("/users")
        html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "98765" in html
    assert "autouser" in html
    assert "Auto Discovered" in html


def test_role_change_with_owner_id_persists(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'users4.db'}"
    init_db(db_url)
    _seed_user(db_url, 555, "normal")

    settings = _make_settings(db_url, owner_id=777)
    app = create_flask_app(settings=settings)

    with app.test_client() as client:
        _login(client, "test-secret")
        page = client.get("/users")
        token = _extract_csrf_token(page.get_data(as_text=True))
        response = client.post(
            "/users/555/role",
            data={"role": "admin", "csrf_token": token},
            follow_redirects=False,
        )

    assert response.status_code == 302

    sf = create_session_factory(db_url)
    with sf() as s:
        user = s.scalar(select(User).where(User.telegram_user_id == 555))
        assert user is not None
        assert user.role.name == "admin"


def test_role_change_without_owner_id_blocked(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'users5.db'}"
    init_db(db_url)
    _seed_user(db_url, 556, "normal")

    settings = _make_settings(db_url)
    app = create_flask_app(settings=settings)

    with app.test_client() as client:
        _login(client, "test-secret")
        page = client.get("/users")
        token = _extract_csrf_token(page.get_data(as_text=True))
        response = client.post(
            "/users/556/role",
            data={"role": "vip", "csrf_token": token},
            follow_redirects=False,
        )

    assert response.status_code == 403


def test_role_change_invalid_role_blocked(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'users6.db'}"
    init_db(db_url)
    _seed_user(db_url, 557, "normal")

    settings = _make_settings(db_url, owner_id=777)
    app = create_flask_app(settings=settings)

    with app.test_client() as client:
        _login(client, "test-secret")
        page = client.get("/users")
        token = _extract_csrf_token(page.get_data(as_text=True))
        response = client.post(
            "/users/557/role",
            data={"role": "superadmin", "csrf_token": token},
            follow_redirects=False,
        )

    assert response.status_code == 400


def test_role_change_requires_csrf(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'users7.db'}"
    init_db(db_url)
    _seed_user(db_url, 558, "normal")

    settings = _make_settings(db_url, owner_id=777)
    app = create_flask_app(settings=settings)

    with app.test_client() as client:
        _login(client, "test-secret")
        response = client.post(
            "/users/558/role",
            data={"role": "admin"},
            follow_redirects=False,
        )

    assert response.status_code == 400
