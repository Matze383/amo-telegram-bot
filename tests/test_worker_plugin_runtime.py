from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from sqlalchemy import select

from amo_bot.db.base import create_session_factory
from amo_bot.db.init_db import init_db
from amo_bot.db.models import AuditEvent, Plugin
from amo_bot.db.repositories import PluginRepository
from amo_bot.plugins.loader import PluginLoader
from amo_bot.plugins.worker_runtime import WorkerPluginManager


def _write_worker_plugin(tmp_path, name: str, code: str, *, backoff_seconds: int = 30) -> PluginLoader:
    plugins_dir = tmp_path / "plugins"
    plugin_dir = plugins_dir / name
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "1.0.0",
                "worker": {"restart_backoff_seconds": backoff_seconds},
                "required_roles": ["admin"],
            }
        ),
        encoding="utf-8",
    )
    (plugin_dir / "main.py").write_text(code, encoding="utf-8")
    return PluginLoader(str(plugins_dir))


def _manager(tmp_path, db_url: str, plugin_name: str, code: str) -> WorkerPluginManager:
    loader = _write_worker_plugin(tmp_path, plugin_name, code)
    sf = create_session_factory(db_url)
    with sf() as session:
        repo = PluginRepository(session)
        repo.sync_discovered(loader.discover().valid)
        repo.activate(plugin_name, actor_telegram_user_id=1)

    async def _send(chat_id: int, text: str):
        return {"ok": True, "chat_id": chat_id, "text": text}

    async def _reply(chat_id: int, message_id: int, text: str):
        return {"ok": True, "chat_id": chat_id, "message_id": message_id, "text": text}

    return WorkerPluginManager(loader=loader, session_factory=sf, send_message=_send, reply=_reply)


def test_worker_start_stop_updates_state_and_audit(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'worker1.db'}"
    init_db(db_url)
    manager = _manager(
        tmp_path,
        db_url,
        "worker_demo",
        """
import asyncio
async def handle_worker(context, host_api):
    await asyncio.sleep(60)
""",
    )

    now = datetime(2030, 1, 1, tzinfo=timezone.utc)

    async def _run() -> None:
        assert await manager.start("worker_demo", now=now) is True
        assert manager.state("worker_demo") == "running"
        assert await manager.stop("worker_demo", now=now) is True
        assert manager.state("worker_demo") == "stopped"

    asyncio.run(_run())

    sf = create_session_factory(db_url)
    with sf() as session:
        plugin = session.scalar(select(Plugin).where(Plugin.name == "worker_demo"))
        assert plugin is not None
        assert plugin.worker_state == "stopped"
        events = [row.event_type for row in session.scalars(select(AuditEvent)).all()]
    assert "plugin_worker_start" in events
    assert "plugin_worker_stop" in events


def test_worker_crash_sets_backoff_and_audit(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'worker2.db'}"
    init_db(db_url)
    manager = _manager(
        tmp_path,
        db_url,
        "worker_crash",
        """
async def handle_worker(context, host_api):
    raise RuntimeError("boom")
""",
    )

    async def _run() -> None:
        assert await manager.start("worker_crash", now=datetime(2030, 1, 1, tzinfo=timezone.utc)) is True
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(_run())

    sf = create_session_factory(db_url)
    with sf() as session:
        plugin = session.scalar(select(Plugin).where(Plugin.name == "worker_crash"))
        assert plugin is not None
        assert plugin.worker_state == "crashed"
        assert plugin.worker_restart_count == 1
        assert plugin.worker_next_restart_at is not None
        assert "boom" in (plugin.worker_last_error or "")
        events = [row.event_type for row in session.scalars(select(AuditEvent)).all()]
    assert "plugin_worker_crash" in events


def test_worker_disabled_plugin_is_not_started(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'worker3.db'}"
    init_db(db_url)
    loader = _write_worker_plugin(
        tmp_path,
        "worker_disabled",
        """
async def handle_worker(context, host_api):
    raise RuntimeError("should-not-run")
""",
    )
    sf = create_session_factory(db_url)
    with sf() as session:
        PluginRepository(session).sync_discovered(loader.discover().valid)

    async def _send(chat_id: int, text: str):
        return {"ok": True}

    async def _reply(chat_id: int, message_id: int, text: str):
        return {"ok": True}

    manager = WorkerPluginManager(loader=loader, session_factory=sf, send_message=_send, reply=_reply)
    assert asyncio.run(manager.start("worker_disabled")) is False

    with sf() as session:
        events = [row.event_type for row in session.scalars(select(AuditEvent)).all()]
    assert "plugin_worker_skipped" in events


def test_worker_sync_start_uses_persistent_background_loop(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'worker4.db'}"
    init_db(db_url)
    manager = _manager(
        tmp_path,
        db_url,
        "worker_sync",
        """
import asyncio
async def handle_worker(context, host_api):
    await asyncio.sleep(60)
""",
    )

    assert manager.start_sync("worker_sync", now=datetime(2030, 1, 1, tzinfo=timezone.utc)) is True
    assert manager.state("worker_sync") == "running"
    assert manager.stop_sync("worker_sync", now=datetime(2030, 1, 1, tzinfo=timezone.utc)) is True
    assert manager.state("worker_sync") == "stopped"
