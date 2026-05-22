from __future__ import annotations

import json

from amo_bot.config.settings import Settings
from amo_bot.webui.flask_app import create_flask_app


def _make_settings(
    database_url: str,
    plugin_dir: str,
    password: str = "test-secret",
    owner_id: int | None = None,
) -> Settings:
    payload = {
        "BOT_TOKEN": "dummy-token",
        "TELEGRAM_API_BASE": "https://api.telegram.org",
        "POLL_TIMEOUT_SECONDS": 30,
        "POLL_LIMIT": 100,
        "POLL_RETRY_MAX_SECONDS": 30,
        "OFFSET_STATE_FILE": ".state/offset.json",
        "DATABASE_URL": database_url,
        "AMO_PLUGIN_DIR": plugin_dir,
        "WEBUI_HOST": "127.0.0.1",
        "WEBUI_PORT": 8080,
        "WEBUI_PASSWORD": password,
        "WEBUI_SECRET_KEY": "test-secret-key-0123456789-abcdef",
        "WEBUI_PUBLIC_MODE": False,
        "WEBUI_REQUIRE_HTTPS": False,
        "WEBUI_SESSION_COOKIE_SECURE": False,
        "WEBUI_LOGIN_DELAY_BASE_SECONDS": 0.25,
        "WEBUI_LOGIN_DELAY_MAX_SECONDS": 1.0,
    }
    if owner_id is not None:
        payload["WEBUI_OWNER_TELEGRAM_ID"] = owner_id
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


def test_plugins_requires_login_redirects_to_login(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'plugins1.db'}"
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir(parents=True)

    app = create_flask_app(settings=_make_settings(db_url, str(plugins_dir)))

    with app.test_client() as client:
        response = client.get("/plugins", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_plugins_with_login_returns_200_and_empty_state(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'plugins2.db'}"
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir(parents=True)

    app = create_flask_app(settings=_make_settings(db_url, str(plugins_dir)))

    with app.test_client() as client:
        _login(client, "test-secret")
        response = client.get("/plugins")
        html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Plugins" in html
    assert "Keine Plugins gefunden." in html


def test_plugins_shows_valid_manifest_data(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'plugins3.db'}"
    plugins_dir = tmp_path / "plugins"
    plugin_dir = plugins_dir / "weather"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps(
            {
                "name": "weather",
                "version": "1.2.3",
                "description": "Weather plugin",
                "commands": ["/weather", "/forecast"],
                "required_roles": ["admin", "vip"],
                "required_permissions": ["send_message", "read_chat"],
                "settings_schema": {
                    "api_key": {
                        "type": "secret",
                        "required": True,
                        "default": "super-secret-default"
                    },
                    "city": {
                        "type": "text",
                        "default": "Berlin"
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    app = create_flask_app(settings=_make_settings(db_url, str(plugins_dir)))

    with app.test_client() as client:
        _login(client, "test-secret")
        response = client.get("/plugins")
        html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "weather" in html
    assert "1.2.3" in html
    assert "/weather, /forecast" in html
    assert "admin, vip" in html
    assert "send_message, read_chat" in html
    assert "activation_pending" in html
    assert "Settings" in html
    assert "<code>api_key</code>" in html
    assert "<code>city</code>" in html
    assert "type: secret, required" in html
    assert "type: text" in html
    assert "super-secret-default" not in html
    assert "kein Wert gesetzt" in html


def test_plugins_rss_config_unavailable_message_without_rss_fetch_permission(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'plugins_rss_ui.db'}"
    plugins_dir = tmp_path / "plugins"

    non_rss_dir = plugins_dir / "weather"
    non_rss_dir.mkdir(parents=True)
    (non_rss_dir / "plugin.json").write_text(
        json.dumps(
            {
                "name": "weather",
                "version": "1.0.0",
                "commands": ["/weather"],
                "required_roles": ["admin"],
                "required_permissions": ["send_message"],
                "settings_schema": {
                    "city": {"type": "text", "default": "Berlin"},
                },
            }
        ),
        encoding="utf-8",
    )

    rss_dir = plugins_dir / "rss_missing_perm"
    rss_dir.mkdir(parents=True)
    (rss_dir / "plugin.json").write_text(
        json.dumps(
            {
                "name": "rss_missing_perm",
                "version": "1.0.0",
                "commands": ["/rss"],
                "required_roles": ["admin"],
                "required_permissions": ["send_message"],
                "settings_schema": {
                    "feed_sources": {"type": "text", "default": "https://example.com/rss.xml"},
                    "poll_interval_seconds": {"type": "number", "default": 300},
                    "label": {"type": "text", "default": "My Feed"},
                },
            }
        ),
        encoding="utf-8",
    )

    app = create_flask_app(settings=_make_settings(db_url, str(plugins_dir)))

    with app.test_client() as client:
        _login(client, "test-secret")
        response = client.get("/plugins")
        html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "weather" in html
    assert "<code>city</code>" in html
    assert "rss_missing_perm" in html
    assert "<code>feed_sources</code>" in html
    assert "<code>poll_interval_seconds</code>" in html
    assert "RSS config unavailable: plugin lacks rss.fetch permission." in html


def test_plugin_enable_disable_requires_owner_id_and_audits(tmp_path) -> None:
    from sqlalchemy import select

    from amo_bot.db.base import create_session_factory
    from amo_bot.db.models import AuditEvent

    db_url = f"sqlite:///{tmp_path / 'plugins5.db'}"
    plugins_dir = tmp_path / "plugins"
    plugin_dir = plugins_dir / "ops"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps(
            {
                "name": "ops",
                "version": "1.0.0",
                "commands": ["ops"],
                "required_roles": ["admin"],
            }
        ),
        encoding="utf-8",
    )

    app = create_flask_app(settings=_make_settings(db_url, str(plugins_dir), owner_id=777))

    sf = create_session_factory(db_url)
    with sf() as session:
        from amo_bot.db.repositories import PluginRepository
        from amo_bot.plugins.loader import PluginLoader

        discovery = PluginLoader(str(plugins_dir)).discover()
        PluginRepository(session).sync_discovered(discovery.valid)

    with app.test_client() as client:
        _login(client, "test-secret")
        page = client.get("/plugins")
        token = _extract_csrf_token(page.get_data(as_text=True))
        response = client.post("/plugins/ops/enable", data={"csrf_token": token}, follow_redirects=False)
        assert response.status_code == 302
        page = client.get("/plugins")
        token = _extract_csrf_token(page.get_data(as_text=True))
        response = client.post("/plugins/ops/disable", data={"csrf_token": token}, follow_redirects=False)
        assert response.status_code == 302

    sf = create_session_factory(db_url)
    with sf() as session:
        events = [row.event_type for row in session.scalars(select(AuditEvent)).all()]
    assert "plugin_activate" in events
    assert "plugin_deactivate" in events


def test_plugin_mutation_without_owner_id_blocked(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'plugins6.db'}"
    plugins_dir = tmp_path / "plugins"
    plugin_dir = plugins_dir / "ops"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps({"name": "ops", "version": "1.0.0", "commands": ["ops"], "required_roles": ["admin"]}),
        encoding="utf-8",
    )

    app = create_flask_app(settings=_make_settings(db_url, str(plugins_dir)))
    app.config["WTF_CSRF_ENABLED"] = False

    with app.test_client() as client:
        with client.session_transaction() as flask_session:
            flask_session["authenticated"] = True
        response = client.post("/plugins/ops/enable", data={}, follow_redirects=False)

    assert response.status_code == 403


def test_worker_buttons_call_worker_manager(tmp_path) -> None:
    class FakeWorkerManager:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def start_sync(self, plugin_name: str) -> bool:
            self.calls.append(("start", plugin_name))
            return True

        def stop_sync(self, plugin_name: str) -> bool:
            self.calls.append(("stop", plugin_name))
            return True

        def restart_sync(self, plugin_name: str) -> bool:
            self.calls.append(("restart", plugin_name))
            return True

    db_url = f"sqlite:///{tmp_path / 'plugins7.db'}"
    plugins_dir = tmp_path / "plugins"
    plugin_dir = plugins_dir / "worker"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps(
            {
                "name": "worker",
                "version": "1.0.0",
                "worker": {"restart_backoff_seconds": 5},
                "required_roles": ["admin"],
            }
        ),
        encoding="utf-8",
    )
    fake = FakeWorkerManager()
    app = create_flask_app(settings=_make_settings(db_url, str(plugins_dir), owner_id=777), worker_manager=fake)

    with app.test_client() as client:
        _login(client, "test-secret")
        page = client.get("/plugins")
        html = page.get_data(as_text=True)
        assert "Start" in html
        assert "Stop" in html
        assert "Restart" in html
        token = _extract_csrf_token(html)
        response = client.post("/plugins/worker/worker/start", data={"csrf_token": token}, follow_redirects=False)

    assert response.status_code == 302
    assert fake.calls == [("start", "worker")]


def test_disable_stops_running_worker_and_deactivates_plugin(tmp_path) -> None:
    class FakeWorkerManager:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def stop_sync(self, plugin_name: str) -> bool:
            self.calls.append(("stop", plugin_name))
            return True

    db_url = f"sqlite:///{tmp_path / 'plugins8.db'}"
    plugins_dir = tmp_path / "plugins"
    plugin_dir = plugins_dir / "worker"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps(
            {
                "name": "worker",
                "version": "1.0.0",
                "worker": {"restart_backoff_seconds": 5},
                "required_roles": ["admin"],
            }
        ),
        encoding="utf-8",
    )
    fake = FakeWorkerManager()
    app = create_flask_app(settings=_make_settings(db_url, str(plugins_dir), owner_id=777), worker_manager=fake)

    with app.test_client() as client:
        _login(client, "test-secret")
        page = client.get("/plugins")
        token = _extract_csrf_token(page.get_data(as_text=True))
        response = client.post("/plugins/worker/disable", data={"csrf_token": token}, follow_redirects=False)

    assert response.status_code == 302
    assert fake.calls == [("stop", "worker")]


def test_plugins_shows_invalid_manifest_with_reason(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'plugins4.db'}"
    plugins_dir = tmp_path / "plugins"
    bad_dir = plugins_dir / "broken"
    bad_dir.mkdir(parents=True)
    (bad_dir / "plugin.json").write_text('{"name": "broken"}', encoding="utf-8")

    app = create_flask_app(settings=_make_settings(db_url, str(plugins_dir)))

    with app.test_client() as client:
        _login(client, "test-secret")
        response = client.get("/plugins")
        html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Fehlerhafte Manifeste" in html
    assert "broken" in html
    assert "fehlerhaft" in html
    assert "invalid manifest" in html


def test_plugins_shows_runtime_history_fields(tmp_path) -> None:
    from datetime import datetime, timezone

    from amo_bot.db.base import create_session_factory
    from amo_bot.db.models import Plugin
    from amo_bot.db.repositories import PluginRepository
    from amo_bot.plugins.manifest import PluginManifest

    db_url = f"sqlite:///{tmp_path / 'plugins9.db'}"
    plugins_dir = tmp_path / "plugins"
    plugin_dir = plugins_dir / "sched_worker"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps(
            {
                "name": "sched_worker",
                "version": "1.0.0",
                "schedule": {"interval_seconds": 60},
                "worker": {"restart_backoff_seconds": 30},
                "required_roles": ["admin"],
            }
        ),
        encoding="utf-8",
    )

    app = create_flask_app(settings=_make_settings(db_url, str(plugins_dir), owner_id=777))

    sf = create_session_factory(db_url)
    with sf() as session:
        manifest = PluginManifest.model_validate_json((plugin_dir / "plugin.json").read_text(encoding="utf-8"))
        PluginRepository(session).sync_discovered([manifest])
        plugin = session.query(Plugin).filter(Plugin.name == "sched_worker").one()
        plugin.last_run_at = datetime(2030, 1, 1, 9, 0, 0, tzinfo=timezone.utc).replace(tzinfo=None)
        plugin.last_status = "error"
        plugin.next_run_at = datetime(2030, 1, 1, 9, 5, 0, tzinfo=timezone.utc).replace(tzinfo=None)
        plugin.worker_state = "crashed"
        plugin.worker_restart_count = 3
        plugin.worker_next_restart_at = datetime(2030, 1, 1, 9, 10, 0, tzinfo=timezone.utc).replace(tzinfo=None)
        plugin.worker_last_heartbeat_at = datetime(2030, 1, 1, 8, 59, 0, tzinfo=timezone.utc).replace(tzinfo=None)
        plugin.worker_last_error = "boom"
        session.commit()

    with app.test_client() as client:
        _login(client, "test-secret")
        response = client.get("/plugins")
        html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Laufhistorie" in html
    assert "Worker-Status" in html
    assert "last_run_at: 2030-01-01 09:00:00" in html
    assert "last_status: error" in html
    assert "next_run_at: 2030-01-01 09:05:00" in html
    assert "state: crashed" in html
    assert "restart_count: 3" in html
    assert "next_restart_at: 2030-01-01 09:10:00" in html
    assert "last_heartbeat_at: 2030-01-01 08:59:00" in html
    assert "last_error: boom" in html


def test_plugins_policy_section_shows_defaults(tmp_path) -> None:
    from amo_bot.db.base import create_session_factory
    from amo_bot.db.repositories import ChatTopicRepository
    db_url = f"sqlite:///{tmp_path / 'plugins10.db'}"
    plugins_dir = tmp_path / "plugins"
    plugin_dir = plugins_dir / "scope"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps({"name": "scope", "version": "1.0.0", "commands": ["scope"], "required_roles": ["admin"]}),
        encoding="utf-8",
    )

    app = create_flask_app(settings=_make_settings(db_url, str(plugins_dir), owner_id=777))

    sf = create_session_factory(db_url)
    with sf() as session:
        chat_repo = ChatTopicRepository(session)
        chat_repo.upsert_chat(chat_id=-1001, chat_type="group", title="Team Alpha", username=None)
        chat_repo.upsert_chat(chat_id=-2002, chat_type="supergroup", title="Team Beta", username=None)

    with app.test_client() as client:
        _login(client, "test-secret")
        response = client.get("/plugins")
        html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert '<select name="roles_mode">\n                        <option value="inherit" selected>inherit</option>' in html
    assert '<select name="private_mode">\n                        <option value="inherit" selected>inherit</option>' in html
    assert '<select name="groups_mode">\n                        <option value="inherit" selected>inherit</option>' in html
    assert 'name="required_roles" value="owner" checked' not in html
    assert 'name="required_roles" value="admin" checked' not in html
    assert 'name="required_roles" value="vip" checked' not in html
    assert 'name="required_roles" value="normal" checked' not in html


def test_plugin_policy_post_saves_roles_override(tmp_path) -> None:
    from amo_bot.db.base import create_session_factory
    from amo_bot.db.repositories import ChatTopicRepository, PluginPolicyOverrideRepository

    db_url = f"sqlite:///{tmp_path / 'plugins11.db'}"
    plugins_dir = tmp_path / "plugins"
    plugin_dir = plugins_dir / "scope"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps({"name": "scope", "version": "1.0.0", "commands": ["scope"], "required_roles": ["admin"]}),
        encoding="utf-8",
    )

    app = create_flask_app(settings=_make_settings(db_url, str(plugins_dir), owner_id=777))

    sf = create_session_factory(db_url)
    with sf() as session:
        chat_repo = ChatTopicRepository(session)
        chat_repo.upsert_chat(chat_id=-1001, chat_type="group", title="Team Alpha", username=None)
        chat_repo.upsert_chat(chat_id=-2002, chat_type="supergroup", title="Team Beta", username=None)

    with app.test_client() as client:
        _login(client, "test-secret")
        page = client.get("/plugins")
        token = _extract_csrf_token(page.get_data(as_text=True))
        response = client.post(
            "/plugins/scope/policy",
            data={
                "csrf_token": token,
                "roles_mode": "override",
                "required_roles": ["admin", "vip"],
                "private_mode": "inherit",
                "groups_mode": "inherit",
            },
            follow_redirects=False,
        )

    assert response.status_code == 302

    sf = create_session_factory(db_url)
    with sf() as session:
        snap = PluginPolicyOverrideRepository(session).get_snapshot(plugin_name="scope")

    assert snap is not None
    assert snap.roles_mode == "override"
    assert [role.value for role in snap.required_roles] == ["admin", "vip"]


def test_plugin_policy_get_renders_saved_override(tmp_path) -> None:
    from amo_bot.db.base import create_session_factory
    from amo_bot.db.repositories import ChatTopicRepository
    db_url = f"sqlite:///{tmp_path / 'plugins12.db'}"
    plugins_dir = tmp_path / "plugins"
    plugin_dir = plugins_dir / "scope"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps({"name": "scope", "version": "1.0.0", "commands": ["scope"], "required_roles": ["admin"]}),
        encoding="utf-8",
    )

    app = create_flask_app(settings=_make_settings(db_url, str(plugins_dir), owner_id=777))

    sf = create_session_factory(db_url)
    with sf() as session:
        chat_repo = ChatTopicRepository(session)
        chat_repo.upsert_chat(chat_id=-1001, chat_type="group", title="Team Alpha", username=None)
        chat_repo.upsert_chat(chat_id=-2002, chat_type="supergroup", title="Team Beta", username=None)

    with app.test_client() as client:
        _login(client, "test-secret")
        page = client.get("/plugins")
        token = _extract_csrf_token(page.get_data(as_text=True))
        response = client.post(
            "/plugins/scope/policy",
            data={
                "csrf_token": token,
                "roles_mode": "override",
                "required_roles": ["admin", "vip"],
                "private_mode": "deny",
                "groups_mode": "allow",
                "allowed_group_ids": ["-2002", "-1001", "-2002"],
            },
            follow_redirects=False,
        )
        assert response.status_code == 302

        rendered = client.get("/plugins")
        html = rendered.get_data(as_text=True)

    assert rendered.status_code == 200
    assert '<option value="override" selected>override</option>' in html
    assert 'name="required_roles" value="admin" checked' in html
    assert 'name="required_roles" value="vip" checked' in html
    assert '<option value="deny" selected>deny</option>' in html
    assert '<option value="allow" selected>allow</option>' in html
    assert 'value="-1001" checked' in html
    assert 'value="-2002" checked' in html


def test_plugin_policy_post_rejects_invalid_modes_and_roles(tmp_path) -> None:
    from amo_bot.db.base import create_session_factory
    from amo_bot.db.repositories import ChatTopicRepository
    db_url = f"sqlite:///{tmp_path / 'plugins13.db'}"
    plugins_dir = tmp_path / "plugins"
    plugin_dir = plugins_dir / "scope"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps({"name": "scope", "version": "1.0.0", "commands": ["scope"], "required_roles": ["admin"]}),
        encoding="utf-8",
    )

    app = create_flask_app(settings=_make_settings(db_url, str(plugins_dir), owner_id=777))

    sf = create_session_factory(db_url)
    with sf() as session:
        ChatTopicRepository(session).upsert_chat(chat_id=-1001, chat_type="group", title="Known Group", username=None)

    with app.test_client() as client:
        _login(client, "test-secret")

        page = client.get("/plugins")
        token = _extract_csrf_token(page.get_data(as_text=True))
        resp_invalid_roles_mode = client.post(
            "/plugins/scope/policy",
            data={
                "csrf_token": token,
                "roles_mode": "nope",
                "private_mode": "inherit",
                "groups_mode": "inherit",
            },
            follow_redirects=False,
        )

        page2 = client.get("/plugins")
        token2 = _extract_csrf_token(page2.get_data(as_text=True))
        resp_invalid_private_mode = client.post(
            "/plugins/scope/policy",
            data={
                "csrf_token": token2,
                "roles_mode": "inherit",
                "private_mode": "nope",
                "groups_mode": "inherit",
            },
            follow_redirects=False,
        )

        page3 = client.get("/plugins")
        token3 = _extract_csrf_token(page3.get_data(as_text=True))
        resp_invalid_groups_mode = client.post(
            "/plugins/scope/policy",
            data={
                "csrf_token": token3,
                "roles_mode": "inherit",
                "private_mode": "inherit",
                "groups_mode": "nope",
            },
            follow_redirects=False,
        )

        page4 = client.get("/plugins")
        token4 = _extract_csrf_token(page4.get_data(as_text=True))
        resp_invalid_role = client.post(
            "/plugins/scope/policy",
            data={
                "csrf_token": token4,
                "roles_mode": "override",
                "required_roles": ["hacker"],
                "private_mode": "inherit",
                "groups_mode": "inherit",
            },
            follow_redirects=False,
        )

        page5 = client.get("/plugins")
        token5 = _extract_csrf_token(page5.get_data(as_text=True))
        resp_invalid_group_id = client.post(
            "/plugins/scope/policy",
            data={
                "csrf_token": token5,
                "roles_mode": "inherit",
                "private_mode": "inherit",
                "groups_mode": "allow",
                "allowed_group_ids": ["not-an-int"],
            },
            follow_redirects=False,
        )

        page6 = client.get("/plugins")
        token6 = _extract_csrf_token(page6.get_data(as_text=True))
        resp_unknown_group_id = client.post(
            "/plugins/scope/policy",
            data={
                "csrf_token": token6,
                "roles_mode": "inherit",
                "private_mode": "inherit",
                "groups_mode": "allow",
                "allowed_group_ids": ["-9999"],
            },
            follow_redirects=False,
        )

        page7 = client.get("/plugins")
        token7 = _extract_csrf_token(page7.get_data(as_text=True))
        resp_allow_without_groups = client.post(
            "/plugins/scope/policy",
            data={
                "csrf_token": token7,
                "roles_mode": "inherit",
                "private_mode": "inherit",
                "groups_mode": "allow",
            },
            follow_redirects=False,
        )

    assert resp_invalid_roles_mode.status_code == 400
    assert resp_invalid_private_mode.status_code == 400
    assert resp_invalid_groups_mode.status_code == 400
    assert resp_invalid_role.status_code == 400
    assert resp_invalid_group_id.status_code == 400
    assert resp_unknown_group_id.status_code == 400
    assert resp_allow_without_groups.status_code == 302

    sf2 = create_session_factory(db_url)
    with sf2() as session:
        from amo_bot.db.repositories import PluginPolicyOverrideRepository

        policy = PluginPolicyOverrideRepository(session).get_snapshot(plugin_name="scope")

    assert policy is not None
    assert policy.groups_mode == "allow"
    assert policy.allowed_group_ids == []


def test_plugin_policy_topics_post_saves_and_renders_checked(tmp_path) -> None:
    from amo_bot.db.base import create_session_factory
    from amo_bot.db.repositories import ChatTopicRepository, PluginPolicyOverrideRepository

    db_url = f"sqlite:///{tmp_path / 'plugins14.db'}"
    plugins_dir = tmp_path / "plugins"
    plugin_dir = plugins_dir / "scope"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps({"name": "scope", "version": "1.0.0", "commands": ["scope"], "required_roles": ["admin"]}),
        encoding="utf-8",
    )

    app = create_flask_app(settings=_make_settings(db_url, str(plugins_dir), owner_id=777))

    sf = create_session_factory(db_url)
    with sf() as session:
        chat_repo = ChatTopicRepository(session)
        chat_repo.upsert_chat(chat_id=-1001, chat_type="group", title="Team Alpha", username=None)
        chat_repo.upsert_topic(chat_id=-1001, message_thread_id=11, telegram_topic_name="Alpha Ops")
        chat_repo.upsert_topic(chat_id=-1001, message_thread_id=12, telegram_topic_name="Alpha Dev")

    with app.test_client() as client:
        _login(client, "test-secret")
        page = client.get("/plugins")
        token = _extract_csrf_token(page.get_data(as_text=True))
        response = client.post(
            "/plugins/scope/policy",
            data={
                "csrf_token": token,
                "roles_mode": "inherit",
                "private_mode": "inherit",
                "groups_mode": "inherit",
                "topics_mode": "allow",
                "allowed_topics": ["-1001:11", "-1001:12", "-1001:11"],
            },
            follow_redirects=False,
        )
        assert response.status_code == 302

        rendered = client.get("/plugins")
        html = rendered.get_data(as_text=True)

    with sf() as session:
        snap = PluginPolicyOverrideRepository(session).get_snapshot(plugin_name="scope")

    assert snap is not None
    assert snap.topics_mode == "allow"
    assert snap.allowed_topics == [(-1001, 11), (-1001, 12)]
    assert rendered.status_code == 200
    assert "Alpha Ops" in html
    assert "Alpha Dev" in html
    assert 'value="-1001:11" checked' in html
    assert 'value="-1001:12" checked' in html


def test_plugin_policy_topics_rejects_invalid_unknown_and_allows_empty(tmp_path) -> None:
    from amo_bot.db.base import create_session_factory
    from amo_bot.db.repositories import ChatTopicRepository, PluginPolicyOverrideRepository

    db_url = f"sqlite:///{tmp_path / 'plugins15.db'}"
    plugins_dir = tmp_path / "plugins"
    plugin_dir = plugins_dir / "scope"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps({"name": "scope", "version": "1.0.0", "commands": ["scope"], "required_roles": ["admin"]}),
        encoding="utf-8",
    )

    app = create_flask_app(settings=_make_settings(db_url, str(plugins_dir), owner_id=777))

    sf = create_session_factory(db_url)
    with sf() as session:
        chat_repo = ChatTopicRepository(session)
        chat_repo.upsert_chat(chat_id=-1001, chat_type="group", title="Team Alpha", username=None)
        chat_repo.upsert_topic(chat_id=-1001, message_thread_id=11, telegram_topic_name="Alpha Ops")

    with app.test_client() as client:
        _login(client, "test-secret")

        page = client.get("/plugins")
        token = _extract_csrf_token(page.get_data(as_text=True))
        invalid = client.post(
            "/plugins/scope/policy",
            data={
                "csrf_token": token,
                "roles_mode": "inherit",
                "private_mode": "inherit",
                "groups_mode": "inherit",
                "topics_mode": "allow",
                "allowed_topics": ["not-a-topic"],
            },
            follow_redirects=False,
        )

        page2 = client.get("/plugins")
        token2 = _extract_csrf_token(page2.get_data(as_text=True))
        unknown = client.post(
            "/plugins/scope/policy",
            data={
                "csrf_token": token2,
                "roles_mode": "inherit",
                "private_mode": "inherit",
                "groups_mode": "inherit",
                "topics_mode": "allow",
                "allowed_topics": ["-1001:9999"],
            },
            follow_redirects=False,
        )

        page3 = client.get("/plugins")
        token3 = _extract_csrf_token(page3.get_data(as_text=True))
        empty = client.post(
            "/plugins/scope/policy",
            data={
                "csrf_token": token3,
                "roles_mode": "inherit",
                "private_mode": "inherit",
                "groups_mode": "inherit",
                "topics_mode": "allow",
            },
            follow_redirects=False,
        )

    assert invalid.status_code == 400
    assert unknown.status_code == 400
    assert empty.status_code == 302

    with sf() as session:
        snap = PluginPolicyOverrideRepository(session).get_snapshot(plugin_name="scope")

    assert snap is not None
    assert snap.topics_mode == "allow"
    assert snap.allowed_topics == []


def test_plugins_policy_section_shows_ai_tool_toggle_default_off(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'plugins16.db'}"
    plugins_dir = tmp_path / "plugins"
    plugin_dir = plugins_dir / "scope"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps({"name": "scope", "version": "1.0.0", "commands": ["scope"], "required_roles": ["admin"]}),
        encoding="utf-8",
    )

    app = create_flask_app(settings=_make_settings(db_url, str(plugins_dir), owner_id=777))

    with app.test_client() as client:
        _login(client, "test-secret")
        response = client.get("/plugins")
        html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'name="ai_tool_enabled" value="1"' in html
    assert 'name="ai_tool_enabled" value="1" checked' not in html
    assert 'name="ai_tool_enabled" value="1" disabled' in html


def test_plugin_policy_post_ignores_ai_tool_enabled_input_and_runtime_deny_remains(tmp_path) -> None:
    import asyncio

    from amo_bot.ai.tool_registry import (
        AIRole,
        AIScopeKind,
        AIToolPolicy,
        AIToolScopeContext,
        invoke_tool_noop,
        validate_tool_invocation_request,
    )

    db_url = f"sqlite:///{tmp_path / 'plugins17.db'}"
    plugins_dir = tmp_path / "plugins"
    plugin_dir = plugins_dir / "scope"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps({"name": "scope", "version": "1.0.0", "commands": ["scope"], "required_roles": ["admin"]}),
        encoding="utf-8",
    )

    app = create_flask_app(settings=_make_settings(db_url, str(plugins_dir), owner_id=777))

    with app.test_client() as client:
        _login(client, "test-secret")
        page = client.get("/plugins")
        token = _extract_csrf_token(page.get_data(as_text=True))
        response = client.post(
            "/plugins/scope/policy",
            data={
                "csrf_token": token,
                "roles_mode": "inherit",
                "private_mode": "inherit",
                "groups_mode": "inherit",
                "topics_mode": "inherit",
                "ai_tool_enabled": "1",
            },
            follow_redirects=False,
        )

    assert response.status_code == 302

    req, err = validate_tool_invocation_request({"tool_name": "scope", "arguments": {}})
    assert err is None and req is not None
    denied = asyncio.run(
        invoke_tool_noop(
            request=req,
            policy=AIToolPolicy(),
            role=AIRole.OWNER,
            scope=AIToolScopeContext(scope_kind=AIScopeKind.TOPIC, chat_id=-1001, topic_id=11),
        )
    )
    assert denied.status.value == "denied"
    assert denied.error_code == "policy_denied"
    assert denied.reason == "tools_disabled"


def test_plugins_language_switch_en(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'plugins_lang.db'}"
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir(parents=True)
    app = create_flask_app(settings=_make_settings(db_url, str(plugins_dir)))

    with app.test_client() as client:
        with client.session_transaction() as flask_session:
            flask_session["authenticated"] = True
        response = client.get("/plugins?lang=en")
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert "Language:" in html
        assert "Plugins" in html
        assert "No plugins found." in html
