from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

from amo_bot.ai.router import AIRouterDecision, AIRouterReasonCode
from amo_bot.auth.roles import Role
from amo_bot.telegram.dispatcher import (
    Dispatcher,
    _format_auto_research_no_result_note,
    _format_auto_research_success_note,
    should_chain_auto_research,
)
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


class _SequenceWebtoolDispatcher:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def execute(self, request):
        self.calls.append(request)
        if self.results:
            return self.results.pop(0)
        return SimpleNamespace(allowed=False, decision="deny", reason="empty_result", text="", sources=(), hosts=(), error=None)


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


def _mk_sequence_dispatcher(webtool_results):
    d, sent = _mk_dispatcher(SimpleNamespace(allowed=False, decision="deny", reason="unused", text="", sources=(), hosts=(), error=None))
    d.webtool_dispatcher = _SequenceWebtoolDispatcher(webtool_results)
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


def test_auto_research_current_rate_query_chains_static_scrape_into_prompt(monkeypatch):
    monkeypatch.setattr("amo_bot.telegram.dispatcher.AIRouter.decide", lambda self, **kwargs: _allowing_router_decision())
    search = SimpleNamespace(
        allowed=True,
        decision="allow",
        reason="search_completed",
        text="USD EUR live rate summary",
        sources=("https://rates.example/live", "https://other.example/eur-usd"),
        hosts=("rates.example", "other.example"),
        error=None,
    )
    scrape = SimpleNamespace(
        allowed=True,
        decision="allow",
        reason="scrape_completed",
        text="EUR USD current exchange rate is shown here with refreshed market data.",
        sources=("https://rates.example/live",),
        hosts=("rates.example",),
        error=None,
    )
    scrape2 = SimpleNamespace(
        allowed=True,
        decision="allow",
        reason="scrape_completed",
        text="Second source also has enough live EUR USD market wording for extraction.",
        sources=("https://other.example/eur-usd",),
        hosts=("other.example",),
        error=None,
    )
    d, sent = _mk_sequence_dispatcher([search, scrape, scrape2])
    calls = []

    async def _ask(prompt: str) -> str:
        calls.append(prompt)
        return "normal ai"

    d.ai_service.ask = _ask
    asyncio.run(
        d._maybe_handle_ai_autoreply(
            message=_mk_message("@amo_bot aktueller USD EUR Kurs jetzt?", reply_to_is_bot=False, reply_to_user_is_bot=False, reply_to_username=""),
            role=Role.ADMIN,
            bot_username="amo_bot",
            from_parsed_update=True,
        )
    )
    assert sent[0] == "normal ai"
    assert [c.capability for c in d.webtool_dispatcher.calls] == ["websearch", "webscraping", "webscraping"]
    assert calls and "AUTO-RESEARCH (LIVE WEB + PAGE EXTRACTION)" in calls[0]
    assert "host=rates.example" in calls[0]
    assert "EUR USD current exchange rate" in calls[0]


def test_auto_research_non_current_ordinary_query_does_not_chain(monkeypatch):
    monkeypatch.setattr("amo_bot.telegram.dispatcher.AIRouter.decide", lambda self, **kwargs: _allowing_router_decision())
    result = SimpleNamespace(allowed=True, decision="allow", reason="search_completed", text="fresh facts", sources=("https://a.example",), hosts=("a.example",), error=None)
    d, _sent = _mk_sequence_dispatcher([result])
    calls = []

    async def _ask(prompt: str) -> str:
        calls.append(prompt)
        return "normal ai"

    d.ai_service.ask = _ask
    asyncio.run(
        d._maybe_handle_ai_autoreply(
            message=_mk_message("@amo_bot was gibt es heute Neues zu Python?", reply_to_is_bot=False, reply_to_user_is_bot=False, reply_to_username=""),
            role=Role.ADMIN,
            bot_username="amo_bot",
            from_parsed_update=True,
        )
    )
    assert [c.capability for c in d.webtool_dispatcher.calls] == ["websearch"]
    assert "PAGE EXTRACTION" not in calls[0]


def test_auto_research_static_empty_then_browser_fallback_succeeds(monkeypatch):
    monkeypatch.setattr("amo_bot.telegram.dispatcher.AIRouter.decide", lambda self, **kwargs: _allowing_router_decision())
    search = SimpleNamespace(allowed=True, decision="allow", reason="search_completed", text="BTC live price", sources=("https://crypto.example/btc",), hosts=("crypto.example",), error=None)
    empty_scrape = SimpleNamespace(allowed=True, decision="allow", reason="scrape_completed", text="", sources=("https://crypto.example/btc",), hosts=("crypto.example",), error=None)
    browser = SimpleNamespace(allowed=True, decision="allow", reason="browser_completed", text="Bitcoin BTC live market price panel contains current exchange data.", sources=("https://crypto.example/btc",), hosts=("crypto.example",), error=None)
    d, _sent = _mk_sequence_dispatcher([search, empty_scrape, browser])
    calls = []

    async def _ask(prompt: str) -> str:
        calls.append(prompt)
        return "normal ai"

    d.ai_service.ask = _ask
    asyncio.run(
        d._maybe_handle_ai_autoreply(
            message=_mk_message("@amo_bot current BTC price live?", reply_to_is_bot=False, reply_to_user_is_bot=False, reply_to_username=""),
            role=Role.ADMIN,
            bot_username="amo_bot",
            from_parsed_update=True,
        )
    )
    assert [c.capability for c in d.webtool_dispatcher.calls] == ["websearch", "webscraping", "browser"]
    assert "- browser host=crypto.example" in calls[0]
    assert "Bitcoin BTC live market price" in calls[0]


def test_auto_research_followup_extraction_failure_is_truthful(monkeypatch):
    monkeypatch.setattr("amo_bot.telegram.dispatcher.AIRouter.decide", lambda self, **kwargs: _allowing_router_decision())
    search = SimpleNamespace(allowed=True, decision="allow", reason="search_completed", text="USD EUR live summary", sources=("https://rates.example/live",), hosts=("rates.example",), error=None)
    failed_scrape = SimpleNamespace(allowed=False, decision="deny", reason="http_error_403", text="", sources=(), hosts=(), error="HTTP error")
    failed_browser = SimpleNamespace(allowed=False, decision="provider_unavailable", reason="browser_provider_not_configured", text="", sources=(), hosts=(), error="No browser")
    d, _sent = _mk_sequence_dispatcher([search, failed_scrape, failed_browser])
    calls = []

    async def _ask(prompt: str) -> str:
        calls.append(prompt)
        return "normal ai"

    d.ai_service.ask = _ask
    asyncio.run(
        d._maybe_handle_ai_autoreply(
            message=_mk_message("@amo_bot current EUR USD exchange rate now?", reply_to_is_bot=False, reply_to_user_is_bot=False, reply_to_username=""),
            role=Role.ADMIN,
            bot_username="amo_bot",
            from_parsed_update=True,
        )
    )
    assert "WEB SEARCH SUCCEEDED, FOLLOW-UP EXTRACTION UNCONFIRMED" in calls[0]
    assert "follow-up page extraction produced no usable confirmation" in calls[0]
    assert "Do NOT say or imply that the bot has no web tools" in calls[0]


def test_auto_research_chain_caps_urls_browser_and_text(monkeypatch):
    monkeypatch.setattr("amo_bot.telegram.dispatcher.AIRouter.decide", lambda self, **kwargs: _allowing_router_decision())
    search = SimpleNamespace(
        allowed=True,
        decision="allow",
        reason="search_completed",
        text="stock live price",
        sources=("https://one.example/a", "https://two.example/b", "https://three.example/c", "https://four.example/d"),
        hosts=("one.example", "two.example", "three.example", "four.example"),
        error=None,
    )
    fail = SimpleNamespace(allowed=False, decision="deny", reason="empty_result", text="", sources=(), hosts=(), error=None)
    long_browser = SimpleNamespace(allowed=True, decision="allow", reason="browser_completed", text="X" * 3000, sources=("https://one.example/a",), hosts=("one.example",), error=None)
    d, _sent = _mk_sequence_dispatcher([search, fail, long_browser, fail, fail, fail])
    calls = []

    async def _ask(prompt: str) -> str:
        calls.append(prompt)
        return "normal ai"

    d.ai_service.ask = _ask
    asyncio.run(
        d._maybe_handle_ai_autoreply(
            message=_mk_message("@amo_bot current stock price live?", reply_to_is_bot=False, reply_to_user_is_bot=False, reply_to_username=""),
            role=Role.ADMIN,
            bot_username="amo_bot",
            from_parsed_update=True,
        )
    )
    assert [c.capability for c in d.webtool_dispatcher.calls] == ["websearch", "webscraping", "browser"]
    assert calls[0].count("X") < 1700


def test_should_chain_auto_research_requires_freshness_and_market_terms():
    assert should_chain_auto_research("current USD EUR rate now", capability="websearch")
    assert not should_chain_auto_research("heute neues zu Python", capability="websearch")
    assert not should_chain_auto_research("current USD EUR rate now", capability="browser")


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
