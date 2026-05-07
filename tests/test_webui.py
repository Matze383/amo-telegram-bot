from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient
from sqlalchemy import select

from amo_bot.config.settings import Settings
from amo_bot.db.base import create_session_factory
from amo_bot.db.init_db import init_db
from amo_bot.db.models import AuditEvent
from amo_bot.plugins.loader import PluginLoader
from amo_bot.plugins.service import ActionContext, PluginPolicyError, PluginService
from amo_bot.webui.app import WebUISessionStore, create_app


def _make_settings(
    db_url: str,
    plugin_dir: str,
    password: str = "test-secret",
    owner_id: int | None = 42,
    session_ttl_seconds: int = 3600,
) -> Settings:
    payload = {
        "BOT_TOKEN": "dummy-token",
        "TELEGRAM_API_BASE": "https://api.telegram.org",
        "POLL_TIMEOUT_SECONDS": 30,
        "POLL_LIMIT": 100,
        "POLL_RETRY_MAX_SECONDS": 30,
        "OFFSET_STATE_FILE": ".state/offset.json",
        "DATABASE_URL": db_url,
        "AMO_PLUGIN_DIR": plugin_dir,
        "WEBUI_HOST": "127.0.0.1",
        "WEBUI_PORT": 8080,
        "WEBUI_PASSWORD": password,
        "WEBUI_SESSION_TTL_SECONDS": session_ttl_seconds,
    }
    payload["WEBUI_OWNER_TELEGRAM_ID"] = owner_id
    return Settings(_env_file=None, **payload)


def _auth_header(client: TestClient) -> dict[str, str]:
    login = client.post("/auth/login", json={"password": "test-secret"})
    assert login.status_code == 200
    token = login.json()["token"]
    return {"Authorization": f"Bearer {token}"}


def test_health_open(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'webui.db'}"
    init_db(db_url)
    app = create_app(settings=_make_settings(db_url, str(tmp_path / 'plugins')))
    client = TestClient(app)

    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_protected_routes_block_without_auth(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'webui.db'}"
    init_db(db_url)
    app = create_app(settings=_make_settings(db_url, str(tmp_path / 'plugins')))
    client = TestClient(app)

    assert client.get("/dashboard").status_code == 401
    assert client.get("/users/123").status_code == 401
    assert client.post("/users/set-role", json={"target_telegram_user_id": 1, "role": "vip"}).status_code == 401


def test_login_and_dashboard_auth(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'webui.db'}"
    init_db(db_url)
    app = create_app(settings=_make_settings(db_url, str(tmp_path / 'plugins')))
    client = TestClient(app)

    bad = client.post("/auth/login", json={"password": "wrong"})
    assert bad.status_code == 401

    headers = _auth_header(client)
    ok = client.get("/dashboard", headers=headers)
    assert ok.status_code == 200
    assert ok.json()["message"] == "MVP dashboard"


def test_set_role_writes_db_and_audit_uses_server_actor(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'webui.db'}"
    init_db(db_url)
    app = create_app(settings=_make_settings(db_url, str(tmp_path / 'plugins')))
    client = TestClient(app)
    headers = _auth_header(client)

    response = client.post(
        "/users/set-role",
        headers=headers,
        json={
            "actor_telegram_user_id": 900,
            "target_telegram_user_id": 901,
            "role": "owner",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["new_role"] == "owner"

    sf = create_session_factory(db_url)
    with sf() as session:
        events = session.scalars(select(AuditEvent).where(AuditEvent.event_type == "role_set")).all()
    assert len(events) == 1
    assert events[0].actor_telegram_user_id == 42
    assert '"new_role": "owner"' in events[0].payload_json


def test_plugins_route_uses_real_discovery_and_db_status(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'webui.db'}"
    init_db(db_url)

    plugins_dir = tmp_path / "plugins"
    pdir = plugins_dir / "demo"
    pdir.mkdir(parents=True)
    (pdir / "plugin.json").write_text(
        json.dumps(
            {
                "name": "demo",
                "version": "1.0.0",
                "commands": ["/demo"],
                "required_roles": ["admin"],
            }
        ),
        encoding="utf-8",
    )

    sf = create_session_factory(db_url)
    plugin_service = PluginService(loader=PluginLoader(str(plugins_dir)), session_factory=sf)

    app = create_app(
        settings=_make_settings(db_url, str(plugins_dir)),
        plugin_service=plugin_service,
        session_store=WebUISessionStore(),
    )
    client = TestClient(app)
    headers = _auth_header(client)

    listed = client.get("/plugins", headers=headers)
    assert listed.status_code == 200
    payload = listed.json()
    assert payload["plugins"][0]["name"] == "demo"
    assert payload["plugins"][0]["enabled"] is False

    activated = client.post("/plugins/activate", headers=headers, json={"plugin_name": "demo"})
    assert activated.status_code == 200
    assert activated.json()["changed"] is True

    listed_after = client.get("/plugins", headers=headers)
    assert listed_after.status_code == 200
    assert listed_after.json()["plugins"][0]["enabled"] is True


def test_plugin_activation_and_deactivation_mutation_block_and_policy(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'webui.db'}"
    init_db(db_url)

    plugins_dir = tmp_path / "plugins"
    pdir = plugins_dir / "demo"
    pdir.mkdir(parents=True)
    (pdir / "plugin.json").write_text(
        json.dumps(
            {
                "name": "demo",
                "version": "1.0.0",
                "commands": ["/demo"],
                "required_roles": ["admin"],
            }
        ),
        encoding="utf-8",
    )

    sf = create_session_factory(db_url)
    plugin_service = PluginService(loader=PluginLoader(str(plugins_dir)), session_factory=sf)

    app = create_app(
        settings=_make_settings(db_url, str(plugins_dir)),
        plugin_service=plugin_service,
        session_store=WebUISessionStore(),
    )
    client = TestClient(app)
    headers = _auth_header(client)

    activate = client.post("/plugins/activate", headers=headers, json={"plugin_name": "demo"})
    assert activate.status_code == 200

    deactivate = client.post("/plugins/deactivate", headers=headers, json={"plugin_name": "demo"})
    assert deactivate.status_code == 200

    try:
        plugin_service.activate("demo", context=ActionContext.TELEGRAM, actor_telegram_user_id=1)
    except PluginPolicyError:
        pass
    else:
        raise AssertionError("telegram activation must remain blocked")


def test_set_role_blocked_without_server_actor(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'webui.db'}"
    init_db(db_url)
    app = create_app(settings=_make_settings(db_url, str(tmp_path / 'plugins'), owner_id=None))
    client = TestClient(app)
    headers = _auth_header(client)

    response = client.post(
        "/users/set-role",
        headers=headers,
        json={"actor_telegram_user_id": 999, "target_telegram_user_id": 123, "role": "vip"},
    )
    assert response.status_code == 503


def test_plugin_mutation_routes_blocked_without_server_actor(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'webui.db'}"
    init_db(db_url)

    plugins_dir = tmp_path / "plugins"
    pdir = plugins_dir / "demo"
    pdir.mkdir(parents=True)
    (pdir / "plugin.json").write_text(
        json.dumps(
            {
                "name": "demo",
                "version": "1.0.0",
                "commands": ["/demo"],
                "required_roles": ["admin"],
            }
        ),
        encoding="utf-8",
    )

    sf = create_session_factory(db_url)
    plugin_service = PluginService(loader=PluginLoader(str(plugins_dir)), session_factory=sf)

    app = create_app(
        settings=_make_settings(db_url, str(plugins_dir), owner_id=None),
        plugin_service=plugin_service,
        session_store=WebUISessionStore(),
    )
    client = TestClient(app)
    headers = _auth_header(client)

    activate = client.post("/plugins/activate", headers=headers, json={"plugin_name": "demo"})
    assert activate.status_code == 503

    deactivate = client.post("/plugins/deactivate", headers=headers, json={"plugin_name": "demo"})
    assert deactivate.status_code == 503


def test_make_settings_owner_id_none_isolated_from_real_env(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("WEBUI_OWNER_TELEGRAM_ID", "900000001")

    db_url = f"sqlite:///{tmp_path / 'webui.db'}"
    settings = _make_settings(db_url, str(tmp_path / "plugins"), owner_id=None)

    assert settings.webui_owner_telegram_id is None


def test_logout_invalidates_token(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'webui.db'}"
    init_db(db_url)
    app = create_app(settings=_make_settings(db_url, str(tmp_path / 'plugins')))
    client = TestClient(app)
    headers = _auth_header(client)

    logout = client.post("/auth/logout", headers=headers)
    assert logout.status_code == 204

    denied = client.get("/dashboard", headers=headers)
    assert denied.status_code == 401


def test_token_expires_by_ttl(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'webui.db'}"
    init_db(db_url)
    store = WebUISessionStore(ttl_seconds=1)
    app = create_app(settings=_make_settings(db_url, str(tmp_path / 'plugins'), session_ttl_seconds=1), session_store=store)
    client = TestClient(app)

    headers = _auth_header(client)
    assert client.get("/dashboard", headers=headers).status_code == 200

    time.sleep(1.2)
    denied = client.get("/dashboard", headers=headers)
    assert denied.status_code == 401


def test_mutating_routes_blocked_when_password_unsafe(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'webui.db'}"
    init_db(db_url)
    app = create_app(settings=_make_settings(db_url, str(tmp_path / 'plugins'), password="change_me"))
    client = TestClient(app)

    login = client.post("/auth/login", json={"password": "change_me"})
    assert login.status_code == 503
