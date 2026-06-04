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
from amo_bot.plugins.sandbox.types import SandboxErrorCode, SandboxRequest, SandboxResponse, SandboxRunnerError
from amo_bot.plugins.worker_runtime import WorkerPluginManager


def _write_worker_plugin(
    tmp_path,
    name: str,
    code: str,
    *,
    backoff_seconds: int = 30,
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
                "worker": {"restart_backoff_seconds": backoff_seconds},
                "required_roles": ["admin"],
                "required_permissions": ["send_message"] if required_permissions is None else required_permissions,
            }
        ),
        encoding="utf-8",
    )
    (plugin_dir / "main.py").write_text(code, encoding="utf-8")
    return PluginLoader(str(plugins_dir))


def _manager(
    tmp_path,
    db_url: str,
    plugin_name: str,
    code: str,
    *,
    required_permissions: list[str] | None = None,
) -> WorkerPluginManager:
    loader = _write_worker_plugin(tmp_path, plugin_name, code, required_permissions=required_permissions)
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


async def _wait_for_state(manager: WorkerPluginManager, plugin_name: str, expected_state: str) -> None:
    for _ in range(20):
        await asyncio.sleep(0.1)
        if manager.state(plugin_name) == expected_state:
            return
    raise AssertionError(f"expected {plugin_name} to reach {expected_state}, got {manager.state(plugin_name)}")


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
        await _wait_for_state(manager, "worker_crash", "crashed")

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


def test_worker_missing_capability_errors_and_audits(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'worker5.db'}"
    init_db(db_url)
    manager = _manager(
        tmp_path,
        db_url,
        "worker_no_cap",
        """
async def handle_worker(context, host_api):
    await host_api.send_message(123, "blocked")
""",
        required_permissions=[],
    )

    async def _run() -> None:
        assert await manager.start("worker_no_cap", now=datetime(2030, 1, 1, tzinfo=timezone.utc)) is True
        await _wait_for_state(manager, "worker_no_cap", "crashed")

    asyncio.run(_run())

    sf = create_session_factory(db_url)
    with sf() as session:
        plugin = session.scalar(select(Plugin).where(Plugin.name == "worker_no_cap"))
        assert plugin is not None
        assert plugin.worker_state == "crashed"
        events = session.scalars(select(AuditEvent)).all()
        crashes = [row for row in events if row.event_type == "plugin_worker_crash"]
    assert crashes
    assert "requires capability 'send_message'" in (crashes[-1].payload_json or "")


def test_worker_sandbox_timeout_keeps_worker_running(tmp_path, monkeypatch) -> None:
    db_url = f"sqlite:///{tmp_path / 'worker_timeout.db'}"
    init_db(db_url)
    manager = _manager(
        tmp_path,
        db_url,
        "worker_timeout",
        """
async def handle_worker(context, host_api):
    return None
""",
    )

    calls = 0

    def _fake_run(self, request: SandboxRequest) -> SandboxResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise SandboxRunnerError(SandboxErrorCode.WORKER_TIMEOUT, "worker_timeout")
        return SandboxResponse(request_id=request.request_id, ok=True, result={"ops": []})

    monkeypatch.setattr("amo_bot.plugins.sandbox.runner.PluginSandboxRunner.run", _fake_run)

    async def _run() -> None:
        assert await manager.start("worker_timeout", now=datetime(2030, 1, 1, tzinfo=timezone.utc)) is True
        await _wait_for_state(manager, "worker_timeout", "stopped")

    asyncio.run(_run())

    assert calls == 2


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


def test_worker_streaming_send_message_is_applied_before_timeout(tmp_path, monkeypatch) -> None:
    db_url = f"sqlite:///{tmp_path / 'worker_streaming.db'}"
    init_db(db_url)
    loader = _write_worker_plugin(
        tmp_path,
        "worker_streaming",
        """
async def handle_worker(context, host_api):
    return None
""",
    )
    sf = create_session_factory(db_url)
    with sf() as session:
        repo = PluginRepository(session)
        repo.sync_discovered(loader.discover().valid)
        repo.activate("worker_streaming", actor_telegram_user_id=1)

    sent: list[tuple[int, str, int | None]] = []

    async def _send(chat_id: int, text: str, message_thread_id: int | None = None):
        sent.append((chat_id, text, message_thread_id))
        return {"ok": True}

    async def _reply(chat_id: int, message_id: int, text: str, message_thread_id: int | None = None):
        return {"ok": True}

    calls = 0

    def _fake_run(self, request: SandboxRequest, stream_event_handler=None) -> SandboxResponse:
        nonlocal calls
        calls += 1
        if stream_event_handler is not None:
            stream_event_handler(
                {
                    "type": "op",
                    "op": {
                        "op": "send_message",
                        "chat_id": 123,
                        "text": "streamed",
                        "message_thread_id": 456,
                    },
                }
            )
        raise SandboxRunnerError(SandboxErrorCode.WORKER_TIMEOUT, "worker_timeout")

    monkeypatch.setattr("amo_bot.plugins.worker_runtime.PluginSandboxRunner.run", _fake_run)

    manager = WorkerPluginManager(loader=loader, session_factory=sf, send_message=_send, reply=_reply)

    async def _run() -> None:
        assert await manager.start("worker_streaming", now=datetime(2030, 1, 1, tzinfo=timezone.utc)) is True
        for _ in range(30):
            if sent:
                break
            await asyncio.sleep(0.1)
        assert sent[0] == (123, "streamed", 456)
        assert manager.state("worker_streaming") == "running"
        assert await manager.stop("worker_streaming", now=datetime(2030, 1, 1, tzinfo=timezone.utc)) is True

    asyncio.run(_run())
    assert calls >= 1
