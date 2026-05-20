from __future__ import annotations

import asyncio

from amo_bot.telegram.live_edit_adapter import (
    DisabledTelegramLiveEditAdapter,
    LiveEditFailure,
    SafeTelegramLiveEditAdapter,
)


def test_disabled_adapter_consumes_events_without_send_or_edit_calls() -> None:
    calls: list[tuple[str, object]] = []

    async def _send(*_args, **_kwargs):
        calls.append(("send", None))
        return {"message_id": 100}

    async def _edit(*_args, **_kwargs):
        calls.append(("edit", None))
        return {}

    adapter = SafeTelegramLiveEditAdapter(enabled=False, send_text=_send, edit_text=_edit)

    for event in (
        {"event": "start"},
        {"event": "delta", "delta": "Hel"},
        {"event": "done"},
        {"event": "error", "error": "x"},
    ):
        asyncio.run(adapter.consume(chat_id=-100, message_thread_id=10, event=event))

    assert calls == []


def test_disabled_default_adapter_sends_and_edits_nothing() -> None:
    adapter = DisabledTelegramLiveEditAdapter()

    asyncio.run(adapter.consume(chat_id=-100, message_thread_id=10, event={"event": "start"}))
    asyncio.run(adapter.consume(chat_id=-100, message_thread_id=10, event={"event": "delta", "delta": "SECRET"}))
    asyncio.run(adapter.consume(chat_id=-100, message_thread_id=10, event={"event": "done"}))


def test_live_edit_failure_records_metadata_only_without_content_leak() -> None:
    failures: list[LiveEditFailure] = []

    async def _record(failure: LiveEditFailure) -> None:
        failures.append(failure)

    async def _send(_chat_id: int, _text: str, _thread_id: int | None):
        return {"message_id": 777}

    async def _edit(_chat_id: int, _message_id: int, _text: str, _thread_id: int | None):
        raise RuntimeError("telegram edit failed with sensitive prompt data that must not be recorded")

    adapter = SafeTelegramLiveEditAdapter(enabled=True, send_text=_send, edit_text=_edit, failure_recorder=_record)

    asyncio.run(adapter.consume(chat_id=-100, message_thread_id=10, event={"event": "start"}))
    asyncio.run(adapter.consume(chat_id=-100, message_thread_id=10, event={"event": "delta", "delta": "SECRET_PROMPT_TEXT"}))

    assert [(f.stage, f.code) for f in failures] == [("delta", "edit_failed")]


def test_live_edit_throttles_fast_deltas_and_never_leaks_content() -> None:
    failures: list[LiveEditFailure] = []
    edits: list[str] = []

    async def _record(failure: LiveEditFailure) -> None:
        failures.append(failure)

    async def _send(_chat_id: int, _text: str, _thread_id: int | None):
        return {"message_id": 42}

    async def _edit(_chat_id: int, _message_id: int, text: str, _thread_id: int | None):
        edits.append(text)
        return {}

    adapter = SafeTelegramLiveEditAdapter(
        enabled=True,
        send_text=_send,
        edit_text=_edit,
        failure_recorder=_record,
        min_edit_interval_seconds=60.0,
    )

    asyncio.run(adapter.consume(chat_id=-100, message_thread_id=10, event={"event": "start"}))
    asyncio.run(adapter.consume(chat_id=-100, message_thread_id=10, event={"event": "delta", "delta": "SECRET_DELTA"}))
    asyncio.run(adapter.consume(chat_id=-100, message_thread_id=10, event={"event": "delta", "delta": "ANOTHER_SECRET"}))

    assert edits == ["…"]
    assert [(f.stage, f.code) for f in failures] == [("delta", "edit_throttled")]


def test_live_edit_disables_after_capped_failures_with_safe_fallback() -> None:
    failures: list[LiveEditFailure] = []
    edit_calls = 0

    async def _record(failure: LiveEditFailure) -> None:
        failures.append(failure)

    async def _send(_chat_id: int, _text: str, _thread_id: int | None):
        return {"message_id": 42}

    async def _edit(_chat_id: int, _message_id: int, _text: str, _thread_id: int | None):
        nonlocal edit_calls
        edit_calls += 1
        raise RuntimeError("sensitive stream token should never be logged")

    adapter = SafeTelegramLiveEditAdapter(
        enabled=True,
        send_text=_send,
        edit_text=_edit,
        failure_recorder=_record,
        min_edit_interval_seconds=0.0,
        max_consecutive_edit_failures=2,
    )

    asyncio.run(adapter.consume(chat_id=-100, message_thread_id=10, event={"event": "start"}))
    asyncio.run(adapter.consume(chat_id=-100, message_thread_id=10, event={"event": "delta", "delta": "a"}))
    asyncio.run(adapter.consume(chat_id=-100, message_thread_id=10, event={"event": "delta", "delta": "b"}))
    asyncio.run(adapter.consume(chat_id=-100, message_thread_id=10, event={"event": "delta", "delta": "c"}))

    assert edit_calls == 2
    assert [(f.stage, f.code) for f in failures] == [
        ("delta", "edit_failed"),
        ("delta", "edit_failed"),
        ("delta", "edit_disabled_after_failures"),
    ]


def test_dispatcher_consumes_current_request_events_after_ask_and_keeps_final_response_parity() -> None:
    calls: list[tuple[int, str, int | None]] = []

    async def _send_text(chat_id: int, text: str, thread_id: int | None = None):
        calls.append((chat_id, text, thread_id))

    from amo_bot.auth.roles import Role
    from amo_bot.telegram.dispatcher import Dispatcher
    from amo_bot.telegram.commands import CommandRegistry

    class _RoleResolver:
        async def resolve(self, *_args, **_kwargs):
            return Role.ADMIN

    class _Decision:
        class _Context:
            scope_type = "group"
            flag_bot_mention = True
            flag_reply_to_bot = False
            recent_messages_text = None
            assembled_soul_text = None
            daily_memory_text = None
            long_memory_text = None

        reason_code = type("RC", (), {"value": "mention_in_active_scope"})
        context = _Context()

    class _Router:
        def __init__(self, topic_agent_memory_repository: object) -> None:
            pass

        def decide(self, **kwargs: object) -> _Decision:
            return _Decision()

    consumed_events: list[tuple[int, int | None, dict[str, object]]] = []

    class _Adapter:
        async def consume(self, *, chat_id: int, message_thread_id: int | None, event: dict[str, object]) -> None:
            consumed_events.append((chat_id, message_thread_id, event))

    class _AIService:
        def __init__(self) -> None:
            self.last_stream_events: list[dict[str, object]] = []

        async def ask(self, _prompt: str) -> str:
            self.last_stream_events = [
                {"event": "start"},
                {"event": "delta", "delta": "He"},
                {"event": "done"},
            ]
            return "final response"

    from unittest.mock import patch

    dispatcher = Dispatcher(
        command_registry=CommandRegistry(),
        role_resolver=_RoleResolver(),
        send_text=_send_text,
        ai_service=_AIService(),
        database_url=None,
        bot_username="amo_bot",
        live_edit_adapter=_Adapter(),
    )

    from amo_bot.telegram.update_parser import parse_update

    update = {
        "update_id": 1,
        "message": {
            "message_id": 20,
            "date": 1,
            "chat": {"id": -100, "type": "supergroup"},
            "from": {"id": 9, "is_bot": False, "first_name": "U"},
            "text": "@amo_bot hello",
            "entities": [{"type": "mention", "offset": 0, "length": 8}],
        },
    }

    parsed = parse_update(update)
    assert parsed is not None and parsed.message is not None

    with patch("amo_bot.telegram.dispatcher.AIRouter", _Router):
        asyncio.run(dispatcher.handle_raw_update(update))

    assert consumed_events == [
        (-100, None, {"event": "start"}),
        (-100, None, {"event": "delta", "delta": "He"}),
        (-100, None, {"event": "done"}),
    ]
    assert calls == [(-100, "final response", None)]
    assert all(text != "…" for _, text, _ in calls)
