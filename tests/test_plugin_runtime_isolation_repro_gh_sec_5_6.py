from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest

from amo_bot.db.base import create_session_factory
from amo_bot.db.init_db import init_db
from amo_bot.db.repositories import PluginRepository
from amo_bot.plugins.command_runtime import CommandActor, CommandInvocation, PluginCommandExecutor
from amo_bot.plugins.loader import PluginLoader
from amo_bot.plugins.sandbox.types import SandboxRequest
from amo_bot.plugins.scheduled_runtime import ScheduledPluginExecutor
from amo_bot.plugins.worker_runtime import WorkerPluginManager
from amo_bot.config.settings import Settings


def _write_plugin(tmp_path, name: str, manifest: dict, main_py: str) -> PluginLoader:
    plugins_dir = tmp_path / "plugins"
    plugin_dir = plugins_dir / name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (plugin_dir / "main.py").write_text(main_py, encoding="utf-8")
    return PluginLoader(str(plugins_dir))


def _mk_settings(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'repro.db'}"
    settings = Settings.model_validate(
        {
            "DATABASE_URL": db_url,
            "OWNER_TELEGRAM_USER_ID": "42",
            "BOT_TOKEN": "123456:TESTTOKEN",
            "WEBUI_PASSWORD": "test-password",
            "WEBUI_SECRET_KEY": "test-secret-key",
        }
    )
    init_db(settings.database_url)
    session_factory = create_session_factory(settings.database_url)
    return settings, session_factory


@pytest.mark.xfail(reason="GH-SEC-5/6: command runtime should no longer use legacy host-process import/execute path")
def test_repro_command_legacy_branch_executes_in_host_process_when_sandbox_disabled(tmp_path) -> None:
    _settings, session_factory = _mk_settings(tmp_path)
    loader = _write_plugin(
        tmp_path,
        "legacy_host_exec",
        {
            "name": "legacy_host_exec",
            "version": "1.0.0",
            "required_permissions": ["send_message"],
            "commands": ["legacy"],
        },
        """
HOST_EXECUTED = False

async def handle_command(context, host_api):
    global HOST_EXECUTED
    HOST_EXECUTED = True
""",
    )

    sent: list[tuple[int, str]] = []
    host_call_count = 0

    async def _send(chat_id: int, text: str) -> None:
        sent.append((chat_id, text))

    async def _reply(chat_id: int, message_id: int, text: str, message_thread_id: int | None = None) -> None:
        return None

    executor = PluginCommandExecutor(
        loader=loader,
        session_factory=session_factory,
        send_message=_send,
        reply=_reply,
        command_sandbox_enabled=False,
    )

    original_load_handler = executor._load_handler  # type: ignore[attr-defined]

    def _wrapped_load_handler(manifest):
        nonlocal host_call_count
        host_call_count += 1
        return original_load_handler(manifest)

    executor._load_handler = _wrapped_load_handler  # type: ignore[method-assign]

    asyncio.run(
        executor.execute(
            actor=CommandActor(telegram_user_id=42, role="owner"),
            invocation=CommandInvocation(
                chat_id=100,
                message_id=1,
                message_thread_id=None,
                command_name="legacy",
                argument="",
            ),
        )
    )

    assert host_call_count == 1
    assert sent == []


def test_repro_worker_runtime_should_execute_via_sandbox_not_host_import(tmp_path, monkeypatch) -> None:
    settings, session_factory = _mk_settings(tmp_path)
    loader = _write_plugin(
        tmp_path,
        "worker_isolation",
        {
            "name": "worker_isolation",
            "version": "1.0.0",
            "required_permissions": [],
            "worker": {"restart_backoff_seconds": 5},
        },
        """
HOST_WORKER_EXECUTED = False

async def handle_worker(context, host_api):
    global HOST_WORKER_EXECUTED
    HOST_WORKER_EXECUTED = True
""",
    )

    calls: list[str] = []

    def _fake_run(self, request: SandboxRequest):
        calls.append(request.action)
        return {"ok": True, "result": {"ops": []}}

    monkeypatch.setattr("amo_bot.plugins.sandbox.runner.PluginSandboxRunner.run", _fake_run)

    async def _send(chat_id: int, text: str) -> None:
        return None

    async def _reply(chat_id: int, message_id: int, text: str, message_thread_id: int | None = None) -> None:
        return None

    manager = WorkerPluginManager(
        loader=loader,
        session_factory=session_factory,
        send_message=_send,
        reply=_reply,
    )

    with session_factory() as session:
        PluginRepository(session).sync_discovered(loader.discover().valid)
        PluginRepository(session).activate("worker_isolation", actor_telegram_user_id=42)

    assert manager.start_sync("worker_isolation", now=datetime(2030, 1, 1, tzinfo=timezone.utc)) is True
    assert manager.stop_sync("worker_isolation", now=datetime(2030, 1, 1, tzinfo=timezone.utc)) in (True, False)

    assert calls, "expected sandbox runner call once worker isolation is implemented"


@pytest.mark.xfail(reason="GH-SEC-5/6: scheduled runtime not yet routed through real sandbox plugin execution")
def test_repro_scheduled_runtime_should_execute_via_sandbox_not_host_import(tmp_path, monkeypatch) -> None:
    settings, session_factory = _mk_settings(tmp_path)
    loader = _write_plugin(
        tmp_path,
        "scheduled_isolation",
        {
            "name": "scheduled_isolation",
            "version": "1.0.0",
            "required_permissions": [],
            "schedule": {"interval_seconds": 60},
        },
        """
HOST_SCHEDULE_EXECUTED = False

async def handle_schedule(context, host_api):
    global HOST_SCHEDULE_EXECUTED
    HOST_SCHEDULE_EXECUTED = True
""",
    )

    calls: list[str] = []

    def _fake_run(self, request: SandboxRequest):
        calls.append(request.action)
        return {"ok": True, "result": {"ops": []}}

    monkeypatch.setattr("amo_bot.plugins.sandbox.runner.PluginSandboxRunner.run", _fake_run)

    async def _send(chat_id: int, text: str) -> None:
        return None

    async def _reply(chat_id: int, message_id: int, text: str, message_thread_id: int | None = None) -> None:
        return None

    executor = ScheduledPluginExecutor(
        loader=loader,
        session_factory=session_factory,
        send_message=_send,
        reply=_reply,
    )

    with session_factory() as session:
        PluginRepository(session).activate("scheduled_isolation", actor_telegram_user_id=42)

    asyncio.run(executor.run_due_once(now=datetime(2030, 1, 1, tzinfo=timezone.utc)))

    assert calls, "expected sandbox runner call once scheduled isolation is implemented"


@pytest.mark.xfail(reason="GH-SEC-6: generic sandbox worker currently echo/stub; should execute real plugin behavior")
def test_repro_generic_sandbox_worker_is_stub_non_execution() -> None:
    request = SandboxRequest.from_dict(
        {
            "request_id": "r1",
            "action": "run",
            "plugin_id": "demo",
            "payload": {
                "plugin_entry": "demo/main.py",
                "trigger": "worker",
            },
            "capability": "plugin.runtime.worker.execute",
        }
    )

    from amo_bot.plugins.sandbox.worker import _sanitize_payload

    payload = _sanitize_payload(request.payload)
    assert payload.get("executed") is True
