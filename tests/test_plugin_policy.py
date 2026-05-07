from __future__ import annotations

import json

from sqlalchemy import select

from amo_bot.db.base import create_session_factory
from amo_bot.db.init_db import init_db
from amo_bot.db.models import AuditEvent, Plugin
from amo_bot.plugins.loader import PluginLoader
from amo_bot.plugins.service import ActionContext, PluginPolicyError, PluginService


def _manifest(name: str = "demo") -> dict[str, object]:
    return {
        "name": name,
        "version": "1.0.0",
        "commands": ["/demo"],
        "required_roles": ["admin"],
        "required_permissions": ["send_message"],
    }


def test_discovery_supports_plugin_json_and_manifest_fallback(tmp_path) -> None:
    plugins_dir = tmp_path / "plugins"
    plugin1 = plugins_dir / "a_plugin"
    plugin1.mkdir(parents=True)
    (plugin1 / "plugin.json").write_text(json.dumps(_manifest("plugin-a")), encoding="utf-8")

    plugin2 = plugins_dir / "b_plugin"
    plugin2.mkdir(parents=True)
    (plugin2 / "manifest.json").write_text(json.dumps(_manifest("plugin-b")), encoding="utf-8")

    loader = PluginLoader(str(plugins_dir))
    result = loader.discover()

    assert sorted(m.name for m in result.valid) == ["plugin-a", "plugin-b"]
    assert result.invalid == []


def test_invalid_manifest_is_reported_without_crash(tmp_path) -> None:
    plugins_dir = tmp_path / "plugins"
    plugin_bad = plugins_dir / "broken"
    plugin_bad.mkdir(parents=True)
    (plugin_bad / "plugin.json").write_text('{"name": "broken"}', encoding="utf-8")

    loader = PluginLoader(str(plugins_dir))
    result = loader.discover()

    assert result.valid == []
    assert len(result.invalid) == 1
    assert result.invalid[0].plugin_dir == "broken"
    assert "invalid manifest" in result.invalid[0].error


def test_manifest_with_entrypoint_is_rejected_for_mvp(tmp_path) -> None:
    plugins_dir = tmp_path / "plugins"
    plugin_bad = plugins_dir / "badentry"
    plugin_bad.mkdir(parents=True)
    (plugin_bad / "plugin.json").write_text(
        json.dumps(
            {
                "name": "badentry",
                "version": "1.0",
                "entrypoint": "plugin.main:run",
                "commands": ["/demo"],
                "required_roles": ["owner"],
            }
        ),
        encoding="utf-8",
    )

    loader = PluginLoader(str(plugins_dir))
    result = loader.discover()

    assert result.valid == []
    assert len(result.invalid) == 1


def test_manifest_commands_validation_blocks_empty_values(tmp_path) -> None:
    plugins_dir = tmp_path / "plugins"
    plugin_bad = plugins_dir / "badcmd"
    plugin_bad.mkdir(parents=True)
    (plugin_bad / "plugin.json").write_text(
        json.dumps(
            {
                "name": "badcmd",
                "version": "1.0",
                "commands": ["", "   "],
                "required_roles": ["owner"],
            }
        ),
        encoding="utf-8",
    )

    loader = PluginLoader(str(plugins_dir))
    result = loader.discover()

    assert result.valid == []
    assert len(result.invalid) == 1


def test_activate_deactivate_persist_and_write_audit(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'plugins.db'}"
    init_db(db_url)

    plugins_dir = tmp_path / "plugins"
    pdir = plugins_dir / "demo"
    pdir.mkdir(parents=True)
    (pdir / "plugin.json").write_text(json.dumps(_manifest("demo")), encoding="utf-8")

    sf = create_session_factory(db_url)
    service = PluginService(loader=PluginLoader(str(plugins_dir)), session_factory=sf)

    changed_on = service.activate("demo", context=ActionContext.WEBUI, actor_telegram_user_id=42)
    changed_off = service.deactivate("demo", context=ActionContext.WEBUI, actor_telegram_user_id=42)

    assert changed_on is True
    assert changed_off is True

    with sf() as session:
        plugin = session.scalar(select(Plugin).where(Plugin.name == "demo"))
        assert plugin is not None
        assert bool(plugin.enabled) is False
        events = session.scalars(
            select(AuditEvent).where(AuditEvent.event_type.in_(["plugin_activate", "plugin_deactivate"]))
        ).all()

    assert len(events) == 2


def test_activate_and_deactivate_blocked_for_telegram_context(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'plugins.db'}"
    init_db(db_url)

    plugins_dir = tmp_path / "plugins"
    pdir = plugins_dir / "demo"
    pdir.mkdir(parents=True)
    (pdir / "plugin.json").write_text(json.dumps(_manifest("demo")), encoding="utf-8")

    sf = create_session_factory(db_url)
    service = PluginService(loader=PluginLoader(str(plugins_dir)), session_factory=sf)

    try:
        service.activate("demo", context=ActionContext.TELEGRAM, actor_telegram_user_id=1)
    except PluginPolicyError:
        pass
    else:
        raise AssertionError("telegram activate must be blocked")

    try:
        service.deactivate("demo", context=ActionContext.TELEGRAM, actor_telegram_user_id=1)
    except PluginPolicyError:
        pass
    else:
        raise AssertionError("telegram deactivate must be blocked")
