from __future__ import annotations

import pytest

from amo_bot.config.settings import Settings
from amo_bot.db.repositories import TopicAgentMemoryRepository
from amo_bot.webui.flask_app import create_flask_app


def _make_settings(
    tmp_path,
    password: str = "test-secret",
    session_ttl_seconds: int = 3600,
    webui_public_mode: bool = False,
    webui_require_https: bool = False,
    webui_session_cookie_secure: bool | None = None,
    webui_owner_telegram_id: int | None = None,
    webui_secret_key: str = "test-secret-key-0123456789-abcdef",
) -> Settings:
    payload = {
        "BOT_TOKEN": "dummy-token",
        "TELEGRAM_API_BASE": "https://api.telegram.org",
        "POLL_TIMEOUT_SECONDS": 30,
        "POLL_LIMIT": 100,
        "POLL_RETRY_MAX_SECONDS": 30,
        "OFFSET_STATE_FILE": ".state/offset.json",
        "DATABASE_URL": f"sqlite:///{tmp_path / 'test.sqlite3'}",
        "AMO_PLUGIN_DIR": "./plugins",
        "WEBUI_HOST": "127.0.0.1",
        "WEBUI_PORT": 8080,
        "WEBUI_PASSWORD": password,
        "WEBUI_SECRET_KEY": webui_secret_key,
        "WEBUI_SESSION_TTL_SECONDS": session_ttl_seconds,
        "WEBUI_PUBLIC_MODE": webui_public_mode,
        "WEBUI_REQUIRE_HTTPS": webui_require_https,
        "WEBUI_SESSION_COOKIE_SECURE": webui_session_cookie_secure,
        "WEBUI_LOGIN_DELAY_BASE_SECONDS": 0.25,
        "WEBUI_LOGIN_DELAY_MAX_SECONDS": 1.0,
    }
    if webui_owner_telegram_id is not None:
        payload["WEBUI_OWNER_TELEGRAM_ID"] = webui_owner_telegram_id
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


def test_get_login_page_ok(tmp_path) -> None:
    app = create_flask_app(settings=_make_settings(tmp_path))

    with app.test_client() as client:
        response = client.get("/login")

    assert response.status_code == 200
    assert "<h1>Login</h1>" in response.get_data(as_text=True)


def test_login_wrong_password_rejected(tmp_path) -> None:
    app = create_flask_app(settings=_make_settings(tmp_path))

    with app.test_client() as client:
        response = _login(client, "wrong")

    assert response.status_code == 401
    assert "Ungültiges Passwort" in response.get_data(as_text=True)


def test_login_success_sets_session_authenticated(tmp_path) -> None:
    app = create_flask_app(settings=_make_settings(tmp_path))

    with app.test_client() as client:
        response = _login(client, "test-secret")

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/dashboard")
        with client.session_transaction() as session:
            assert session.get("authenticated") is True
            assert session.permanent is True


def test_logout_clears_session(tmp_path) -> None:
    app = create_flask_app(settings=_make_settings(tmp_path))

    with app.test_client() as client:
        login = _login(client, "test-secret")
        assert login.status_code == 302

        logout = _logout(client)
        assert logout.status_code == 302

        with client.session_transaction() as session:
            assert session.get("authenticated") is None


def test_login_blocked_with_unsafe_password_change_me(tmp_path) -> None:
    app = create_flask_app(settings=_make_settings(tmp_path, password="change_me"))

    with app.test_client() as client:
        response = _login(client, "change_me")

    assert response.status_code == 503
    assert "Login deaktiviert" in response.get_data(as_text=True)


def test_dashboard_requires_login_redirects_to_login(tmp_path) -> None:
    app = create_flask_app(settings=_make_settings(tmp_path))

    with app.test_client() as client:
        response = client.get("/dashboard", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_dashboard_contains_expected_text_after_login(tmp_path) -> None:
    app = create_flask_app(settings=_make_settings(tmp_path))

    with app.test_client() as client:
        login = _login(client, "test-secret")
        assert login.status_code == 302

        dashboard = client.get("/dashboard")
        html = dashboard.get_data(as_text=True)

    assert dashboard.status_code == 200
    assert "AMO Telegram Bot WebUI" in html
    assert "Flask WebUI, lokal/LAN" in html
    assert '<a href="/users">Users</a>' in html
    assert "KI Topic-Agent Status (Read-Only)" in html
    assert "Keine KI-Topic-Agent-Konfigurationen vorhanden." in html
    assert "KI Memory (Read-Only + Deactivate Long Memory)" in html
    assert "Keine Memory-Einträge vorhanden." in html


def test_dashboard_renders_topic_agent_status_read_only_without_secret_or_memory_dump(tmp_path) -> None:
    app = create_flask_app(settings=_make_settings(tmp_path))

    with app.app_context():
        session_factory = app.extensions["amo.plugin_service"]._session_factory
        with session_factory() as session:
            repo = TopicAgentMemoryRepository(session)
            repo.upsert_config(
                scope_type="topic",
                chat_id=-1001,
                topic_id=42,
                ai_enabled=True,
                response_mode="mention_or_reply",
                main_soul_text="TOP-SECRET-MAIN-SOUL",
                topic_soul_text="TOP-SECRET-TOPIC-SOUL",
            )
            repo.upsert_config(
                scope_type="private_user",
                user_id=555,
                ai_enabled=False,
                response_mode="command",
                main_soul_text="PRIVATE-SECRET",
                topic_soul_text="PRIVATE-TOPIC-SECRET",
            )
            repo.upsert_daily_memory(
                scope_type="topic",
                chat_id=-1001,
                topic_id=42,
                memory_date="2026-05-14",
                summary_text="raw daily memory content",
                tokens_estimate=123,
            )
            repo.create_long_memory(
                scope_type="topic",
                chat_id=-1001,
                topic_id=42,
                fact_text="safe long memory summary",
                source_daily_memory_id=1,
            )

    with app.test_client() as client:
        login = _login(client, "test-secret")
        assert login.status_code == 302

        dashboard = client.get("/dashboard")
        html = dashboard.get_data(as_text=True)

    assert dashboard.status_code == 200
    assert "topic" in html
    assert "private_user" in html
    assert "-1001" in html
    assert "42" in html
    assert "555" in html
    assert "active" in html
    assert "inactive" in html
    assert "mention_or_reply" in html
    assert "command" in html
    assert "2026-05-14" in html
    assert "safe long memory summary" in html
    assert "raw daily memory content" not in html

    assert "TOP-SECRET-MAIN-SOUL" not in html
    assert "TOP-SECRET-TOPIC-SOUL" not in html
    assert "PRIVATE-SECRET" not in html
    assert "PRIVATE-TOPIC-SECRET" not in html


def test_deactivate_long_memory_owner_only_and_csrf(tmp_path) -> None:
    app = create_flask_app(settings=_make_settings(tmp_path, webui_owner_telegram_id=777))

    with app.app_context():
        session_factory = app.extensions["amo.plugin_service"]._session_factory
        with session_factory() as session:
            repo = TopicAgentMemoryRepository(session)
            repo.upsert_config(scope_type="topic", chat_id=-1001, topic_id=42, ai_enabled=True, response_mode="mention_or_reply")
            row = repo.create_long_memory(
                scope_type="topic",
                chat_id=-1001,
                topic_id=42,
                fact_text="to deactivate",
                source_daily_memory_id=1,
            )
            memory_id = row.id

    with app.test_client() as client:
        login = _login(client, "test-secret")
        assert login.status_code == 302

        no_csrf = client.post(f"/memory/long/{memory_id}/deactivate", data={}, follow_redirects=False)
        assert no_csrf.status_code == 400

        dashboard = client.get("/dashboard")
        token = _extract_csrf_token(dashboard.get_data(as_text=True))
        ok = client.post(f"/memory/long/{memory_id}/deactivate", data={"csrf_token": token}, follow_redirects=False)
        assert ok.status_code == 302
        assert ok.headers["Location"].endswith("/dashboard")

    with app.app_context():
        session_factory = app.extensions["amo.plugin_service"]._session_factory
        with session_factory() as session:
            repo = TopicAgentMemoryRepository(session)
            rows = repo.list_long_memories(scope_type="topic", chat_id=-1001, topic_id=42, active_only=False)
            assert len(rows) == 1
            assert rows[0].is_active is False


def test_deactivate_long_memory_denied_without_owner_config(tmp_path) -> None:
    app = create_flask_app(settings=_make_settings(tmp_path))

    with app.app_context():
        session_factory = app.extensions["amo.plugin_service"]._session_factory
        with session_factory() as session:
            repo = TopicAgentMemoryRepository(session)
            repo.upsert_config(scope_type="topic", chat_id=-1001, topic_id=42, ai_enabled=True, response_mode="mention_or_reply")
            row = repo.create_long_memory(
                scope_type="topic",
                chat_id=-1001,
                topic_id=42,
                fact_text="should stay active",
                source_daily_memory_id=1,
            )
            memory_id = row.id

    with app.test_client() as client:
        login = _login(client, "test-secret")
        assert login.status_code == 302

        dashboard = client.get("/dashboard")
        token = _extract_csrf_token(dashboard.get_data(as_text=True))
        denied = client.post(f"/memory/long/{memory_id}/deactivate", data={"csrf_token": token}, follow_redirects=False)
        assert denied.status_code == 403

    with app.app_context():
        session_factory = app.extensions["amo.plugin_service"]._session_factory
        with session_factory() as session:
            repo = TopicAgentMemoryRepository(session)
            rows = repo.list_long_memories(scope_type="topic", chat_id=-1001, topic_id=42, active_only=False)
            assert len(rows) == 1
            assert rows[0].is_active is True


def test_logout_blocks_dashboard_again(tmp_path) -> None:
    app = create_flask_app(settings=_make_settings(tmp_path))

    with app.test_client() as client:
        login = _login(client, "test-secret")
        assert login.status_code == 302

        logout = _logout(client)
        assert logout.status_code == 302

        dashboard = client.get("/dashboard", follow_redirects=False)

    assert dashboard.status_code == 302
    assert dashboard.headers["Location"].endswith("/login")


def test_logout_requires_csrf(tmp_path) -> None:
    app = create_flask_app(settings=_make_settings(tmp_path))

    with app.test_client() as client:
        login = _login(client, "test-secret")
        assert login.status_code == 302

        response = client.post("/logout", data={}, follow_redirects=False)

    assert response.status_code == 400


def test_create_app_rejects_missing_webui_secret_key(tmp_path) -> None:
    with pytest.raises(ValueError, match="WEBUI_SECRET_KEY"):
        create_flask_app(settings=_make_settings(tmp_path, webui_secret_key=""))


def test_create_app_rejects_weak_webui_secret_key(tmp_path) -> None:
    with pytest.raises(ValueError, match="WEBUI_SECRET_KEY"):
        create_flask_app(settings=_make_settings(tmp_path, webui_secret_key="change_me"))


def test_create_app_uses_configured_webui_secret_key(tmp_path) -> None:
    strong_secret = "this-is-a-very-strong-test-secret-key-1234567890"
    app = create_flask_app(settings=_make_settings(tmp_path, webui_secret_key=strong_secret))
    assert app.secret_key == strong_secret


def test_webui_secret_key_not_derived_from_password(tmp_path) -> None:
    strong_secret = "independent-secret-key-abcdefghijklmnopqrstuvwxyz"
    app_a = create_flask_app(settings=_make_settings(tmp_path, password="pw-a", webui_secret_key=strong_secret))
    app_b = create_flask_app(settings=_make_settings(tmp_path, password="pw-b", webui_secret_key=strong_secret))
    assert app_a.secret_key == app_b.secret_key == strong_secret


def test_default_cookie_security_flags_present_without_secure(tmp_path) -> None:
    app = create_flask_app(settings=_make_settings(tmp_path))

    with app.test_client() as client:
        response = _login(client, "test-secret")

    cookie = response.headers.get("Set-Cookie", "")
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie
    assert "Secure" not in cookie


def test_security_headers_present_on_normal_response(tmp_path) -> None:
    app = create_flask_app(settings=_make_settings(tmp_path))

    with app.test_client() as client:
        response = client.get("/login")

    assert response.status_code == 200
    csp = response.headers.get("Content-Security-Policy", "")
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "object-src 'none'" in csp
    assert "base-uri 'self'" in csp
    assert "form-action 'self'" in csp
    assert "style-src 'self'" in csp
    assert "'unsafe-inline'" not in csp
    assert response.headers.get("X-Frame-Options") == "DENY"
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    assert response.headers.get("Permissions-Policy") == "geolocation=(), microphone=(), camera=()"


def test_security_headers_present_on_error_response(tmp_path) -> None:
    app = create_flask_app(settings=_make_settings(tmp_path))

    with app.test_client() as client:
        response = client.post("/logout", data={}, follow_redirects=False)

    assert response.status_code == 400
    assert response.headers.get("Content-Security-Policy")
    assert response.headers.get("X-Frame-Options") == "DENY"


def test_secure_mode_sets_secure_cookie_and_hsts(tmp_path) -> None:
    app = create_flask_app(
        settings=_make_settings(tmp_path, webui_public_mode=True, webui_require_https=True, webui_session_cookie_secure=True)
    )
    app.extensions["amo.webui_access_window_service"].enable_for_one_hour(actor_id=123)

    with app.test_client() as client:
        response = _login(client, "test-secret")

    cookie = response.headers.get("Set-Cookie", "")
    assert "Secure" in cookie
    assert response.headers.get("Strict-Transport-Security") == "max-age=31536000; includeSubDomains"


def test_public_mode_requires_https_and_secure_cookie(tmp_path) -> None:
    with pytest.raises(ValueError, match="WEBUI_PUBLIC_MODE=true"):
        create_flask_app(settings=_make_settings(tmp_path, webui_public_mode=True, webui_require_https=False))


def test_login_bruteforce_delay_progressive_and_capped(monkeypatch, tmp_path) -> None:
    app = create_flask_app(settings=_make_settings(tmp_path))
    app.extensions["amo.login_attempt_tracker"] = __import__("amo_bot.webui.flask_blueprints.auth", fromlist=["LoginAttemptTracker"]).LoginAttemptTracker(base_delay_seconds=0.1, max_delay_seconds=0.25)

    delays: list[float] = []
    app.extensions["amo.login_delay_fn"] = lambda seconds: delays.append(seconds)

    with app.test_client() as client:
        for _ in range(4):
            response = _login(client, "wrong")
            assert response.status_code == 401

    assert delays == [0.1, 0.2, 0.25, 0.25]


def test_login_success_resets_delay_counter(monkeypatch, tmp_path) -> None:
    app = create_flask_app(settings=_make_settings(tmp_path))
    app.extensions["amo.login_attempt_tracker"] = __import__("amo_bot.webui.flask_blueprints.auth", fromlist=["LoginAttemptTracker"]).LoginAttemptTracker(base_delay_seconds=0.1, max_delay_seconds=1.0)

    delays: list[float] = []
    app.extensions["amo.login_delay_fn"] = lambda seconds: delays.append(seconds)

    with app.test_client() as client:
        assert _login(client, "wrong").status_code == 401
        assert _login(client, "test-secret").status_code == 302
        assert _login(client, "wrong").status_code == 401

    assert delays == [0.1, 0.1]


def test_login_tracker_caps_distinct_keys_to_prevent_unbounded_growth(tmp_path) -> None:
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


def test_login_writes_auth_audit_events(tmp_path) -> None:
    app = create_flask_app(settings=_make_settings(tmp_path))
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


def test_public_mode_closed_blocks_login_with_403(tmp_path) -> None:
    app = create_flask_app(
        settings=_make_settings(tmp_path, webui_public_mode=True, webui_require_https=True, webui_session_cookie_secure=True)
    )

    with app.test_client() as client:
        response = client.get("/login")

    assert response.status_code == 403
    assert response.get_data(as_text=True).strip() == "forbidden"


def test_public_mode_closed_blocks_protected_pages_with_403(tmp_path) -> None:
    app = create_flask_app(
        settings=_make_settings(tmp_path, webui_public_mode=True, webui_require_https=True, webui_session_cookie_secure=True)
    )

    with app.test_client() as client:
        response = client.get("/dashboard", follow_redirects=False)

    assert response.status_code == 403
    assert response.get_data(as_text=True).strip() == "forbidden"


def test_public_mode_closed_keeps_health_reachable(tmp_path) -> None:
    app = create_flask_app(
        settings=_make_settings(tmp_path, webui_public_mode=True, webui_require_https=True, webui_session_cookie_secure=True)
    )

    with app.test_client() as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_public_mode_open_allows_login_and_password_flow(tmp_path) -> None:
    app = create_flask_app(
        settings=_make_settings(tmp_path, webui_public_mode=True, webui_require_https=True, webui_session_cookie_secure=True)
    )
    app.extensions["amo.webui_access_window_service"].enable_for_one_hour(actor_id=123)

    with app.test_client() as client:
        login_page = client.get("/login")
        assert login_page.status_code == 200

        login = _login(client, "test-secret")

    assert login.status_code == 302
    assert login.headers["Location"].endswith("/dashboard")


def test_public_mode_expired_window_blocks_login_again(tmp_path) -> None:
    from datetime import UTC, datetime, timedelta

    app = create_flask_app(
        settings=_make_settings(tmp_path, webui_public_mode=True, webui_require_https=True, webui_session_cookie_secure=True)
    )
    now = datetime.now(UTC)
    app.extensions["amo.webui_access_window_service"].enable_for_one_hour(actor_id=123, now_utc=now - timedelta(hours=2))

    with app.test_client() as client:
        response = client.get("/login")

    assert response.status_code == 403


def test_public_mode_closed_logout_remains_allowed(tmp_path) -> None:
    app = create_flask_app(
        settings=_make_settings(tmp_path, webui_public_mode=True, webui_require_https=True, webui_session_cookie_secure=True)
    )

    with app.test_client() as client:
        response = client.get("/logout", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_login_page_language_switch_renders_english(tmp_path) -> None:
    app = create_flask_app(settings=_make_settings(tmp_path))

    with app.test_client() as client:
        response = client.get("/login?lang=en")
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert "Language:" in html
        assert "Password" in html




def test_language_switch_preserves_existing_query_args(tmp_path) -> None:
    app = create_flask_app(settings=_make_settings(tmp_path))

    with app.test_client() as client:
        response = client.get("/login?next=%2Fdashboard&foo=bar&lang=de")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert '/login?next=/dashboard&amp;foo=bar&amp;lang=en' in html
    assert '/login?next=/dashboard&amp;foo=bar&amp;lang=de' in html

def test_login_invalid_password_flash_is_bilingual(tmp_path) -> None:
    app = create_flask_app(settings=_make_settings(tmp_path, password="secret123"))

    with app.test_client() as client:
        page = client.get("/login?lang=en")
        token = _extract_csrf_token(page.get_data(as_text=True))
        bad = client.post("/login", data={"password": "wrong", "csrf_token": token}, follow_redirects=False)
        assert bad.status_code == 401
        assert "Invalid password." in bad.get_data(as_text=True)

        page = client.get("/login?lang=de")
        token = _extract_csrf_token(page.get_data(as_text=True))
        bad = client.post("/login", data={"password": "wrong", "csrf_token": token}, follow_redirects=False)
        assert bad.status_code == 401
        assert "Ungültiges Passwort." in bad.get_data(as_text=True)
