from __future__ import annotations

import asyncio

from amo_bot.ai.router import AIRouter
from amo_bot.auth.roles import Role
from amo_bot.db.base import create_session_factory
from amo_bot.db.init_db import init_db
from amo_bot.db.models import AuditEvent
from amo_bot.db.repositories import PromptContextDocRepository, TopicAgentMemoryRepository
from amo_bot.telegram.commands import CommandContext, create_builtin_registry
from amo_bot.telegram.dispatcher import Dispatcher
from amo_bot.telegram.role_resolver import InMemoryRoleResolver


def _db_url(tmp_path, name: str) -> str:
    return f"sqlite:///{tmp_path / name}"


def _ctx(
    *,
    command_name: str,
    argument: str | None,
    role: Role = Role.ADMIN,
    chat_id: int = -1001,
    user_id: int = 42,
    message_thread_id: int | None = None,
    reply_to_message_text: str = "",
) -> CommandContext:
    return CommandContext(
        chat_id=chat_id,
        user_id=user_id,
        role=role,
        command_name=command_name,
        argument=argument,
        message_thread_id=message_thread_id,
        reply_to_message_text=reply_to_message_text,
    )


def _cmd(db_url: str, name: str):
    cmd = create_builtin_registry(database_url=db_url).get(name)
    assert cmd is not None
    return cmd


def test_authorized_set_get_list_delete_happy_path(tmp_path) -> None:
    db_url = _db_url(tmp_path, "ctxdoc_cmds_happy.sqlite")
    init_db(db_url)

    set_out = asyncio.run(_cmd(db_url, "ctxdoc_set").handler(_ctx(command_name="ctxdoc_set", argument="AGENT global be precise")))
    get_out = asyncio.run(_cmd(db_url, "ctxdoc_get").handler(_ctx(command_name="ctxdoc_get", argument="agent global")))
    list_out = asyncio.run(_cmd(db_url, "ctxdoc_list").handler(_ctx(command_name="ctxdoc_list", argument=None)))
    del_out = asyncio.run(_cmd(db_url, "ctxdoc_del").handler(_ctx(command_name="ctxdoc_del", argument="AGENT global")))
    get_after = asyncio.run(_cmd(db_url, "ctxdoc_get").handler(_ctx(command_name="ctxdoc_get", argument="AGENT global")))

    assert set_out == "ctxdoc saved: AGENT global (10 chars)"
    assert get_out == "AGENT global (enabled, 10 chars):\nbe precise"
    assert "AGENT global enabled chars=10" in str(list_out)
    assert "be precise" not in str(list_out)
    assert del_out == "ctxdoc deleted: AGENT global"
    assert get_after == "ctxdoc not found: AGENT global"


def test_unauthorized_cannot_set_get_list_delete_via_dispatcher(tmp_path) -> None:
    db_url = _db_url(tmp_path, "ctxdoc_cmds_unauth.sqlite")
    init_db(db_url)
    sent: list[tuple[int, str, int | None]] = []

    async def _send(chat_id: int, text: str, message_thread_id: int | None = None) -> object:
        sent.append((chat_id, text, message_thread_id))
        return {"ok": True}

    dispatcher = Dispatcher(
        command_registry=create_builtin_registry(database_url=db_url),
        role_resolver=InMemoryRoleResolver({55: Role.NORMAL}),
        send_text=_send,
        bot_username="amo_bot",
        database_url=db_url,
    )

    for idx, text in enumerate((
        "/ctxdoc_set AGENT global secret",
        "/ctxdoc_get AGENT global",
        "/ctxdoc_list",
        "/ctxdoc_del AGENT global",
    ), start=1):
        asyncio.run(dispatcher.handle_raw_update({
            "update_id": idx,
            "message": {
                "message_id": idx,
                "from": {"id": 55, "is_bot": False, "first_name": "N"},
                "chat": {"id": -1001, "type": "supergroup", "title": "G"},
                "text": text,
            },
        }))

    assert sent == []
    with create_session_factory(db_url)() as session:
        assert PromptContextDocRepository(session).list_docs() == []


def test_invalid_kind_and_scope_rejected(tmp_path) -> None:
    db_url = _db_url(tmp_path, "ctxdoc_cmds_invalid.sqlite")
    init_db(db_url)

    bad_kind = asyncio.run(_cmd(db_url, "ctxdoc_set").handler(_ctx(command_name="ctxdoc_set", argument="BAD global x")))
    bad_scope = asyncio.run(_cmd(db_url, "ctxdoc_get").handler(_ctx(command_name="ctxdoc_get", argument="AGENT private")))

    assert bad_kind == "invalid kind. allowed: AGENT, SOUL, PLUGINS, AUFGABE"
    assert bad_scope == "invalid scope. allowed: global, topic"


def test_topic_scope_requires_topic_context_and_uses_current_topic_only(tmp_path) -> None:
    db_url = _db_url(tmp_path, "ctxdoc_cmds_topic.sqlite")
    init_db(db_url)

    no_topic = asyncio.run(_cmd(db_url, "ctxdoc_set").handler(_ctx(command_name="ctxdoc_set", argument="AUFGABE topic t")))
    topic_ctx = _ctx(command_name="ctxdoc_set", argument="AUFGABE topic topic-11", message_thread_id=11)
    saved = asyncio.run(_cmd(db_url, "ctxdoc_set").handler(topic_ctx))

    with create_session_factory(db_url)() as session:
        docs = PromptContextDocRepository(session)
        assert docs.get_doc(kind="AUFGABE", scope_type="topic", chat_id=-1001, topic_id=11).content == "topic-11"  # type: ignore[union-attr]
        assert docs.get_doc(kind="AUFGABE", scope_type="topic", chat_id=-1001, topic_id=22) is None

    assert no_topic == "topic scope requires running the command inside a Telegram topic"
    assert saved == "ctxdoc saved: AUFGABE topic (8 chars)"


def test_long_content_limit_rejected_and_audited_without_content(tmp_path) -> None:
    db_url = _db_url(tmp_path, "ctxdoc_cmds_long.sqlite")
    init_db(db_url)
    long_content = "x" * 6001

    out = asyncio.run(_cmd(db_url, "ctxdoc_set").handler(_ctx(command_name="ctxdoc_set", argument=f"SOUL global {long_content}")))

    assert out == "content too long: 6001 chars (max 6000)"
    with create_session_factory(db_url)() as session:
        assert PromptContextDocRepository(session).get_doc(kind="SOUL", scope_type="global") is None
        event = session.query(AuditEvent).filter(AuditEvent.event_type == "prompt_context_doc_set").one()
        assert "6001" in event.payload_json
        assert long_content not in event.payload_json


def test_list_does_not_leak_content_and_reply_text_supported(tmp_path) -> None:
    db_url = _db_url(tmp_path, "ctxdoc_cmds_reply_list.sqlite")
    init_db(db_url)
    secret = "SECRET_CONTEXT_BODY"

    out = asyncio.run(_cmd(db_url, "ctxdoc_set").handler(_ctx(command_name="ctxdoc_set", argument="PLUGINS global", reply_to_message_text=secret)))
    list_out = asyncio.run(_cmd(db_url, "ctxdoc_list").handler(_ctx(command_name="ctxdoc_list", argument="global PLUGINS")))

    assert out == "ctxdoc saved: PLUGINS global (19 chars)"
    assert "PLUGINS global enabled chars=19" in str(list_out)
    assert secret not in str(list_out)


def test_delete_removes_content_and_router_no_longer_includes_it(tmp_path) -> None:
    db_url = _db_url(tmp_path, "ctxdoc_cmds_delete_router.sqlite")
    init_db(db_url)
    with create_session_factory(db_url)() as session:
        TopicAgentMemoryRepository(session).upsert_config(scope_type="topic", chat_id=-1001, topic_id=11, ai_enabled=True)

    asyncio.run(_cmd(db_url, "ctxdoc_set").handler(_ctx(command_name="ctxdoc_set", argument="AGENT global secret-agent")))
    with create_session_factory(db_url)() as session:
        text = AIRouter(
            topic_agent_memory_repository=TopicAgentMemoryRepository(session),
            prompt_context_doc_repository=PromptContextDocRepository(session),
        ).decide(prompt="hi @amo_bot", chat_id=-1001, topic_id=11, user_id=5, bot_username="amo_bot").context.prompt_context_docs_text
        assert "secret-agent" in text

    asyncio.run(_cmd(db_url, "ctxdoc_del").handler(_ctx(command_name="ctxdoc_del", argument="AGENT global")))
    with create_session_factory(db_url)() as session:
        assert PromptContextDocRepository(session).get_doc(kind="AGENT", scope_type="global") is None
        text = AIRouter(
            topic_agent_memory_repository=TopicAgentMemoryRepository(session),
            prompt_context_doc_repository=PromptContextDocRepository(session),
        ).decide(prompt="hi @amo_bot", chat_id=-1001, topic_id=11, user_id=5, bot_username="amo_bot").context.prompt_context_docs_text
        assert "secret-agent" not in text
