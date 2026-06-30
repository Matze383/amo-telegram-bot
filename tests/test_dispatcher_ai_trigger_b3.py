from __future__ import annotations

import asyncio
from dataclasses import dataclass

from amo_bot.auth.roles import Role
from amo_bot.telegram.dispatcher import Dispatcher


@dataclass
class _FakeAI:
    called: bool = False

    async def ask(self, prompt: str) -> str:
        self.called = True
        return "ok"


def test_ai_not_called_without_trigger_when_router_rejects(monkeypatch) -> None:
    ai = _FakeAI()

    dispatcher = Dispatcher(
        command_registry=None,  # type: ignore[arg-type]
        role_resolver=None,  # type: ignore[arg-type]
        send_text=None,  # type: ignore[arg-type]
        ai_service=ai,
        database_url="sqlite:///ignored.db",
    )

    class _Decision:
        class _Context:
            scope_type = "group"
            flag_bot_mention = False
            flag_reply_to_bot = False
            recent_messages_text = None
            assembled_soul_text = None
            daily_memory_text = None
            long_memory_text = None
            recall_memory_text = None
            user_profile_context_text = None

        reason_code = type("RC", (), {"value": "scope_disabled"})
        context = _Context()

    class _Router:
        def __init__(self, **kwargs: object) -> None:
            pass

        def decide(self, **kwargs: object) -> _Decision:
            return _Decision()

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("amo_bot.telegram.dispatcher.AIRouter", _Router)
    monkeypatch.setattr("amo_bot.telegram.dispatcher.create_session_factory", lambda *_: (lambda: _Session()))

    message = type("Msg", (), {})()
    message.text = "hello there"
    message.chat = type("Chat", (), {"id": -100, "type": "group"})
    message.message_thread_id = None
    message.from_user = type("User", (), {"id": 1})
    message.reply_to_is_bot = False
    message.reply_to_user_is_bot = False
    message.reply_to_username = None
    message.message_id = 10

    asyncio.run(dispatcher._maybe_handle_ai_autoreply(message=message, role=Role.OWNER, bot_username="amo_bot"))

    assert ai.called is False


def test_autoreply_repositories_receive_context_memory_vector_recall(monkeypatch) -> None:
    ai = _FakeAI()
    vector_recall = object()
    captured: dict[str, object] = {}

    dispatcher = Dispatcher(
        command_registry=None,  # type: ignore[arg-type]
        role_resolver=None,  # type: ignore[arg-type]
        send_text=None,  # type: ignore[arg-type]
        ai_service=ai,
        database_url="sqlite:///ignored.db",
        context_memory_vector_recall=vector_recall,  # type: ignore[arg-type]
    )

    class _Decision:
        class _Context:
            scope_type = "group"
            flag_bot_mention = False
            flag_reply_to_bot = False
            recent_messages_text = None
            assembled_soul_text = None
            daily_memory_text = None
            long_memory_text = None
            recall_memory_text = None
            user_profile_context_text = None

        reason_code = type("RC", (), {"value": "scope_disabled"})
        context = _Context()

    class _Router:
        def __init__(self, **kwargs: object) -> None:
            captured["topic_repo"] = kwargs["topic_agent_memory_repository"]
            captured["retrievable_repo"] = kwargs["retrievable_memory_repository"]

        def decide(self, **kwargs: object) -> _Decision:
            return _Decision()

    class _TopicRepo:
        def __init__(self, session, *, vector_recall=None, **kwargs: object) -> None:  # noqa: ANN001
            captured["topic_vector_recall"] = vector_recall

    class _RetrievableRepo:
        def __init__(self, session, *, vector_recall=None, **kwargs: object) -> None:  # noqa: ANN001
            captured["retrievable_vector_recall"] = vector_recall

    class _IgnoredRepo:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("amo_bot.telegram.dispatcher.AIRouter", _Router)
    monkeypatch.setattr("amo_bot.telegram.dispatcher.TopicAgentMemoryRepository", _TopicRepo)
    monkeypatch.setattr("amo_bot.telegram.dispatcher.RetrievableMemoryRepository", _RetrievableRepo)
    monkeypatch.setattr("amo_bot.telegram.dispatcher.UserMemoryProfileRepository", _IgnoredRepo)
    monkeypatch.setattr("amo_bot.telegram.dispatcher.PromptContextDocRepository", _IgnoredRepo)
    monkeypatch.setattr("amo_bot.telegram.dispatcher.create_session_factory", lambda *_: (lambda: _Session()))

    message = type("Msg", (), {})()
    message.text = "hello there"
    message.chat = type("Chat", (), {"id": -100, "type": "group"})
    message.message_thread_id = 7
    message.from_user = type("User", (), {"id": 1})
    message.reply_to_is_bot = False
    message.reply_to_user_is_bot = False
    message.reply_to_username = None
    message.message_id = 10

    asyncio.run(dispatcher._maybe_handle_ai_autoreply(message=message, role=Role.OWNER, bot_username="amo_bot"))

    assert captured["topic_vector_recall"] is vector_recall
    assert captured["retrievable_vector_recall"] is vector_recall
