from __future__ import annotations

import asyncio
import json

from sqlalchemy import select

from amo_bot.auth.roles import Role
from amo_bot.db.base import create_session_factory
from amo_bot.db.init_db import init_db
from amo_bot.db.models import AuditEvent, PluginPolicyAllowedGroup, PluginPolicyAllowedTopic, PluginPolicyOverride
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
    commands: list[str] | None = None,
    command_sandbox_enabled: bool = False,
) -> tuple[PluginCommandExecutor, list[tuple[int, str]], list[tuple[int, int, str, int | None]]]:
    plugins_dir = tmp_path / "plugins"
    pdir = plugins_dir / plugin_name
    pdir.mkdir(parents=True)
    (pdir / "plugin.json").write_text(
        json.dumps(
            {
                "name": plugin_name,
                "version": "1.0.0",
                "description": "demo",
                "commands": ["plug"] if commands is None else commands,
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
    replied: list[tuple[int, int, str, int | None]] = []

    async def _send(chat_id: int, text: str):
        sent.append((chat_id, text))
        return {"ok": True}

    async def _reply(chat_id: int, message_id: int, text: str, message_thread_id: int | None = None):
        replied.append((chat_id, message_id, text, message_thread_id))
        return {"ok": True}

    executor = PluginCommandExecutor(
        loader=PluginLoader(str(plugins_dir)),
        session_factory=sf,
        send_message=_send,
        reply=_reply,
        timeout_seconds=0.05,
        command_sandbox_enabled=command_sandbox_enabled,
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
    assert replied == [(77, 9, "ack", None)]

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


def test_plugin_command_manifest_slash_command_matches_slashless_invocation(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'plugin_runtime6.db'}"
    init_db(db_url)
    executor, sent, replied = _mk_executor(
        tmp_path,
        db_url,
        "slash_demo",
        """
async def handle_command(context, host_api):
    await host_api.reply(context.chat_id, context.message_id, "slash-ok")
""",
        commands=["/pluginping"],
    )

    asyncio.run(
        executor.execute(
            actor=CommandActor(telegram_user_id=100, role=Role.ADMIN),
            invocation=CommandInvocation(command_name="pluginping", argument=None, chat_id=77, message_id=9),
        )
    )

    assert sent == []
    assert replied == [(77, 9, "slash-ok", None)]


def test_plugin_command_handler_uses_reply_or_send_contract(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'plugin_runtime7.db'}"
    init_db(db_url)

    reply_executor, sent_reply, replied_reply = _mk_executor(
        tmp_path,
        db_url,
        "contract_reply_demo",
        """
async def handle_command(context, host_api):
    await host_api.reply(context.chat_id, context.message_id, "reply-path")
""",
        commands=["pluginping"],
    )
    asyncio.run(
        reply_executor.execute(
            actor=CommandActor(telegram_user_id=100, role=Role.ADMIN),
            invocation=CommandInvocation(command_name="pluginping", argument=None, chat_id=77, message_id=9),
        )
    )

    send_executor, sent_send, replied_send = _mk_executor(
        tmp_path,
        db_url,
        "contract_send_demo",
        """
async def handle_command(context, host_api):
    await host_api.send_message(context.chat_id, "send-path")
""",
        commands=["pluginping", "plug"],
    )
    asyncio.run(
        send_executor.execute(
            actor=CommandActor(telegram_user_id=100, role=Role.ADMIN),
            invocation=CommandInvocation(command_name="plug", argument=None, chat_id=77, message_id=9),
        )
    )

    assert replied_reply == [(77, 9, "reply-path", None)]
    assert sent_reply == []
    assert sent_send == [(77, "send-path")]
    assert replied_send == []


def test_plugin_command_default_path_uses_inprocess_handler(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'plugin_runtime_inprocess.db'}"
    init_db(db_url)

    executor, sent, replied = _mk_executor(
        tmp_path,
        db_url,
        "inprocess_demo",
        """
async def handle_command(context, host_api):
    await host_api.send_message(context.chat_id, "inprocess")
""",
        command_sandbox_enabled=False,
    )

    asyncio.run(
        executor.execute(
            actor=CommandActor(telegram_user_id=100, role=Role.ADMIN),
            invocation=CommandInvocation(command_name="plug", argument=None, chat_id=77, message_id=9),
        )
    )

    assert sent == [(77, "inprocess")]
    assert replied == []


def test_plugin_command_sandbox_enabled_routes_via_worker(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'plugin_runtime_sandbox.db'}"
    init_db(db_url)

    executor, sent, replied = _mk_executor(
        tmp_path,
        db_url,
        "sandbox_demo",
        """
async def handle_command(context, host_api):
    await host_api.send_message(context.chat_id, f"sandbox:{context.command_name}")
    await host_api.reply(context.chat_id, context.message_id, "sandbox-ack")
""",
        command_sandbox_enabled=True,
    )

    asyncio.run(
        executor.execute(
            actor=CommandActor(telegram_user_id=100, role=Role.ADMIN),
            invocation=CommandInvocation(command_name="plug", argument=None, chat_id=77, message_id=9),
        )
    )

    assert sent == [(77, "sandbox:plug")]
    assert replied == [(77, 9, "sandbox-ack", None)]


def test_plugin_command_sandbox_runtime_error_is_safely_mapped(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'plugin_runtime_sandbox_error.db'}"
    init_db(db_url)

    executor, sent, replied = _mk_executor(
        tmp_path,
        db_url,
        "sandbox_error_demo",
        """
async def handle_command(context, host_api):
    raise RuntimeError("Traceback: leaked details")
""",
        command_sandbox_enabled=True,
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
        rows = session.scalars(select(AuditEvent).where(AuditEvent.event_type == "plugin_command_error")).all()
    assert rows
    assert "command execution failed" in (rows[-1].payload_json or "")
    assert "Traceback" not in (rows[-1].payload_json or "")


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


def test_plugin_command_roles_override_replaces_manifest_roles(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'plugin_runtime_roles_override.db'}"
    init_db(db_url)
    executor, sent, _ = _mk_executor(
        tmp_path,
        db_url,
        "roles_override_demo",
        """
async def handle_command(context, host_api):
    await host_api.send_message(context.chat_id, "ok")
""",
    )

    sf = create_session_factory(db_url)
    with sf() as session:
        session.add(
            PluginPolicyOverride(
                plugin_name="roles_override_demo",
                roles_mode="override",
                required_roles_json='["normal"]',
            )
        )
        session.commit()

    asyncio.run(
        executor.execute(
            actor=CommandActor(telegram_user_id=100, role=Role.NORMAL),
            invocation=CommandInvocation(command_name="plug", argument=None, chat_id=77, message_id=9),
        )
    )

    assert sent == [(77, "ok")]


def test_plugin_command_private_deny_blocks_private_and_inherit_keeps_legacy(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'plugin_runtime_private_scope.db'}"
    init_db(db_url)
    executor, sent, _ = _mk_executor(
        tmp_path,
        db_url,
        "private_scope_demo",
        """
async def handle_command(context, host_api):
    await host_api.send_message(context.chat_id, "ok")
""",
    )

    sf = create_session_factory(db_url)
    with sf() as session:
        session.add(
            PluginPolicyOverride(
                plugin_name="private_scope_demo",
                private_mode="deny",
            )
        )
        session.commit()

    asyncio.run(
        executor.execute(
            actor=CommandActor(telegram_user_id=100, role=Role.ADMIN),
            invocation=CommandInvocation(command_name="plug", argument=None, chat_id=100, message_id=9),
        )
    )
    assert sent == []

    with sf() as session:
        row = session.scalar(select(PluginPolicyOverride).where(PluginPolicyOverride.plugin_name == "private_scope_demo"))
        assert row is not None
        row.private_mode = "inherit"
        session.commit()

    asyncio.run(
        executor.execute(
            actor=CommandActor(telegram_user_id=100, role=Role.ADMIN),
            invocation=CommandInvocation(command_name="plug", argument=None, chat_id=100, message_id=10),
        )
    )
    assert sent == [(100, "ok")]


def _denied_reason_payload(payload_json: str | None) -> str | None:
    if not payload_json:
        return None
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError:
        return None
    return payload.get("reason")


def test_plugin_command_group_allow_mode_empty_denies_all_then_allows_specific_group(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'plugin_runtime_group_scope.db'}"
    init_db(db_url)
    executor, sent, _ = _mk_executor(
        tmp_path,
        db_url,
        "group_scope_demo",
        """
async def handle_command(context, host_api):
    await host_api.send_message(context.chat_id, "ok")
""",
    )

    sf = create_session_factory(db_url)
    with sf() as session:
        override = PluginPolicyOverride(
            plugin_name="group_scope_demo",
            groups_mode="allow",
        )
        session.add(override)
        session.commit()
        override_id = override.id

    asyncio.run(
        executor.execute(
            actor=CommandActor(telegram_user_id=100, role=Role.ADMIN),
            invocation=CommandInvocation(command_name="plug", argument=None, chat_id=-111, message_id=9),
        )
    )
    assert sent == []

    with sf() as session:
        denied = session.scalars(select(AuditEvent).where(AuditEvent.event_type == "plugin_command_denied")).all()
    assert denied
    assert _denied_reason_payload(denied[-1].payload_json) == "group_not_allowed"

    with sf() as session:
        session.add(PluginPolicyAllowedGroup(override_id=override_id, chat_id=-111))
        session.commit()

    asyncio.run(
        executor.execute(
            actor=CommandActor(telegram_user_id=100, role=Role.ADMIN),
            invocation=CommandInvocation(command_name="plug", argument=None, chat_id=-111, message_id=10),
        )
    )
    assert sent == [(-111, "ok")]


def test_plugin_command_group_deny_mode_blocks_group_chat(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'plugin_runtime_group_deny_scope.db'}"
    init_db(db_url)
    executor, sent, _ = _mk_executor(
        tmp_path,
        db_url,
        "group_deny_scope_demo",
        """
async def handle_command(context, host_api):
    await host_api.send_message(context.chat_id, "ok")
""",
    )

    sf = create_session_factory(db_url)
    with sf() as session:
        session.add(
            PluginPolicyOverride(
                plugin_name="group_deny_scope_demo",
                groups_mode="deny",
            )
        )
        session.commit()

    asyncio.run(
        executor.execute(
            actor=CommandActor(telegram_user_id=100, role=Role.ADMIN),
            invocation=CommandInvocation(command_name="plug", argument=None, chat_id=-333, message_id=9),
        )
    )
    assert sent == []

    with sf() as session:
        denied = session.scalars(select(AuditEvent).where(AuditEvent.event_type == "plugin_command_denied")).all()
    assert denied
    assert _denied_reason_payload(denied[-1].payload_json) == "group_denied"


def test_plugin_command_topic_deny_mode_blocks_topic_messages_only(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'plugin_runtime_topic_deny_scope.db'}"
    init_db(db_url)
    executor, sent, _ = _mk_executor(
        tmp_path,
        db_url,
        "topic_deny_scope_demo",
        """
async def handle_command(context, host_api):
    await host_api.send_message(context.chat_id, "ok")
""",
    )

    sf = create_session_factory(db_url)
    with sf() as session:
        session.add(
            PluginPolicyOverride(
                plugin_name="topic_deny_scope_demo",
                topics_mode="deny",
            )
        )
        session.commit()

    asyncio.run(
        executor.execute(
            actor=CommandActor(telegram_user_id=100, role=Role.ADMIN),
            invocation=CommandInvocation(command_name="plug", argument=None, chat_id=-444, message_id=9, message_thread_id=1),
        )
    )

    asyncio.run(
        executor.execute(
            actor=CommandActor(telegram_user_id=100, role=Role.ADMIN),
            invocation=CommandInvocation(command_name="plug", argument=None, chat_id=-444, message_id=10, message_thread_id=None),
        )
    )

    assert sent == [(-444, "ok")]

    with sf() as session:
        denied = session.scalars(select(AuditEvent).where(AuditEvent.event_type == "plugin_command_denied")).all()
    assert denied
    assert _denied_reason_payload(denied[-1].payload_json) == "topic_denied"


def test_plugin_command_topic_allow_mode_empty_denies_all_then_allows_specific_topic(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'plugin_runtime_topic_scope.db'}"
    init_db(db_url)
    executor, sent, _ = _mk_executor(
        tmp_path,
        db_url,
        "topic_scope_demo",
        """
async def handle_command(context, host_api):
    await host_api.send_message(context.chat_id, "ok")
""",
    )

    sf = create_session_factory(db_url)
    with sf() as session:
        override = PluginPolicyOverride(
            plugin_name="topic_scope_demo",
            topics_mode="allow",
        )
        session.add(override)
        session.commit()
        override_id = override.id

    asyncio.run(
        executor.execute(
            actor=CommandActor(telegram_user_id=100, role=Role.ADMIN),
            invocation=CommandInvocation(command_name="plug", argument=None, chat_id=-222, message_id=9, message_thread_id=1),
        )
    )
    assert sent == []

    with sf() as session:
        denied = session.scalars(select(AuditEvent).where(AuditEvent.event_type == "plugin_command_denied")).all()
    assert denied
    assert _denied_reason_payload(denied[-1].payload_json) == "topic_not_allowed"

    with sf() as session:
        session.add(PluginPolicyAllowedTopic(override_id=override_id, chat_id=-222, message_thread_id=1))
        session.commit()

    asyncio.run(
        executor.execute(
            actor=CommandActor(telegram_user_id=100, role=Role.ADMIN),
            invocation=CommandInvocation(command_name="plug", argument=None, chat_id=-222, message_id=10, message_thread_id=1),
        )
    )
    assert sent == [(-222, "ok")]
