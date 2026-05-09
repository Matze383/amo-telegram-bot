from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from amo_bot.db.base import create_session_factory
from amo_bot.db.init_db import init_db
from amo_bot.db.models import AuditEvent, Plugin
from amo_bot.db.repositories import PluginRepository
from amo_bot.plugins.loader import PluginLoader
from amo_bot.plugins.scheduled_runtime import ScheduledPluginExecutor


def _write_scheduled_plugin(
    tmp_path,
    name: str,
    code: str,
    *,
    interval_seconds: int = 30,
    required_permissions: list[str] | None = None,
) -> PluginLoader:
    plugins_dir = tmp_path / "plugins"
    plugin_dir = plugins_dir / name
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "1.0.0",
                "schedule": {"interval_seconds": interval_seconds},
                "required_roles": ["admin"],
                "required_permissions": ["send_message"] if required_permissions is None else required_permissions,
            }
        ),
        encoding="utf-8",
    )
    (plugin_dir / "main.py").write_text(code, encoding="utf-8")
    return PluginLoader(str(plugins_dir))


def _executor(tmp_path, db_url: str, plugin_name: str, code: str) -> tuple[ScheduledPluginExecutor, list[tuple[int, str]]]:
    loader = _write_scheduled_plugin(tmp_path, plugin_name, code)
    sf = create_session_factory(db_url)
    with sf() as session:
        repo = PluginRepository(session)
        repo.sync_discovered(loader.discover().valid)
        repo.activate(plugin_name, actor_telegram_user_id=1)

    sent: list[tuple[int, str]] = []

    async def _send(chat_id: int, text: str):
        sent.append((chat_id, text))
        return {"ok": True}

    async def _reply(chat_id: int, message_id: int, text: str):
        return {"ok": True, "chat_id": chat_id, "message_id": message_id, "text": text}

    return (
        ScheduledPluginExecutor(
            loader=loader,
            session_factory=sf,
            send_message=_send,
            reply=_reply,
            timeout_seconds=0.05,
            backoff_seconds=10,
        ),
        sent,
    )


def test_scheduled_plugin_run_due_once_success_updates_state_and_audit(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'scheduled1.db'}"
    init_db(db_url)
    executor, sent = _executor(
        tmp_path,
        db_url,
        "scheduled_demo",
        """
async def handle_schedule(context, host_api):
    await host_api.send_message(123, f"scheduled:{context.trigger_type}")
""",
    )

    now = datetime(2030, 1, 1, tzinfo=timezone.utc)
    assert asyncio.run(executor.run_due_once(now=now)) == 1
    assert sent == [(123, "scheduled:schedule")]

    sf = create_session_factory(db_url)
    with sf() as session:
        plugin = session.scalar(select(Plugin).where(Plugin.name == "scheduled_demo"))
        assert plugin is not None
        assert plugin.last_run_at == now.replace(tzinfo=None)
        assert plugin.next_run_at == (now + timedelta(seconds=30)).replace(tzinfo=None)
        assert plugin.last_status == "success"
        events = [row.event_type for row in session.scalars(select(AuditEvent)).all()]
    assert "plugin_schedule_start" in events
    assert "plugin_schedule_success" in events


def test_scheduled_plugin_disabled_or_not_due_is_skipped(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'scheduled2.db'}"
    init_db(db_url)
    loader = _write_scheduled_plugin(
        tmp_path,
        "scheduled_skip",
        """
async def handle_schedule(context, host_api):
    await host_api.send_message(123, "should-not-run")
""",
    )
    sf = create_session_factory(db_url)
    future = datetime(2030, 1, 2, tzinfo=timezone.utc)
    with sf() as session:
        repo = PluginRepository(session)
        repo.sync_discovered(loader.discover().valid)
        plugin = session.scalar(select(Plugin).where(Plugin.name == "scheduled_skip"))
        assert plugin is not None
        plugin.enabled = 1
        plugin.next_run_at = future
        session.commit()

    sent: list[tuple[int, str]] = []

    async def _send(chat_id: int, text: str):
        sent.append((chat_id, text))
        return {"ok": True}

    async def _reply(chat_id: int, message_id: int, text: str):
        return {"ok": True}

    executor = ScheduledPluginExecutor(loader=loader, session_factory=sf, send_message=_send, reply=_reply)
    assert asyncio.run(executor.run_due_once(now=datetime(2030, 1, 1, tzinfo=timezone.utc))) == 0
    assert sent == []


def test_scheduled_plugin_missing_capability_errors_and_audits(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'scheduled4.db'}"
    init_db(db_url)
    loader = _write_scheduled_plugin(
        tmp_path,
        "scheduled_no_cap",
        """
async def handle_schedule(context, host_api):
    await host_api.send_message(123, "blocked")
""",
        required_permissions=[],
    )
    sf = create_session_factory(db_url)
    with sf() as session:
        repo = PluginRepository(session)
        repo.sync_discovered(loader.discover().valid)
        repo.activate("scheduled_no_cap", actor_telegram_user_id=1)

    async def _send(chat_id: int, text: str):
        return {"ok": True}

    async def _reply(chat_id: int, message_id: int, text: str):
        return {"ok": True}

    executor = ScheduledPluginExecutor(loader=loader, session_factory=sf, send_message=_send, reply=_reply, backoff_seconds=10)

    now = datetime(2030, 1, 1, tzinfo=timezone.utc)
    assert asyncio.run(executor.run_due_once(now=now)) == 1

    with sf() as session:
        plugin = session.scalar(select(Plugin).where(Plugin.name == "scheduled_no_cap"))
        assert plugin is not None
        assert plugin.last_status == "error"
        events = session.scalars(select(AuditEvent)).all()
        errors = [row for row in events if row.event_type == "plugin_schedule_error"]
    assert errors
    assert "requires capability 'send_message'" in (errors[-1].payload_json or "")


def test_scheduled_plugin_error_uses_backoff_and_audit(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'scheduled3.db'}"
    init_db(db_url)
    executor, sent = _executor(
        tmp_path,
        db_url,
        "scheduled_error",
        """
async def handle_schedule(context, host_api):
    raise RuntimeError("boom")
""",
    )

    now = datetime(2030, 1, 1, tzinfo=timezone.utc)
    assert asyncio.run(executor.run_due_once(now=now)) == 1
    assert sent == []

    sf = create_session_factory(db_url)
    with sf() as session:
        plugin = session.scalar(select(Plugin).where(Plugin.name == "scheduled_error"))
        assert plugin is not None
        assert plugin.last_run_at == now.replace(tzinfo=None)
        assert plugin.next_run_at == (now + timedelta(seconds=10)).replace(tzinfo=None)
        assert plugin.last_status == "error"
        events = [row.event_type for row in session.scalars(select(AuditEvent)).all()]
    assert "plugin_schedule_error" in events
