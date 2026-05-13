from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import select

from amo_bot.db.base import create_session_factory
from amo_bot.db.init_db import init_db
from amo_bot.db.models import AuditEvent, Plugin
from amo_bot.db.repositories import PluginRepository
from amo_bot.plugins.command_runtime import CommandActor, CommandInvocation, PluginCommandExecutor
from amo_bot.plugins.loader import DiscoveryCode, PluginLoader
from amo_bot.plugins.manifest import PluginManifest
from amo_bot.plugins.scheduled_runtime import ScheduledPluginExecutor
from amo_bot.auth.roles import Role


def _base_manifest(name: str) -> dict[str, object]:
    return {
        "name": name,
        "version": "1.0.0",
        "required_roles": ["admin"],
    }


def test_manifest_schedule_cron_valid_is_accepted() -> None:
    manifest = PluginManifest.model_validate(
        {
            "name": "cron_demo",
            "version": "1.0.0",
            "required_roles": ["admin"],
            "schedule": {"cron": "*/5 * * * *"},
        }
    )
    assert manifest.schedule == {"cron": "*/5 * * * *"}


def test_manifest_schedule_cron_invalid_is_rejected() -> None:
    with pytest.raises(ValueError, match="schedule.cron must be a valid cron expression"):
        PluginManifest.model_validate(
            {
                "name": "bad_cron",
                "version": "1.0.0",
                "required_roles": ["admin"],
                "schedule": {"cron": "not a cron"},
            }
        )


def test_manifest_schedule_interval_too_short_is_rejected() -> None:
    with pytest.raises(ValueError, match="schedule.interval_seconds must be >= 10"):
        PluginManifest.model_validate(
            {
                "name": "fast_interval",
                "version": "1.0.0",
                "required_roles": ["admin"],
                "schedule": {"interval_seconds": 5},
            }
        )


def test_discovery_rejects_ki_triggered(tmp_path) -> None:
    plugins_dir = tmp_path / "plugins"
    plugin_dir = plugins_dir / "ki_demo"
    plugin_dir.mkdir(parents=True)
    payload = _base_manifest("ki_demo")
    payload["commands"] = ["/demo"]
    payload["triggers"] = ["ki-triggered"]
    (plugin_dir / "plugin.json").write_text(json.dumps(payload), encoding="utf-8")

    result = PluginLoader(str(plugins_dir)).discover()

    assert result.valid == []
    assert any(o.plugin_dir == "ki_demo" and o.code == DiscoveryCode.INVALID_TRIGGER_TYPE for o in result.outcomes)


def test_command_collision_within_plugin_is_rejected_by_manifest_validation() -> None:
    with pytest.raises(ValueError, match="commands must contain only non-empty strings"):
        PluginManifest.model_validate(
            {
                "name": "collision",
                "version": "1.0.0",
                "required_roles": ["admin"],
                "commands": ["/dup", "  "],
            }
        )


def test_inactive_plugin_command_not_executed(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'inactive_cmd.db'}"
    init_db(db_url)

    plugins_dir = tmp_path / "plugins"
    pdir = plugins_dir / "inactive_cmd"
    pdir.mkdir(parents=True)
    (pdir / "plugin.json").write_text(
        json.dumps(
            {
                "name": "inactive_cmd",
                "version": "1.0.0",
                "commands": ["/inactive"],
                "required_roles": ["admin"],
                "required_permissions": ["send_message"],
            }
        ),
        encoding="utf-8",
    )
    (pdir / "main.py").write_text(
        """
async def handle_command(context, host_api):
    await host_api.send_message(context.chat_id, "should-not-send")
""",
        encoding="utf-8",
    )

    loader = PluginLoader(str(plugins_dir))
    sf = create_session_factory(db_url)
    with sf() as session:
        PluginRepository(session).sync_discovered(loader.discover().valid)

    sent: list[tuple[int, str]] = []

    async def _send(chat_id: int, text: str):
        sent.append((chat_id, text))
        return {"ok": True}

    async def _reply(chat_id: int, message_id: int, text: str, message_thread_id: int | None = None):
        return {"ok": True}

    executor = PluginCommandExecutor(loader=loader, session_factory=sf, send_message=_send, reply=_reply)

    asyncio.run(
        executor.execute(
            actor=CommandActor(telegram_user_id=1, role=Role.ADMIN),
            invocation=CommandInvocation(command_name="/inactive", argument=None, chat_id=7, message_id=8),
        )
    )

    assert sent == []
    with sf() as session:
        rows = session.scalars(select(AuditEvent)).all()
    assert any(row.event_type == "plugin_command_skipped" for row in rows)


def test_inactive_scheduled_plugin_not_executed(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'inactive_schedule.db'}"
    init_db(db_url)

    plugins_dir = tmp_path / "plugins"
    pdir = plugins_dir / "inactive_schedule"
    pdir.mkdir(parents=True)
    (pdir / "plugin.json").write_text(
        json.dumps(
            {
                "name": "inactive_schedule",
                "version": "1.0.0",
                "schedule": {"interval_seconds": 10},
                "required_roles": ["admin"],
                "required_permissions": ["send_message"],
            }
        ),
        encoding="utf-8",
    )
    (pdir / "main.py").write_text(
        """
async def handle_schedule(context, host_api):
    await host_api.send_message(77, "should-not-send")
""",
        encoding="utf-8",
    )

    loader = PluginLoader(str(plugins_dir))
    sf = create_session_factory(db_url)
    with sf() as session:
        PluginRepository(session).sync_discovered(loader.discover().valid)

    sent: list[tuple[int, str]] = []

    async def _send(chat_id: int, text: str):
        sent.append((chat_id, text))
        return {"ok": True}

    async def _reply(chat_id: int, message_id: int, text: str):
        return {"ok": True}

    executor = ScheduledPluginExecutor(loader=loader, session_factory=sf, send_message=_send, reply=_reply)

    assert asyncio.run(executor.run_due_once()) == 0
    assert sent == []

    with sf() as session:
        plugin = session.scalar(select(Plugin).where(Plugin.name == "inactive_schedule"))
    assert plugin is not None
    assert plugin.enabled == 0
