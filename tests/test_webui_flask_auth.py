from __future__ import annotations

from amo_bot.config.settings import Settings
from amo_bot.webui.flask_app import create_flask_app


def _make_settings(password: str = "test-secret", session_ttl_seconds: int = 3600) -> Settings:
    payload = {
        "BOT_TOKEN": "dummy-token",
        "TELEGRAM_API_BASE": "https://api.telegram.org",
        "POLL_TIMEOUT_SECONDS": 30,
        "POLL_LIMIT": 100,
        "POLL_RETRY_MAX_SECONDS": 30,
        "OFFSET_STATE_FILE": ".state/offset.json",
        "DATABASE_URL": "sqlite:///./data/test_flask_auth.db",
        "AMO_PLUGIN_DIR": "./plugins",
        "WEBUI_HOST": "127.0.0.1",
        "WEBUI_PORT": 8080,
        "WEBUI_PASSWORD": password,
        "WEBUI_SESSION_TTL_SECONDS": session_ttl_seconds,
    }
    return Settings(_env_file=None, **payload)


def _extract_csrf_token(html: str) -> str:
    marker = 'name="csrf_token" type="hidden" value="'
    start = html.find(marker)
    assert start != -1, "csrf token field missing"
    start += len(marker)
    end = html.find('"', start)
    assert end != -1
    return html[start:end]


def _login(client, password: str):
    get_response = client.get("/login")
    token = _extract_csrf_token(get_response.get_data(as_text=True))
    return client.post("/login", data={"password": password, "csrf_token": token}, follow_redirects=False)


def _logout(client):
    get_response = client.get("/login")
    token = _extract_csrf_token(get_response.get_data(as_text=True))
    return client.post("/logout", data={"csrf_token": token}, follow_redirects=False)


def test_get_login_page_ok() -> None:
    app = create_flask_app(settings=_make_settings())

    with app.test_client() as client:
        response = client.get("/login")

    assert response.status_code == 200
    assert "<h1>Login</h1>" in response.get_data(as_text=True)


def test_login_wrong_password_rejected() -> None:
    app = create_flask_app(settings=_make_settings())

    with app.test_client() as client:
        response = _login(client, "wrong")

    assert response.status_code == 401
    assert "Ungültiges Passwort" in response.get_data(as_text=True)


def test_login_success_sets_session_authenticated() -> None:
    app = create_flask_app(settings=_make_settings())

    with app.test_client() as client:
        response = _login(client, "test-secret")

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/dashboard")
        with client.session_transaction() as session:
            assert session.get("authenticated") is True
            assert session.permanent is True


def test_logout_clears_session() -> None:
    app = create_flask_app(settings=_make_settings())

    with app.test_client() as client:
        login = _login(client, "test-secret")
        assert login.status_code == 302

        logout = _logout(client)
        assert logout.status_code == 302

        with client.session_transaction() as session:
            assert session.get("authenticated") is None


def test_login_blocked_with_unsafe_password_change_me() -> None:
    app = create_flask_app(settings=_make_settings(password="change_me"))

    with app.test_client() as client:
        response = _login(client, "change_me")

    assert response.status_code == 503
    assert "Login deaktiviert" in response.get_data(as_text=True)


def test_dashboard_requires_login_redirects_to_login() -> None:
    app = create_flask_app(settings=_make_settings())

    with app.test_client() as client:
        response = client.get("/dashboard", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_dashboard_contains_expected_text_after_login() -> None:
    app = create_flask_app(settings=_make_settings())

    with app.test_client() as client:
        login = _login(client, "test-secret")
        assert login.status_code == 302

        dashboard = client.get("/dashboard")
        html = dashboard.get_data(as_text=True)

    assert dashboard.status_code == 200
    assert "AMO Telegram Bot WebUI" in html
    assert "Flask WebUI, lokal/LAN" in html
    assert "User/Gruppen/Plugins kommen später" in html


def test_logout_blocks_dashboard_again() -> None:
    app = create_flask_app(settings=_make_settings())

    with app.test_client() as client:
        login = _login(client, "test-secret")
        assert login.status_code == 302

        logout = _logout(client)
        assert logout.status_code == 302

        dashboard = client.get("/dashboard", follow_redirects=False)

    assert dashboard.status_code == 302
    assert dashboard.headers["Location"].endswith("/login")


def test_logout_requires_csrf() -> None:
    app = create_flask_app(settings=_make_settings())

    with app.test_client() as client:
        login = _login(client, "test-secret")
        assert login.status_code == 302

        response = client.post("/logout", data={}, follow_redirects=False)

    assert response.status_code == 400
