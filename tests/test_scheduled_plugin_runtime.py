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
from amo_bot.plugins.sandbox.types import SandboxResponse
from amo_bot.main import run


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

    async def _send(chat_id: int, text: str, message_thread_id: int | None = None):
        sent.append((chat_id, text, message_thread_id))
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
    assert sent == [(123, "scheduled:schedule", None)]

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

    async def _send(chat_id: int, text: str, message_thread_id: int | None = None):
        sent.append((chat_id, text, message_thread_id))
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


def test_scheduled_executor_uses_relative_plugin_entry_for_sandbox(tmp_path, monkeypatch) -> None:
    db_url = f"sqlite:///{tmp_path / 'scheduled_rel_entry.db'}"
    init_db(db_url)
    loader = _write_scheduled_plugin(
        tmp_path,
        "scheduled_rel_entry",
        """
async def handle_schedule(context, host_api):
    await host_api.send_message(123, "ok")
""",
    )
    sf = create_session_factory(db_url)
    with sf() as session:
        repo = PluginRepository(session)
        repo.sync_discovered(loader.discover().valid)
        repo.activate("scheduled_rel_entry", actor_telegram_user_id=1)

    captured: dict[str, object] = {}

    class _FakeRunner:
        def __init__(self, *, plugins_dir: str, max_timeout_ms: int | None = None) -> None:
            captured["plugins_dir"] = plugins_dir
            captured["max_timeout_ms"] = max_timeout_ms

        def run(self, request):
            captured["request"] = request
            return SandboxResponse(ok=True, request_id=request.request_id, plugin_id=request.plugin_id, result={"ops": []})

    monkeypatch.setattr("amo_bot.plugins.scheduled_runtime.PluginSandboxRunner", _FakeRunner)

    async def _send(chat_id: int, text: str):
        return {"ok": True}

    async def _reply(chat_id: int, message_id: int, text: str):
        return {"ok": True}

    executor = ScheduledPluginExecutor(loader=loader, session_factory=sf, send_message=_send, reply=_reply)
    now = datetime(2030, 1, 1, tzinfo=timezone.utc)
    assert asyncio.run(executor.run_due_once(now=now)) == 1

    request = captured.get("request")
    assert request is not None
    payload = getattr(request, "payload", {})
    assert isinstance(payload, dict)
    assert payload.get("plugin_entry") == "scheduled_rel_entry/main.py"
    assert not str(payload.get("plugin_entry", "")).startswith("/")
    assert captured.get("max_timeout_ms") == 2000




def test_scheduled_plugin_logs_schedule_diagnostics_sanitized(tmp_path, monkeypatch, caplog) -> None:
    db_url = f"sqlite:///{tmp_path / 'scheduled_diag.db'}"
    init_db(db_url)
    loader = _write_scheduled_plugin(
        tmp_path,
        "scheduled_diag",
        """
async def handle_schedule(context, host_api):
    return {"schedule_diagnostics": []}
""",
    )
    sf = create_session_factory(db_url)
    with sf() as session:
        repo = PluginRepository(session)
        repo.sync_discovered(loader.discover().valid)
        repo.activate("scheduled_diag", actor_telegram_user_id=1)

    diagnostics = [
        {
            "event": "yt_rss_schedule_subscription",
            "subscriptions_count": 10,
            "checked_count": 3,
            "kind": "new_item",
            "fingerprint": "fp-1",
            "feed_id": "feed-1",
            "entry_id": "entry-1",
            "channel_key": "chan-1",
            "chat_id": "target-chat",
            "thread_id": "42",
            "success": False,
            "reason_code": "not_due",
            "error_category": "none",
            "item_count": 7,
            "new_item_count": 2,
            "posted_count": 0,
            "cursor_advanced": True,
            "title": "should-not-log",
            "url": "https://example.invalid",
            "text": "secret-text",
            "secret": "token",
        },
        "ignore-me",
        123,
    ]

    class _FakeRunner:
        def __init__(self, *, plugins_dir: str, max_timeout_ms: int | None = None) -> None:
            pass

        def run(self, request):
            return SandboxResponse(
                ok=True,
                request_id=request.request_id,
                result={"ops": [], "schedule_diagnostics": diagnostics},
            )

    monkeypatch.setattr("amo_bot.plugins.scheduled_runtime.PluginSandboxRunner", _FakeRunner)

    sent: list[tuple[int, str, int | None]] = []

    async def _send(chat_id: int, text: str, message_thread_id: int | None = None):
        sent.append((chat_id, text, message_thread_id))
        return {"ok": True}

    async def _reply(chat_id: int, message_id: int, text: str):
        return {"ok": True}

    caplog.set_level("INFO")

    executor = ScheduledPluginExecutor(loader=loader, session_factory=sf, send_message=_send, reply=_reply)
    now = datetime(2030, 1, 1, tzinfo=timezone.utc)
    assert asyncio.run(executor.run_due_once(now=now)) == 1

    records = [r for r in caplog.records if r.msg == "plugin_schedule_diagnostic"]
    assert len(records) == 1

    payload = records[0].__dict__.copy()
    assert payload["plugin_name"] == "scheduled_diag"
    assert payload["event"] == "yt_rss_schedule_subscription"
    assert payload["subscriptions_count"] == 10
    assert payload["checked_count"] == 3
    assert payload["channel_key"] == "chan-1"
    assert payload["chat_id"] == "target-chat"
    assert payload["thread_id"] == "42"
    assert payload["success"] is False
    assert payload["reason_code"] == "not_due"
    assert payload["error_category"] == "none"
    assert payload["item_count"] == 7
    assert payload["new_item_count"] == 2
    assert payload["posted_count"] == 0
    assert payload["cursor_advanced"] is True

    allowed = {
        "event",
        "subscriptions_count",
        "checked_count",
        "chat_id",
        "thread_id",
        "channel_key",
        "success",
        "reason_code",
        "error_category",
        "item_count",
        "new_item_count",
        "posted_count",
        "cursor_advanced",
    }
    for key in allowed:
        assert key in payload

    for disallowed_key in (
        "kind",
        "fingerprint",
        "feed_id",
        "entry_id",
        "title",
        "url",
        "text",
        "secret",
    ):
        assert disallowed_key not in payload

    assert sent == []

def test_main_wires_scheduled_plugin_executor_timeout(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _StopRun(Exception):
        pass

    class _FakeSettings:
        database_url = "sqlite:///:memory:"
        webui_owner_telegram_id = 1
        bot_token = "token"
        telegram_api_base = "https://api.telegram.org"
        bot_username = "bot"
        offset_state_file = "offset.json"
        ai_provider = "noop"
        ollama_base_url = "http://localhost:11434"
        ollama_model = "dummy"
        amo_plugin_dir = "/tmp/plugins"
        poll_timeout_seconds = 5
        poll_limit = 10
        poll_retry_max_seconds = 30
        webui_host = "127.0.0.1"
        webui_port = 8080

    class _DummyContext:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, tb):
            return False

    def _fake_session_factory():
        return _DummyContext()

    class _FakeScheduledPluginExecutor:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def run_due_once(self, now=None):
            return 0

    async def _fake_run_polling(*args, **kwargs):
        raise _StopRun

    monkeypatch.setattr("amo_bot.main.get_settings", lambda: _FakeSettings())
    monkeypatch.setattr("amo_bot.main.setup_logging", lambda: None)
    monkeypatch.setattr("amo_bot.main.init_db", lambda _db_url: None)
    monkeypatch.setattr("amo_bot.main.create_session_factory", lambda _db_url: _fake_session_factory)
    monkeypatch.setattr("amo_bot.main.UserRoleRepository", lambda _session: type("_Repo", (), {"bootstrap_owner_from_settings": lambda self, **kwargs: None})())
    monkeypatch.setattr("amo_bot.main.TelegramClient", lambda **kwargs: object())
    monkeypatch.setattr("amo_bot.main.OffsetStore", lambda _path: object())
    monkeypatch.setattr("amo_bot.main.DBRoleResolver", lambda _sf: object())
    monkeypatch.setattr("amo_bot.main.build_ai_provider", lambda _settings: object())
    monkeypatch.setattr("amo_bot.main.OwnerNotifier", lambda **kwargs: object())
    monkeypatch.setattr("amo_bot.main.create_builtin_registry", lambda **kwargs: object())
    monkeypatch.setattr("amo_bot.main.PluginLoader", lambda _dir: object())
    monkeypatch.setattr("amo_bot.main.PluginCommandExecutor", lambda **kwargs: object())
    monkeypatch.setattr("amo_bot.main.ScheduledPluginExecutor", _FakeScheduledPluginExecutor)
    monkeypatch.setattr("amo_bot.main.ChatTopicPersistenceService", lambda *args, **kwargs: object())
    monkeypatch.setattr("amo_bot.main.Dispatcher", lambda **kwargs: object())
    monkeypatch.setattr("amo_bot.main.run_polling", _fake_run_polling)

    try:
        run([])
    except _StopRun:
        pass

    assert captured.get("timeout_seconds") == 30.0


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
