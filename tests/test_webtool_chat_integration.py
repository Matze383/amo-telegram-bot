from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

from amo_bot.ai.router import AIRouterDecision, AIRouterReasonCode
from amo_bot.auth.roles import Role
from amo_bot.telegram.dispatcher import (
    Dispatcher,
    _chain_diagnostic_snapshot,
    _format_auto_research_no_result_note,
    _format_auto_research_success_note,
    should_chain_auto_research,
    _select_chain_urls,
)
from amo_bot.telegram.update_parser import TelegramChat, TelegramMessage, TelegramUser
from amo_bot.telegram.webtool_chat_integration import build_empty_result_retry_query


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


def _mk_message(
    text: str,
    *,
    reply_to_is_bot: bool = True,
    reply_to_user_is_bot: bool = True,
    reply_to_username: str = "amo_bot",
    reply_to_message_text: str = "",
    reply_to_message_id: int | None = None,
) -> TelegramMessage:
    return TelegramMessage(
        message_id=1,
        chat=TelegramChat(id=-100, type="supergroup", title="g", username=None),
        from_user=TelegramUser(id=123, is_bot=False, first_name="u", username="u", language_code="de"),
        text=text,
        attachments=(),
        message_thread_id=7,
        reply_to_message=None,
        reply_to_message_id=reply_to_message_id,
        reply_to_message_text=reply_to_message_text,
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
            current_time_context_text="Context:\nCurrent date: 2026-06-03\nTimezone: Europe/Berlin\nWhen answering about current events or live facts, prefer available web research over prior knowledge.",
            user_profile_context_text="",
            assembled_soul_text="",
            daily_memory_text="",
            long_memory_text="",
            recall_memory_text="",
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


def test_auto_research_injects_strict_context_without_chain_for_weak_current_intent(monkeypatch):
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
            message=_mk_message("@amo_bot Python decorators 2020", reply_to_is_bot=False, reply_to_user_is_bot=False, reply_to_username=""),
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
    assert "Memory and model priors are not acceptable substitutes for live evidence" in calls[0]


def test_auto_research_classifier_current_prompt_empty_result_fails_closed(monkeypatch):
    monkeypatch.setattr("amo_bot.telegram.dispatcher.AIRouter.decide", lambda self, **kwargs: _allowing_router_decision())
    result = SimpleNamespace(allowed=False, decision="deny", reason="empty_result", text="", sources=(), hosts=(), error=None)
    d, sent = _mk_dispatcher(result)
    calls = []

    async def _ask(prompt: str) -> str:
        calls.append(prompt)
        return "normal ai"

    d.ai_service.ask = _ask
    asyncio.run(
        d._maybe_handle_ai_autoreply(
            message=_mk_message("@amo_bot Ist der Dienst gerade down?", reply_to_is_bot=False, reply_to_user_is_bot=False, reply_to_username=""),
            role=Role.ADMIN,
            bot_username="amo_bot",
            from_parsed_update=True,
        )
    )
    assert sent[0] == "normal ai"
    assert [c.capability for c in d.webtool_dispatcher.calls][0] == "websearch"
    assert calls and "AUTO-RESEARCH STATUS — WEB ATTEMPTED" in calls[0]
    assert "NO USABLE RESULT" in calls[0]
    assert "live" in calls[0]
    assert "Memory and model priors are not acceptable substitutes for live evidence" in calls[0]


def test_auto_research_sports_tournament_empty_result_fails_closed(monkeypatch):
    monkeypatch.setattr("amo_bot.telegram.dispatcher.AIRouter.decide", lambda self, **kwargs: _allowing_router_decision())
    result = SimpleNamespace(allowed=False, decision="deny", reason="empty_result", text="", sources=(), hosts=(), error=None)
    d, sent = _mk_dispatcher(result)
    calls = []

    async def _ask(prompt: str) -> str:
        calls.append(prompt)
        return "normal ai"

    d.ai_service.ask = _ask
    asyncio.run(
        d._maybe_handle_ai_autoreply(
            message=_mk_message("@amo_bot Wie läuft die WM-Vorrunde?", reply_to_is_bot=False, reply_to_user_is_bot=False, reply_to_username=""),
            role=Role.ADMIN,
            bot_username="amo_bot",
            from_parsed_update=True,
        )
    )
    assert sent[0] == "normal ai"
    assert [c.capability for c in d.webtool_dispatcher.calls][0] == "websearch"
    assert "AUTO-RESEARCH STATUS — WEB ATTEMPTED" in calls[0]
    assert "Strict anti-hallucination" in calls[0]
    assert "Do NOT provide an estimated current value" in calls[0]


def test_auto_research_empty_result_retries_once_with_stable_btc_query_and_chains(monkeypatch):
    monkeypatch.setattr("amo_bot.telegram.dispatcher.AIRouter.decide", lambda self, **kwargs: _allowing_router_decision())
    empty = SimpleNamespace(allowed=False, decision="deny", reason="empty_result", text="", sources=(), hosts=(), error=None)
    search = SimpleNamespace(
        allowed=True,
        decision="allow",
        reason="search_completed",
        text="BTC live price summary from market sources",
        sources=("https://crypto.example/btc",),
        hosts=("crypto.example",),
        error=None,
    )
    scrape = SimpleNamespace(
        allowed=True,
        decision="allow",
        reason="scrape_completed",
        text="Bitcoin BTC live price page confirms current USD market data.",
        sources=("https://crypto.example/btc",),
        hosts=("crypto.example",),
        error=None,
    )
    d, sent = _mk_sequence_dispatcher([empty, search, scrape])
    calls = []

    async def _ask(prompt: str) -> str:
        calls.append(prompt)
        return "normal ai"

    d.ai_service.ask = _ask
    asyncio.run(
        d._maybe_handle_ai_autoreply(
            message=_mk_message(
                "@amo_bot was ist der aktuelle BTC Kurs? Bot answer: alter Preis 100 USD reicht nicht",
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
    assert [c.capability for c in d.webtool_dispatcher.calls] == ["websearch", "websearch", "webscraping"]
    assert d.webtool_dispatcher.calls[1].query == "bitcoin kurs USD BTC"
    assert "alter Preis" not in d.webtool_dispatcher.calls[1].query
    assert "BTC live price summary" in calls[0]
    assert "Bitcoin BTC live price page confirms" in calls[0]


def test_empty_result_retry_query_uses_current_message_not_prior_context():
    query = build_empty_result_retry_query("@amo_bot such weiter, das reicht nicht — Bot answer: BTC war 100 USD. Aktueller Bitcoin Preis?")

    assert query == "bitcoin kurs USD BTC"
    assert "100" not in query
    assert "Bot answer" not in query




def test_empty_result_retry_query_simplifies_generic_current_question():
    query = build_empty_result_retry_query(
        "@amo_bot such bitte weiter, das reicht nicht — Bot answer: Alter Stand war falsch. "
        "Was gibt es heute Neues zum Python 3.14 Release Candidate?"
    )

    assert query == "Alter falsch Neues Python 3.14 Release Candidate"
    assert len(query) < 90
    assert "@amo_bot" not in query
    assert "such" not in query.lower()


def test_chain_diagnostic_snapshot_is_metadata_only_and_splits_host_counts():
    diagnostics = _chain_diagnostic_snapshot(
        search_hosts=("search-one.example", "search-two.example", "search-three.example", "search-four.example", "search-five.example"),
        chain_urls=(
            "https://search-one.example/a",
            "https://search-two.example/b",
            "https://search-three.example/c",
            "https://search-four.example/d",
            "https://search-five.example/e",
        ),
        static_attempts=5,
        browser_attempts=1,
        chain_extracts=[],
        reason_buckets={"empty_text": 2, "provider_unavailable": 1, "timeout": 1},
        content_length_buckets={"zero": 3, "short": 1},
        timeout_count=1,
        error_class_buckets={"RuntimeError": 1},
    )

    assert diagnostics["status"] if "status" in diagnostics else True
    assert diagnostics["search_host_count"] == 5
    assert diagnostics["selected_url_host_count"] == 5
    assert diagnostics["extraction_host_count"] == 0
    assert diagnostics["host_count"] == 0
    assert diagnostics["failed_attempt_count"] == 6
    assert diagnostics["reason_buckets"] == {"empty_text": 2, "provider_unavailable": 1, "timeout": 1}
    serialized = str(diagnostics)
    assert "https://" not in serialized
    assert "/a" not in serialized
    assert "/e" not in serialized


def test_select_chain_urls_caps_at_five_and_dedupes_hosts():
    urls = _select_chain_urls((
        "https://one.example/a",
        "https://two.example/b",
        "https://one.example/duplicate",
        "https://three.example/c",
        "https://four.example/d",
        "https://five.example/e",
        "https://six.example/f",
    ))

    assert urls == (
        "https://one.example/a",
        "https://two.example/b",
        "https://three.example/c",
        "https://four.example/d",
        "https://five.example/e",
    )

def test_auto_research_empty_result_retry_failure_forbids_stale_estimate(monkeypatch):
    monkeypatch.setattr("amo_bot.telegram.dispatcher.AIRouter.decide", lambda self, **kwargs: _allowing_router_decision())
    empty1 = SimpleNamespace(allowed=False, decision="deny", reason="empty_result", text="", sources=(), hosts=(), error=None)
    empty2 = SimpleNamespace(allowed=False, decision="deny", reason="empty_result", text="", sources=(), hosts=(), error=None)
    d, _sent = _mk_sequence_dispatcher([empty1, empty2])
    calls = []

    async def _ask(prompt: str) -> str:
        calls.append(prompt)
        return "normal ai"

    d.ai_service.ask = _ask
    asyncio.run(
        d._maybe_handle_ai_autoreply(
            message=_mk_message("@amo_bot current BTC price now?", reply_to_is_bot=False, reply_to_user_is_bot=False, reply_to_username=""),
            role=Role.ADMIN,
            bot_username="amo_bot",
            from_parsed_update=True,
        )
    )
    assert [c.capability for c in d.webtool_dispatcher.calls] == ["websearch", "websearch"]
    assert "RETRY ALSO NO USABLE RESULT" in calls[0]
    assert "no current value/fact could be confirmed" in calls[0]
    assert "do NOT reuse old/stale prices" in calls[0]
    assert "Do NOT provide an estimated current value" in calls[0]


def test_auto_research_empty_result_retry_only_once(monkeypatch):
    monkeypatch.setattr("amo_bot.telegram.dispatcher.AIRouter.decide", lambda self, **kwargs: _allowing_router_decision())
    empties = [SimpleNamespace(allowed=False, decision="deny", reason="empty_result", text="", sources=(), hosts=(), error=None) for _ in range(4)]
    d, _sent = _mk_sequence_dispatcher(empties)

    async def _ask(prompt: str) -> str:
        return "normal ai"

    d.ai_service.ask = _ask
    asyncio.run(
        d._maybe_handle_ai_autoreply(
            message=_mk_message("@amo_bot aktueller BTC Kurs jetzt?", reply_to_is_bot=False, reply_to_user_is_bot=False, reply_to_username=""),
            role=Role.ADMIN,
            bot_username="amo_bot",
            from_parsed_update=True,
        )
    )
    assert [c.capability for c in d.webtool_dispatcher.calls] == ["websearch", "websearch"]


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


def test_auto_research_current_non_market_query_chains_static_scrape_into_prompt(monkeypatch):
    monkeypatch.setattr("amo_bot.telegram.dispatcher.AIRouter.decide", lambda self, **kwargs: _allowing_router_decision())
    search = SimpleNamespace(allowed=True, decision="allow", reason="search_completed", text="Python latest news", sources=("https://python.example/news",), hosts=("python.example",), error=None)
    scrape = SimpleNamespace(
        allowed=True,
        decision="allow",
        reason="scrape_completed",
        text="Python release page contains current news and version update details for today.",
        sources=("https://python.example/news",),
        hosts=("python.example",),
        error=None,
    )
    d, _sent = _mk_sequence_dispatcher([search, scrape])
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
    assert [c.capability for c in d.webtool_dispatcher.calls] == ["websearch", "webscraping"]
    assert "AUTO-RESEARCH (LIVE WEB + PAGE EXTRACTION)" in calls[0]
    assert "Python release page contains current news" in calls[0]


def test_auto_research_timeless_ordinary_query_does_not_chain(monkeypatch):
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
            message=_mk_message("@amo_bot erkläre mir Python decorators", reply_to_is_bot=False, reply_to_user_is_bot=False, reply_to_username=""),
            role=Role.ADMIN,
            bot_username="amo_bot",
            from_parsed_update=True,
        )
    )
    assert [c.capability for c in d.webtool_dispatcher.calls] == []
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


def test_user_feedback_followup_reply_triggers_search_and_extraction_without_freshness_terms(monkeypatch):
    monkeypatch.setattr("amo_bot.telegram.dispatcher.AIRouter.decide", lambda self, **kwargs: _allowing_router_decision())
    search = SimpleNamespace(
        allowed=True,
        decision="allow",
        reason="search_completed",
        text="More source candidates for the previous topic",
        sources=("https://source.example/one",),
        hosts=("source.example",),
        error=None,
    )
    scrape = SimpleNamespace(
        allowed=True,
        decision="allow",
        reason="scrape_completed",
        text="Additional source page has enough details to compare against the thin prior answer.",
        sources=("https://source.example/one",),
        hosts=("source.example",),
        error=None,
    )
    d, _sent = _mk_sequence_dispatcher([search, scrape])
    calls = []

    async def _ask(prompt: str) -> str:
        calls.append(prompt)
        return "normal ai"

    d.ai_service.ask = _ask
    asyncio.run(
        d._maybe_handle_ai_autoreply(
            message=_mk_message(
                "such weiter / öffne andere Quellen",
                reply_to_message_id=41,
                reply_to_message_text="Bot answer: Ich konnte nur eine dünne Quelle zum Thema Solarförderung finden.",
            ),
            role=Role.ADMIN,
            bot_username="amo_bot",
            from_parsed_update=True,
        )
    )
    assert [c.capability for c in d.webtool_dispatcher.calls] == ["websearch", "webscraping"]
    assert "Bot answer" in d.webtool_dispatcher.calls[0].query
    assert "such weiter" in d.webtool_dispatcher.calls[0].query
    assert "FOLLOW-UP AUTO-RESEARCH (LIVE WEB + PAGE EXTRACTION)" in calls[0]
    assert "user feedback requested more/different sources" in calls[0]


def test_random_reply_feedback_does_not_trigger_followup_search(monkeypatch):
    monkeypatch.setattr("amo_bot.telegram.dispatcher.AIRouter.decide", lambda self, **kwargs: _allowing_router_decision())
    result = SimpleNamespace(allowed=True, decision="allow", reason="search_completed", text="unused", sources=("https://a.example",), hosts=("a.example",), error=None)
    d, _sent = _mk_dispatcher(result)
    calls = []

    async def _ask(prompt: str) -> str:
        calls.append(prompt)
        return "normal ai"

    d.ai_service.ask = _ask
    asyncio.run(
        d._maybe_handle_ai_autoreply(
            message=_mk_message("okay danke", reply_to_message_id=42, reply_to_message_text="Bot answer with prior context"),
            role=Role.ADMIN,
            bot_username="amo_bot",
            from_parsed_update=True,
        )
    )
    assert d.webtool_dispatcher.calls == []
    assert calls and "FOLLOW-UP AUTO-RESEARCH" not in calls[0]


def test_user_feedback_followup_extraction_failure_prompt_is_truthful(monkeypatch):
    monkeypatch.setattr("amo_bot.telegram.dispatcher.AIRouter.decide", lambda self, **kwargs: _allowing_router_decision())
    search = SimpleNamespace(allowed=True, decision="allow", reason="search_completed", text="More candidates", sources=("https://source.example/one",), hosts=("source.example",), error=None)
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
            message=_mk_message("das reicht nicht, prüfe andere Quellen", reply_to_message_id=43, reply_to_message_text="Bot answer: Keine sichere Bestätigung gefunden."),
            role=Role.ADMIN,
            bot_username="amo_bot",
            from_parsed_update=True,
        )
    )
    assert [c.capability for c in d.webtool_dispatcher.calls] == ["websearch", "webscraping", "browser"]
    assert "FOLLOW-UP AUTO-RESEARCH STATUS" in calls[0]
    assert "WEB SEARCH SUCCEEDED, FOLLOW-UP EXTRACTION UNCONFIRMED" in calls[0]
    assert "still could not confirm" in calls[0]


def test_auto_research_chain_caps_urls_browser_and_text(monkeypatch):
    monkeypatch.setattr("amo_bot.telegram.dispatcher.AIRouter.decide", lambda self, **kwargs: _allowing_router_decision())
    search = SimpleNamespace(
        allowed=True,
        decision="allow",
        reason="search_completed",
        text="stock live price",
        sources=(
            "https://one.example/a",
            "https://two.example/b",
            "https://three.example/c",
            "https://four.example/d",
            "https://five.example/e",
            "https://six.example/f",
        ),
        hosts=("one.example", "two.example", "three.example", "four.example", "five.example", "six.example"),
        error=None,
    )
    fail = SimpleNamespace(allowed=False, decision="deny", reason="empty_result", text="", sources=(), hosts=(), error=None)
    long_browser = SimpleNamespace(allowed=True, decision="allow", reason="browser_completed", text="X" * 3000, sources=("https://one.example/a",), hosts=("one.example",), error=None)
    d, _sent = _mk_sequence_dispatcher([search, fail, long_browser, fail, fail, fail, fail, fail])
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
    assert len([c for c in d.webtool_dispatcher.calls if c.capability == "browser"]) == 1
    assert calls[0].count("X") < 1700


def test_should_chain_auto_research_requires_websearch_and_current_intent():
    assert should_chain_auto_research("current USD EUR rate now", capability="websearch")
    assert should_chain_auto_research("heute neues zu Python", capability="websearch")
    assert should_chain_auto_research("aktueller Stand OpenAI Release?", capability="websearch")
    assert should_chain_auto_research("current status of OpenAI release", capability="websearch")
    assert not should_chain_auto_research("erkläre mir Python decorators", capability="websearch")
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
