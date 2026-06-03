from __future__ import annotations

import asyncio

from amo_bot.auth.roles import Role
from amo_bot.db.base import create_session_factory
from amo_bot.db.init_db import init_db
from amo_bot.db.models import PromptContextDoc
from amo_bot.db.repositories import PromptContextDocRepository, TopicAgentMemoryRepository, UserRoleRepository
from amo_bot.telegram.commands import create_builtin_registry
from amo_bot.telegram.dispatcher import Dispatcher
from amo_bot.telegram.role_resolver import InMemoryRoleResolver


def _db_url(tmp_path, name: str) -> str:
    return f"sqlite:///{tmp_path / name}"


def test_prompt_context_doc_no_docs_noop_for_router(tmp_path) -> None:
    from amo_bot.ai.router import AIRouter

    db_url = _db_url(tmp_path, "prompt_docs_noop.sqlite")
    init_db(db_url)
    with create_session_factory(db_url)() as session:
        mem_repo = TopicAgentMemoryRepository(session)
        mem_repo.upsert_config(scope_type="topic", chat_id=-1001, topic_id=11, ai_enabled=True)
        decision = AIRouter(
            topic_agent_memory_repository=mem_repo,
            prompt_context_doc_repository=PromptContextDocRepository(session),
        ).decide(
            prompt="hi @amo_bot",
            chat_id=-1001,
            topic_id=11,
            user_id=5,
            bot_username="amo_bot",
        )

    assert decision.context.prompt_context_docs_text == ""


def test_global_docs_included_in_deterministic_order(tmp_path) -> None:
    from amo_bot.ai.router import AIRouter

    db_url = _db_url(tmp_path, "prompt_docs_global.sqlite")
    init_db(db_url)
    with create_session_factory(db_url)() as session:
        docs = PromptContextDocRepository(session)
        docs.upsert_doc(kind="PLUGINS", scope_type="global", content="plugin text")
        docs.upsert_doc(kind="AGENT", scope_type="global", content="agent text")
        docs.upsert_doc(kind="AUFGABE", scope_type="global", content="aufgabe text")
        docs.upsert_doc(kind="SOUL", scope_type="global", content="soul text")
        mem_repo = TopicAgentMemoryRepository(session)
        mem_repo.upsert_config(scope_type="topic", chat_id=-1001, topic_id=11, ai_enabled=True)
        decision = AIRouter(topic_agent_memory_repository=mem_repo, prompt_context_doc_repository=docs).decide(
            prompt="hi @amo_bot",
            chat_id=-1001,
            topic_id=11,
            user_id=5,
            bot_username="amo_bot",
        )

    text = decision.context.prompt_context_docs_text
    assert text.index("[AGENT") < text.index("[SOUL") < text.index("[PLUGINS") < text.index("[AUFGABE")
    assert "agent text" in text
    assert "soul text" in text
    assert "plugin text" in text
    assert "aufgabe text" in text


def test_topic_doc_overrides_matching_kind_for_matching_topic_only(tmp_path) -> None:
    from amo_bot.ai.router import AIRouter

    db_url = _db_url(tmp_path, "prompt_docs_topic.sqlite")
    init_db(db_url)
    with create_session_factory(db_url)() as session:
        docs = PromptContextDocRepository(session)
        docs.upsert_doc(kind="AUFGABE", scope_type="global", content="global task")
        docs.upsert_doc(kind="AUFGABE", scope_type="topic", chat_id=-1001, topic_id=11, content="topic task")
        mem_repo = TopicAgentMemoryRepository(session)
        mem_repo.upsert_config(scope_type="topic", chat_id=-1001, topic_id=11, ai_enabled=True)
        mem_repo.upsert_config(scope_type="topic", chat_id=-1001, topic_id=22, ai_enabled=True)
        router = AIRouter(topic_agent_memory_repository=mem_repo, prompt_context_doc_repository=docs)

        matching = router.decide(prompt="hi @amo_bot", chat_id=-1001, topic_id=11, user_id=5, bot_username="amo_bot")
        other = router.decide(prompt="hi @amo_bot", chat_id=-1001, topic_id=22, user_id=5, bot_username="amo_bot")

    assert "topic task" in matching.context.prompt_context_docs_text
    assert "global task" not in matching.context.prompt_context_docs_text
    assert "global task" in other.context.prompt_context_docs_text
    assert "topic task" not in other.context.prompt_context_docs_text


def test_disabled_docs_ignored(tmp_path) -> None:
    db_url = _db_url(tmp_path, "prompt_docs_disabled.sqlite")
    init_db(db_url)
    with create_session_factory(db_url)() as session:
        docs = PromptContextDocRepository(session)
        docs.upsert_doc(kind="AGENT", scope_type="global", content="enabled")
        docs.upsert_doc(kind="SOUL", scope_type="global", content="disabled", enabled=False)
        resolved = docs.resolve_docs(chat_id=-1001, topic_id=11)

    assert [doc.kind for doc in resolved] == ["AGENT"]
    assert "disabled" not in [doc.content for doc in resolved]


def test_prompt_context_doc_char_caps_and_metadata(tmp_path) -> None:
    from amo_bot.ai.router import AIRouter

    db_url = _db_url(tmp_path, "prompt_docs_caps.sqlite")
    init_db(db_url)
    with create_session_factory(db_url)() as session:
        docs = PromptContextDocRepository(session)
        docs.upsert_doc(kind="AGENT", scope_type="global", content="a" * 3000)
        docs.upsert_doc(kind="SOUL", scope_type="global", content="b" * 3000)
        docs.upsert_doc(kind="PLUGINS", scope_type="global", content="c" * 3000)
        docs.upsert_doc(kind="AUFGABE", scope_type="global", content="d" * 3000)
        mem_repo = TopicAgentMemoryRepository(session)
        mem_repo.upsert_config(scope_type="topic", chat_id=-1001, topic_id=11, ai_enabled=True)
        text = AIRouter(topic_agent_memory_repository=mem_repo, prompt_context_doc_repository=docs).decide(
            prompt="hi @amo_bot", chat_id=-1001, topic_id=11, user_id=5, bot_username="amo_bot"
        ).context.prompt_context_docs_text

    assert len(text) <= 6100
    assert "chars=2000" in text
    assert "truncated=true" in text
    assert "prompt_context_docs truncated" in text


def test_dispatcher_prompt_keeps_current_user_message_primary_with_docs(tmp_path) -> None:
    db_url = _db_url(tmp_path, "prompt_docs_dispatcher.sqlite")
    init_db(db_url)
    with create_session_factory(db_url)() as session:
        UserRoleRepository(session).set_user_role(
            actor_telegram_user_id=42,
            target_telegram_user_id=42,
            role=Role.OWNER,
        )
        TopicAgentMemoryRepository(session).upsert_config(scope_type="topic", chat_id=-1001, topic_id=11, ai_enabled=True)
        PromptContextDocRepository(session).upsert_doc(kind="AGENT", scope_type="global", content="be concise")

    prompts: list[str] = []
    sent: list[tuple[int, str, int | None]] = []

    class _FakeAI:
        async def ask(self, prompt: str) -> str:
            prompts.append(prompt)
            return "ok"

    async def _send(chat_id: int, text: str, message_thread_id: int | None = None) -> object:
        sent.append((chat_id, text, message_thread_id))
        return {"ok": True}

    dispatcher = Dispatcher(
        command_registry=create_builtin_registry(),
        role_resolver=InMemoryRoleResolver({42: Role.OWNER}),
        send_text=_send,
        bot_username="amo_bot",
        database_url=db_url,
        ai_service=_FakeAI(),
    )
    update = {
        "update_id": 1,
        "message": {
            "message_id": 10,
            "date": 1,
            "chat": {"id": -1001, "type": "supergroup", "title": "Group"},
            "message_thread_id": 11,
            "from": {"id": 42, "is_bot": False, "first_name": "Owner"},
            "text": "@amo_bot please answer this current request",
        },
    }

    asyncio.run(dispatcher.handle_raw_update(update))

    assert sent == [(-1001, "ok", 11)]
    assert len(prompts) == 1
    prompt = prompts[0]
    assert prompt.index("Current message:") < prompt.index("Assistant context notes")
    assert "do not quote or describe these notes" in prompt
    assert prompt.rstrip().endswith("User message:\nplease answer this current request")


def test_prompt_context_docs_table_created_by_init_db(tmp_path) -> None:
    db_url = _db_url(tmp_path, "prompt_docs_table.sqlite")
    init_db(db_url)
    with create_session_factory(db_url)() as session:
        assert session.query(PromptContextDoc).count() == 0
