from __future__ import annotations

import asyncio
import json

from sqlalchemy import select

from amo_bot.auth.roles import Role
from amo_bot.db.base import create_session_factory
from amo_bot.db.init_db import init_db
from amo_bot.db.models import AuditEvent
from amo_bot.db.repositories import PluginRepository
from amo_bot.plugins.command_runtime import CommandActor, CommandInvocation, PluginCommandExecutor
from amo_bot.plugins.loader import PluginLoader


def _mk_executor(
    tmp_path,
    db_url: str,
    plugin_name: str,
    plugin_code: str,
    *,
    required_permissions: list[str] | None = None,
) -> tuple[PluginCommandExecutor, list[tuple[int, str]], list[tuple[int, int, str]]]:
    plugins_dir = tmp_path / "plugins"
    pdir = plugins_dir / plugin_name
    pdir.mkdir(parents=True)
    (pdir / "plugin.json").write_text(
        json.dumps(
            {
                "name": plugin_name,
                "version": "1.0.0",
                "description": "demo",
                "commands": ["plug"],
                "required_roles": ["admin", "owner"],
                "required_permissions": ["send_message"] if required_permissions is None else required_permissions,
            }
        ),
        encoding="utf-8",
    )
    (pdir / "main.py").write_text(plugin_code, encoding="utf-8")

    sf = create_session_factory(db_url)
    with sf() as session:
        repo = PluginRepository(session)
        repo.sync_discovered(PluginLoader(str(plugins_dir)).discover().valid)
        repo.activate(plugin_name, actor_telegram_user_id=1)

    sent: list[tuple[int, str]] = []
    replied: list[tuple[int, int, str]] = []

    async def _send(chat_id: int, text: str):
        sent.append((chat_id, text))
        return {"ok": True}

    async def _reply(chat_id: int, message_id: int, text: str):
        replied.append((chat_id, message_id, text))
        return {"ok": True}

    executor = PluginCommandExecutor(
        loader=PluginLoader(str(plugins_dir)),
        session_factory=sf,
        send_message=_send,
        reply=_reply,
        timeout_seconds=0.05,
    )
    return executor, sent, replied


def test_plugin_command_success_and_audit(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'plugin_runtime1.db'}"
    init_db(db_url)
    executor, sent, replied = _mk_executor(
        tmp_path,
        db_url,
        "demo",
        """
async def handle_command(context, host_api):
    await host_api.send_message(context.chat_id, f\"ok:{context.command_name}\")
    await host_api.reply(context.chat_id, context.message_id, \"ack\")
""",
    )

    asyncio.run(
        executor.execute(
            actor=CommandActor(telegram_user_id=100, role=Role.ADMIN),
            invocation=CommandInvocation(command_name="plug", argument="x", chat_id=77, message_id=9),
        )
    )

    assert sent == [(77, "ok:plug")]
    assert replied == [(77, 9, "ack")]

    sf = create_session_factory(db_url)
    with sf() as session:
        events = [row.event_type for row in session.scalars(select(AuditEvent)).all()]
    assert "plugin_command_start" in events
    assert "plugin_command_success" in events


def test_plugin_command_denied_by_role_and_ignore(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'plugin_runtime2.db'}"
    init_db(db_url)
    executor, sent, replied = _mk_executor(
        tmp_path,
        db_url,
        "demo",
        """
async def handle_command(context, host_api):
    await host_api.send_message(context.chat_id, \"should-not-send\")
""",
    )

    asyncio.run(
        executor.execute(
            actor=CommandActor(telegram_user_id=100, role=Role.NORMAL),
            invocation=CommandInvocation(command_name="plug", argument=None, chat_id=77, message_id=9),
        )
    )

    asyncio.run(
        executor.execute(
            actor=CommandActor(telegram_user_id=101, role=Role.IGNORE),
            invocation=CommandInvocation(command_name="plug", argument=None, chat_id=77, message_id=9),
        )
    )

    assert sent == []
    assert replied == []

    sf = create_session_factory(db_url)
    with sf() as session:
        events = [row.event_type for row in session.scalars(select(AuditEvent)).all()]
    assert events.count("plugin_command_denied") == 2


def test_plugin_command_missing_capability_errors_and_audits(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'plugin_runtime4.db'}"
    init_db(db_url)
    executor, sent, replied = _mk_executor(
        tmp_path,
        db_url,
        "no_send_perm",
        """
async def handle_command(context, host_api):
    await host_api.send_message(context.chat_id, "blocked")
""",
        required_permissions=[],
    )

    asyncio.run(
        executor.execute(
            actor=CommandActor(telegram_user_id=100, role=Role.ADMIN),
            invocation=CommandInvocation(command_name="plug", argument=None, chat_id=77, message_id=9),
        )
    )

    assert sent == []
    assert replied == []

    sf = create_session_factory(db_url)
    with sf() as session:
        rows = session.scalars(select(AuditEvent)).all()
        events = [row.event_type for row in rows]
        error_events = [row for row in rows if row.event_type == "plugin_command_error"]
    assert "plugin_command_error" in events
    assert error_events
    assert "requires capability 'send_message'" in (error_events[-1].payload_json or "")


def test_plugin_command_reply_missing_capability_errors_and_audits(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'plugin_runtime5.db'}"
    init_db(db_url)
    executor, sent, replied = _mk_executor(
        tmp_path,
        db_url,
        "no_reply_perm",
        """
async def handle_command(context, host_api):
    await host_api.reply(context.chat_id, context.message_id, "blocked")
""",
        required_permissions=[],
    )

    asyncio.run(
        executor.execute(
            actor=CommandActor(telegram_user_id=100, role=Role.ADMIN),
            invocation=CommandInvocation(command_name="plug", argument=None, chat_id=77, message_id=9),
        )
    )

    assert sent == []
    assert replied == []

    sf = create_session_factory(db_url)
    with sf() as session:
        rows = session.scalars(select(AuditEvent)).all()
        events = [row.event_type for row in rows]
        error_events = [row for row in rows if row.event_type == "plugin_command_error"]
    assert "plugin_command_error" in events
    assert error_events
    assert "operation 'reply' requires capability 'send_message'" in (error_events[-1].payload_json or "")


def test_plugin_command_timeout_and_error_are_isolated(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'plugin_runtime3.db'}"
    init_db(db_url)

    timeout_executor, sent1, _ = _mk_executor(
        tmp_path,
        db_url,
        "timeout_demo",
        """
import asyncio

async def handle_command(context, host_api):
    await asyncio.sleep(0.5)
""",
    )

    asyncio.run(
        timeout_executor.execute(
            actor=CommandActor(telegram_user_id=100, role=Role.ADMIN),
            invocation=CommandInvocation(command_name="plug", argument=None, chat_id=77, message_id=9),
        )
    )
    assert sent1 == []

    error_executor, sent2, _ = _mk_executor(
        tmp_path,
        db_url,
        "error_demo",
        """
async def handle_command(context, host_api):
    raise RuntimeError(\"boom\")
""",
    )

    asyncio.run(
        error_executor.execute(
            actor=CommandActor(telegram_user_id=100, role=Role.ADMIN),
            invocation=CommandInvocation(command_name="plug", argument=None, chat_id=77, message_id=9),
        )
    )
    assert sent2 == []

    sf = create_session_factory(db_url)
    with sf() as session:
        events = [row.event_type for row in session.scalars(select(AuditEvent)).all()]
    assert "plugin_command_timeout" in events
    assert "plugin_command_error" in events
