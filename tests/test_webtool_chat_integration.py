from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

from amo_bot.ai.router import AIRouterDecision, AIRouterReasonCode
from amo_bot.auth.roles import Role
from amo_bot.telegram.dispatcher import Dispatcher, _format_auto_research_no_result_note, _format_auto_research_success_note
from amo_bot.telegram.update_parser import TelegramChat, TelegramMessage, TelegramUser


class _RoleResolver:
    async def resolve(self, *_args, **_kwargs):
        return Role.ADMIN


class _AIService:
    async def ask(self, prompt: str) -> str:
        return "normal ai"


class _WebtoolDispatcher:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def execute(self, request):
        self.calls.append(request)
        return self.result


def _mk_message(text: str, *, reply_to_is_bot: bool = True, reply_to_user_is_bot: bool = True, reply_to_username: str = "amo_bot") -> TelegramMessage:
    return TelegramMessage(
        message_id=1,
        chat=TelegramChat(id=-100, type="supergroup", title="g", username=None),
        from_user=TelegramUser(id=123, is_bot=False, first_name="u", username="u", language_code="de"),
        text=text,
        attachments=(),
        message_thread_id=7,
        reply_to_message=None,
        reply_to_message_id=None,
        reply_to_message_text="",
        reply_to_user_id=None,
        reply_to_username=reply_to_username,
        reply_to_is_bot=reply_to_is_bot,
        reply_to_user_is_bot=reply_to_user_is_bot,
    )


def _mk_dispatcher(webtool_result, *, database_url: str | None = None):
    sent = []

    async def _send(chat_id: int, text: str, thread_id=None):
        sent.append(text)
        return {"message_id": 99}

    d = Dispatcher(
        command_registry=SimpleNamespace(get=lambda *_: None, is_allowed=lambda *_: True),
        role_resolver=_RoleResolver(),
        send_text=_send,
        ai_service=_AIService(),
        webtool_dispatcher=_WebtoolDispatcher(webtool_result),
        database_url=database_url,
    )
    return d, sent


def _allowing_router_decision() -> AIRouterDecision:
    return AIRouterDecision(
        passthrough=True,
        eligible=True,
        reason_code=AIRouterReasonCode.MENTION_IN_ACTIVE_SCOPE,
        context=SimpleNamespace(
            scope_type="group_topic",
            flag_bot_mention=True,
            flag_reply_to_bot=True,
            recent_messages_text="",
            user_profile_context_text="",
            assembled_soul_text="",
            daily_memory_text="",
            long_memory_text="",
        ),
    )


def test_webtool_trigger_dispatches_through_dispatcher(monkeypatch):
    monkeypatch.setattr("amo_bot.telegram.dispatcher.AIRouter.decide", lambda self, **kwargs: _allowing_router_decision())
    result = SimpleNamespace(allowed=True, decision="allow", reason="ok", text="answer", sources=("https://a",), hosts=("a",), error=None)
    d, sent = _mk_dispatcher(result)
    asyncio.run(d._maybe_handle_ai_autoreply(message=_mk_message("@amo_bot websearch: python"), role=Role.ADMIN, bot_username="amo_bot", from_parsed_update=True))
    assert sent and sent[0].startswith("Websearch:")
    assert len(d.webtool_dispatcher.calls) == 1


def test_quota_denied_no_provider_call_and_user_gets_limit_text(monkeypatch):
    monkeypatch.setattr("amo_bot.telegram.dispatcher.AIRouter.decide", lambda self, **kwargs: _allowing_router_decision())
    result = SimpleNamespace(allowed=False, decision="quota_exceeded", reason="quota_exceeded", text="", sources=(), hosts=(), error=None)
    d, sent = _mk_dispatcher(result)
    asyncio.run(d._maybe_handle_ai_autoreply(message=_mk_message("@amo_bot websearch: test"), role=Role.ADMIN, bot_username="amo_bot", from_parsed_update=True))
    assert "Limit" in sent[0] or "limit" in sent[0]


def test_provider_unavailable_fail_closed_text(monkeypatch):
    monkeypatch.setattr("amo_bot.telegram.dispatcher.AIRouter.decide", lambda self, **kwargs: _allowing_router_decision())
    result = SimpleNamespace(allowed=False, decision="provider_unavailable", reason="search_provider_not_configured", text="", sources=(), hosts=(), error="No search provider configured")
    d, sent = _mk_dispatcher(result)
    asyncio.run(d._maybe_handle_ai_autoreply(message=_mk_message("@amo_bot websearch: test"), role=Role.ADMIN, bot_username="amo_bot", from_parsed_update=True))
    assert "nicht ausführen" in sent[0]


def test_provider_success_returns_sanitized_compact_output(monkeypatch):
    monkeypatch.setattr("amo_bot.telegram.dispatcher.AIRouter.decide", lambda self, **kwargs: _allowing_router_decision())
    result = SimpleNamespace(allowed=True, decision="allow", reason="search_completed", text="  foo\n\nbar   ", sources=("https://a",), hosts=("a",), error=None)
    d, sent = _mk_dispatcher(result)
    asyncio.run(d._maybe_handle_ai_autoreply(message=_mk_message("@amo_bot websearch: test"), role=Role.ADMIN, bot_username="amo_bot", from_parsed_update=True))
    assert sent[0] == "Websearch: foo bar"


def test_auto_research_injects_strict_context_and_calls_dispatcher_once(monkeypatch):
    monkeypatch.setattr("amo_bot.telegram.dispatcher.AIRouter.decide", lambda self, **kwargs: _allowing_router_decision())
    result = SimpleNamespace(allowed=True, decision="allow", reason="search_completed", text="fresh facts", sources=("https://a", "https://b"), hosts=("a", "b"), error=None)
    d, sent = _mk_dispatcher(result)
    calls = []

    async def _ask(prompt: str) -> str:
        calls.append(prompt)
        return "normal ai"

    d.ai_service.ask = _ask
    asyncio.run(
        d._maybe_handle_ai_autoreply(
            message=_mk_message("@amo_bot Wie ist der aktuelle wetter stand heute?", reply_to_is_bot=False, reply_to_user_is_bot=False, reply_to_username=""),
            role=Role.ADMIN,
            bot_username="amo_bot",
            from_parsed_update=True,
        )
    )
    assert len(d.webtool_dispatcher.calls) == 1
    assert sent[0] == "normal ai"
    assert calls and "AUTO-RESEARCH (LIVE WEB) — STRICT INSTRUCTION" in calls[0]
    assert "A live websearch/web tool result is available in this turn" in calls[0]
    assert "Do NOT claim or imply that the bot has no web tools" in calls[0]
    assert "no live data capability" in calls[0]
    assert "cannot search the web" in calls[0]
    assert "Use the supplied web summary as primary evidence" in calls[0]
    assert "do NOT override it with stale memory/priors" in calls[0]
    assert "do NOT invent dates, prices, levels" in calls[0]
    assert "available live sources do not confirm that exact value" in calls[0]
    assert "Source hosts: a, b" in calls[0]


def test_auto_research_empty_result_injects_no_live_warning(monkeypatch):
    monkeypatch.setattr("amo_bot.telegram.dispatcher.AIRouter.decide", lambda self, **kwargs: _allowing_router_decision())
    result = SimpleNamespace(allowed=False, decision="provider_unavailable", reason="search_provider_not_configured", text="", sources=(), hosts=(), error=None)
    d, sent = _mk_dispatcher(result)
    calls = []

    async def _ask(prompt: str) -> str:
        calls.append(prompt)
        return "normal ai"

    d.ai_service.ask = _ask
    asyncio.run(
        d._maybe_handle_ai_autoreply(
            message=_mk_message("@amo_bot current bitcoin price now?", reply_to_is_bot=False, reply_to_user_is_bot=False, reply_to_username=""),
            role=Role.ADMIN,
            bot_username="amo_bot",
            from_parsed_update=True,
        )
    )
    assert sent[0] == "normal ai"
    assert calls and "AUTO-RESEARCH STATUS — WEB ATTEMPTED, NO USABLE RESULT" in calls[0]
    assert "A live websearch attempt was made in this turn" in calls[0]
    assert "the provider was unavailable" in calls[0]
    assert "Do NOT say or imply that the bot has no web tools" in calls[0]
    assert "Die Websuche wurde versucht" in calls[0]
    assert "do NOT invent current facts" in calls[0]


def test_auto_research_success_note_forbids_no_tool_claims_and_prefers_live_sources():
    note = _format_auto_research_success_note(capability="websearch", text="fresh\nsummary", hosts=("a", "b"))

    assert "LIVE WEB" in note
    assert "A live websearch/web tool result is available in this turn" in note
    assert "Do NOT claim or imply that the bot has no web tools" in note
    assert "no live data capability" in note
    assert "cannot search the web" in note
    assert "Use the supplied web summary as primary evidence" in note
    assert "available live sources do not confirm that exact value" in note
    assert "do not say no webtools" in note
    assert "Source hosts: a, b" in note


def test_auto_research_no_result_note_is_reason_specific_and_forbids_no_tool_claims():
    empty_note = _format_auto_research_no_result_note(capability="websearch", reason="empty_result")
    timeout_note = _format_auto_research_no_result_note(capability="websearch", reason="provider_timeout")

    assert "websearch attempt was made" in empty_note
    assert "returned no usable hits" in empty_note
    assert "provider timed out" in timeout_note
    assert "Do NOT say or imply that the bot has no web tools" in empty_note
    assert "no live data capability" in empty_note
    assert "do NOT invent current facts" in empty_note


def test_normal_autoreply_without_trigger_not_quota_limited(monkeypatch):
    monkeypatch.setattr("amo_bot.telegram.dispatcher.AIRouter.decide", lambda self, **kwargs: _allowing_router_decision())
    result = SimpleNamespace(allowed=False, decision="quota_exceeded", reason="quota_exceeded", text="", sources=(), hosts=(), error=None)
    d, sent = _mk_dispatcher(result)
    asyncio.run(
        d._maybe_handle_ai_autoreply(
            message=_mk_message(
                "@amo_bot hallo",
                reply_to_is_bot=False,
                reply_to_user_is_bot=False,
                reply_to_username="",
            ),
            role=Role.ADMIN,
            bot_username="amo_bot",
            from_parsed_update=True,
        )
    )
    assert sent[0] == "normal ai"
    assert len(d.webtool_dispatcher.calls) == 0


def test_metadata_only_logging_no_query_or_url(caplog, monkeypatch):
    caplog.set_level(logging.INFO)
    monkeypatch.setattr("amo_bot.telegram.dispatcher.AIRouter.decide", lambda self, **kwargs: _allowing_router_decision())
    result = SimpleNamespace(allowed=True, decision="allow", reason="ok", text="answer", sources=("https://a",), hosts=("a",), error=None)
    d, _ = _mk_dispatcher(result)
    text = "@amo_bot websearch: secret query https://private.local"
    asyncio.run(d._maybe_handle_ai_autoreply(message=_mk_message(text), role=Role.ADMIN, bot_username="amo_bot", from_parsed_update=True))
    merged = "\n".join(r.getMessage() for r in caplog.records)
    assert "secret query" not in merged
    assert "private.local" not in merged
