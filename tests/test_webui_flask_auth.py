from __future__ import annotations

import pytest

from amo_bot.config.settings import Settings
from amo_bot.webui.flask_app import create_flask_app


def _make_settings(
    password: str = "test-secret",
    session_ttl_seconds: int = 3600,
    webui_public_mode: bool = False,
    webui_require_https: bool = False,
    webui_session_cookie_secure: bool | None = None,
) -> Settings:
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
        "WEBUI_PUBLIC_MODE": webui_public_mode,
        "WEBUI_REQUIRE_HTTPS": webui_require_https,
        "WEBUI_SESSION_COOKIE_SECURE": webui_session_cookie_secure,
        "WEBUI_LOGIN_DELAY_BASE_SECONDS": 0.25,
        "WEBUI_LOGIN_DELAY_MAX_SECONDS": 1.0,
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
    assert '<a href="/users">Users</a>' in html


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


def test_default_cookie_security_flags_present_without_secure() -> None:
    app = create_flask_app(settings=_make_settings())

    with app.test_client() as client:
        response = _login(client, "test-secret")

    cookie = response.headers.get("Set-Cookie", "")
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie
    assert "Secure" not in cookie


def test_security_headers_present_on_normal_response() -> None:
    app = create_flask_app(settings=_make_settings())

    with app.test_client() as client:
        response = client.get("/login")

    assert response.status_code == 200
    csp = response.headers.get("Content-Security-Policy", "")
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "object-src 'none'" in csp
    assert "base-uri 'self'" in csp
    assert "form-action 'self'" in csp
    assert response.headers.get("X-Frame-Options") == "DENY"
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    assert response.headers.get("Permissions-Policy") == "geolocation=(), microphone=(), camera=()"


def test_security_headers_present_on_error_response() -> None:
    app = create_flask_app(settings=_make_settings())

    with app.test_client() as client:
        response = client.post("/logout", data={}, follow_redirects=False)

    assert response.status_code == 400
    assert response.headers.get("Content-Security-Policy")
    assert response.headers.get("X-Frame-Options") == "DENY"


def test_secure_mode_sets_secure_cookie_and_hsts() -> None:
    app = create_flask_app(
        settings=_make_settings(webui_public_mode=True, webui_require_https=True, webui_session_cookie_secure=True)
    )

    with app.test_client() as client:
        response = _login(client, "test-secret")

    cookie = response.headers.get("Set-Cookie", "")
    assert "Secure" in cookie
    assert response.headers.get("Strict-Transport-Security") == "max-age=31536000; includeSubDomains"


def test_public_mode_requires_https_and_secure_cookie() -> None:
    with pytest.raises(ValueError, match="WEBUI_PUBLIC_MODE=true"):
        create_flask_app(settings=_make_settings(webui_public_mode=True, webui_require_https=False))


def test_login_bruteforce_delay_progressive_and_capped(monkeypatch) -> None:
    app = create_flask_app(settings=_make_settings())
    app.extensions["amo.login_attempt_tracker"] = __import__("amo_bot.webui.flask_blueprints.auth", fromlist=["LoginAttemptTracker"]).LoginAttemptTracker(base_delay_seconds=0.1, max_delay_seconds=0.25)

    delays: list[float] = []
    app.extensions["amo.login_delay_fn"] = lambda seconds: delays.append(seconds)

    with app.test_client() as client:
        for _ in range(4):
            response = _login(client, "wrong")
            assert response.status_code == 401

    assert delays == [0.1, 0.2, 0.25, 0.25]


def test_login_success_resets_delay_counter(monkeypatch) -> None:
    app = create_flask_app(settings=_make_settings())
    app.extensions["amo.login_attempt_tracker"] = __import__("amo_bot.webui.flask_blueprints.auth", fromlist=["LoginAttemptTracker"]).LoginAttemptTracker(base_delay_seconds=0.1, max_delay_seconds=1.0)

    delays: list[float] = []
    app.extensions["amo.login_delay_fn"] = lambda seconds: delays.append(seconds)

    with app.test_client() as client:
        assert _login(client, "wrong").status_code == 401
        assert _login(client, "test-secret").status_code == 302
        assert _login(client, "wrong").status_code == 401

    assert delays == [0.1, 0.1]


def test_login_tracker_caps_distinct_keys_to_prevent_unbounded_growth() -> None:
    tracker_cls = __import__("amo_bot.webui.flask_blueprints.auth", fromlist=["LoginAttemptTracker"]).LoginAttemptTracker
    tracker = tracker_cls(base_delay_seconds=0.1, max_delay_seconds=1.0, max_keys=2)

    assert tracker.next_delay_seconds("ip-1") == 0.1
    assert tracker.next_delay_seconds("ip-2") == 0.1
    assert len(tracker._failures_by_key) == 2

    # A new key above cap evicts the oldest key.
    assert tracker.next_delay_seconds("ip-3") == 0.1
    assert len(tracker._failures_by_key) == 2
    assert "ip-1" not in tracker._failures_by_key
    assert "ip-2" in tracker._failures_by_key
    assert "ip-3" in tracker._failures_by_key


def test_login_writes_auth_audit_events() -> None:
    app = create_flask_app(settings=_make_settings())
    app.extensions["amo.login_delay_fn"] = lambda _seconds: None

    with app.test_client() as client:
        assert _login(client, "wrong").status_code == 401
        assert _login(client, "test-secret").status_code == 302

    from sqlalchemy import select

    session_factory = app.extensions["amo.session_factory"]
    with session_factory() as db_session:
        events = db_session.execute(
            select(__import__("amo_bot.db.models", fromlist=["AuditEvent"]).AuditEvent)
            .where(__import__("amo_bot.db.models", fromlist=["AuditEvent"]).AuditEvent.event_type.in_(["webui_login_failure", "webui_login_success"]))
            .order_by(__import__("amo_bot.db.models", fromlist=["AuditEvent"]).AuditEvent.id.asc())
        ).scalars().all()

    assert [event.event_type for event in events[-2:]] == ["webui_login_failure", "webui_login_success"]
