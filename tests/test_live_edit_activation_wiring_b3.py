from __future__ import annotations

import asyncio

from amo_bot.auth.roles import Role
from amo_bot.telegram.commands import CommandRegistry
from amo_bot.telegram.dispatcher import Dispatcher


class _RoleResolver:
    async def resolve(self, *_args, **_kwargs):
        return Role.ADMIN


from amo_bot.ai.router import AIRouterReasonCode


class _DecisionMention:
    class _Context:
        scope_type = "group"
        flag_bot_mention = True
        flag_reply_to_bot = False
        recent_messages_text = None
        assembled_soul_text = None
        daily_memory_text = None
        long_memory_text = None

    reason_code = AIRouterReasonCode.MENTION_IN_ACTIVE_SCOPE
    context = _Context()


class _RouterMention:
    def __init__(self, topic_agent_memory_repository: object) -> None:
        pass

    def decide(self, **kwargs: object):
        return _DecisionMention()


class _AIService:
    def __init__(self, *, request_endpoint: str, streaming_mode: str) -> None:
        self.client = type("Client", (), {"request_endpoint": request_endpoint, "streaming_mode": streaming_mode})()
        self.last_stream_events: list[dict[str, object]] = []

    async def ask(self, _prompt: str) -> str:
        self.last_stream_events = [{"event": "start"}, {"event": "delta", "delta": "x"}, {"event": "done"}]
        return "final response"


def _mk_message(*, text: str, thread_id: int | None):
    msg = type("Msg", (), {})()
    msg.text = text
    msg.chat = type("Chat", (), {"id": -100, "type": "supergroup"})
    msg.message_thread_id = thread_id
    msg.from_user = type("User", (), {"id": 1})
    msg.reply_to_is_bot = False
    msg.reply_to_user_is_bot = False
    msg.reply_to_username = None
    msg.message_id = 10
    return msg


def _mk_dispatcher(*, ai_service: _AIService, adapter):
    sends: list[tuple[int, str, int | None]] = []

    async def _send_text(chat_id: int, text: str, thread_id: int | None = None):
        sends.append((chat_id, text, thread_id))

    return (
        Dispatcher(
            command_registry=CommandRegistry(),
            role_resolver=_RoleResolver(),
            send_text=_send_text,
            ai_service=ai_service,
            database_url=None,
            bot_username="amo_bot",
            live_edit_adapter=adapter,
        ),
        sends,
    )


def test_defaults_no_live_edit_activation(monkeypatch) -> None:
    consumed: list[dict[str, object]] = []

    class _Adapter:
        async def consume(self, **kwargs):
            consumed.append(kwargs["event"])

    dispatcher, sends = _mk_dispatcher(ai_service=_AIService(request_endpoint="generate", streaming_mode="off"), adapter=_Adapter())
    monkeypatch.setattr("amo_bot.telegram.dispatcher.AIRouter", _RouterMention)

    asyncio.run(dispatcher._maybe_handle_ai_autoreply(message=_mk_message(text="@amo_bot hi", thread_id=7), role=Role.ADMIN, bot_username="amo_bot"))

    assert consumed == []
    assert sends == [(-100, "final response", 7)]


def test_live_edit_without_chat_endpoint_no_live_edit_activation(monkeypatch) -> None:
    consumed: list[dict[str, object]] = []

    class _Adapter:
        async def consume(self, **kwargs):
            consumed.append(kwargs["event"])

    dispatcher, sends = _mk_dispatcher(ai_service=_AIService(request_endpoint="generate", streaming_mode="live_edit"), adapter=_Adapter())
    monkeypatch.setattr("amo_bot.telegram.dispatcher.AIRouter", _RouterMention)

    asyncio.run(dispatcher._maybe_handle_ai_autoreply(message=_mk_message(text="@amo_bot hi", thread_id=7), role=Role.ADMIN, bot_username="amo_bot"))

    assert consumed == []
    assert sends == [(-100, "final response", 7)]


def test_chat_without_live_edit_no_live_edit_activation(monkeypatch) -> None:
    consumed: list[dict[str, object]] = []

    class _Adapter:
        async def consume(self, **kwargs):
            consumed.append(kwargs["event"])

    dispatcher, sends = _mk_dispatcher(ai_service=_AIService(request_endpoint="chat", streaming_mode="collect_only"), adapter=_Adapter())
    monkeypatch.setattr("amo_bot.telegram.dispatcher.AIRouter", _RouterMention)

    asyncio.run(dispatcher._maybe_handle_ai_autoreply(message=_mk_message(text="@amo_bot hi", thread_id=7), role=Role.ADMIN, bot_username="amo_bot"))

    assert consumed == []
    assert sends == [(-100, "final response", 7)]


def test_both_gates_and_valid_trigger_with_request_context_invokes_live_path(monkeypatch) -> None:
    consumed: list[dict[str, object]] = []

    class _Adapter:
        async def consume(self, **kwargs):
            consumed.append(kwargs["event"])

    dispatcher, sends = _mk_dispatcher(ai_service=_AIService(request_endpoint="chat", streaming_mode="live_edit"), adapter=_Adapter())
    monkeypatch.setattr("amo_bot.telegram.dispatcher.AIRouter", _RouterMention)

    asyncio.run(
        dispatcher._maybe_handle_ai_autoreply(
            message=_mk_message(text="@amo_bot hi", thread_id=7),
            role=Role.ADMIN,
            bot_username="amo_bot",
            from_parsed_update=True,
        )
    )

    assert [event["event"] for event in consumed] == ["start", "delta", "done"]
    assert sends == [(-100, "final response", 7)]


def test_live_edit_requires_request_scoped_context_thread(monkeypatch) -> None:
    consumed: list[dict[str, object]] = []

    class _Adapter:
        async def consume(self, **kwargs):
            consumed.append(kwargs["event"])

    dispatcher, sends = _mk_dispatcher(ai_service=_AIService(request_endpoint="chat", streaming_mode="live_edit"), adapter=_Adapter())
    monkeypatch.setattr("amo_bot.telegram.dispatcher.AIRouter", _RouterMention)

    asyncio.run(dispatcher._maybe_handle_ai_autoreply(message=_mk_message(text="@amo_bot hi", thread_id=None), role=Role.ADMIN, bot_username="amo_bot"))

    assert consumed == []
    assert sends == [(-100, "final response", None)]


def test_live_edit_terminal_cancel_blocks_followup_deltas(monkeypatch) -> None:
    consumed: list[dict[str, object]] = []

    class _Adapter:
        async def consume(self, **kwargs):
            consumed.append(kwargs["event"])

    class _AIServiceCancel(_AIService):
        async def ask(self, _prompt: str) -> str:
            self.last_stream_events = [
                {"event": "start"},
                {"event": "delta", "delta": "x"},
                {"event": "cancel"},
                {"event": "delta", "delta": "ignored"},
                {"event": "done"},
            ]
            return "final response"

    dispatcher, sends = _mk_dispatcher(ai_service=_AIServiceCancel(request_endpoint="chat", streaming_mode="live_edit"), adapter=_Adapter())
    monkeypatch.setattr("amo_bot.telegram.dispatcher.AIRouter", _RouterMention)

    asyncio.run(
        dispatcher._maybe_handle_ai_autoreply(
            message=_mk_message(text="@amo_bot hi", thread_id=7),
            role=Role.ADMIN,
            bot_username="amo_bot",
            from_parsed_update=True,
        )
    )

    assert [event["event"] for event in consumed] == ["start", "delta", "cancel", "delta", "done"]
    assert sends == [(-100, "final response", 7)]
