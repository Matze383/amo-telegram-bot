from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from amo_bot.telegram import dispatcher as dispatcher_module
from amo_bot.auth.roles import Role
from amo_bot.current_info import CurrentInfoAnswer, CurrentInfoRequest, EvidenceChunk, EvidencePackage
from amo_bot.telegram.dispatcher import Dispatcher
from amo_bot.telegram.update_parser import TelegramChat, TelegramMessage, TelegramUser


class _CurrentInfoService:
    def __init__(self, answer: CurrentInfoAnswer) -> None:
        self.answer_value = answer
        self.requests: list[CurrentInfoRequest] = []

    def answer(self, request: CurrentInfoRequest) -> CurrentInfoAnswer:
        self.requests.append(request)
        return self.answer_value


class _SlowCurrentInfoService:
    def answer(self, request: CurrentInfoRequest) -> CurrentInfoAnswer:
        time.sleep(0.05)
        return CurrentInfoAnswer(
            status="answered",
            answer_text="too late",
            request=request,
            sources=("https://late.example",),
        )


class _DelayedCurrentInfoService:
    def __init__(self, *, delay_seconds: float, answer_text: str = "delayed answer") -> None:
        self.delay_seconds = delay_seconds
        self.answer_text = answer_text
        self.requests: list[CurrentInfoRequest] = []

    def answer(self, request: CurrentInfoRequest) -> CurrentInfoAnswer:
        self.requests.append(request)
        time.sleep(self.delay_seconds)
        return CurrentInfoAnswer(
            status="answered",
            answer_text=self.answer_text,
            request=request,
            sources=("https://research.example/source",),
            confidence=0.8,
        )


class _SlowAIService:
    async def ask(self, prompt: str, *, task_type: str | None = None) -> str:
        await asyncio.sleep(0.05)
        return "too late"


class _FailingAIService:
    async def ask(self, prompt: str, *, task_type: str | None = None) -> str:
        raise RuntimeError("synthesis failed")


@dataclass
class _AIService:
    response: str = "Synthetisierte Antwort."
    prompts: list[str] | None = None
    task_types: list[str | None] | None = None

    async def ask(self, prompt: str, *, task_type: str | None = None) -> str:
        if self.prompts is None:
            self.prompts = []
        if self.task_types is None:
            self.task_types = []
        self.prompts.append(prompt)
        self.task_types.append(task_type)
        return self.response


def _message(text: str) -> TelegramMessage:
    return TelegramMessage(
        message_id=10,
        chat=TelegramChat(id=-100, type="supergroup", title="g", username=None),
        from_user=TelegramUser(id=123, is_bot=False, first_name="U", username="u", language_code="de"),
        text=text,
        attachments=(),
        message_thread_id=7,
        reply_to_message=None,
        reply_to_message_id=None,
        reply_to_message_text="",
        reply_to_user_id=None,
        reply_to_username="",
        reply_to_is_bot=False,
        reply_to_user_is_bot=False,
    )


def _dispatcher(
    *,
    service: object,
    ai: object | None = None,
    timeout: float = 1.0,
    research_timeout: float | None = None,
    late_synthesis_timeout: float = 1.0,
):
    sent: list[str] = []

    async def _send(chat_id: int, text: str, thread_id=None):
        sent.append(text)
        return {"message_id": 99}

    dispatcher = Dispatcher(
        command_registry=None,  # type: ignore[arg-type]
        role_resolver=None,  # type: ignore[arg-type]
        send_text=_send,
        ai_service=ai or _AIService(),
        current_info_service=service,
        current_info_enabled=True,
        current_info_timeout_seconds=timeout,
        current_info_research_timeout_seconds=timeout if research_timeout is None else research_timeout,
        current_info_late_synthesis_timeout_seconds=late_synthesis_timeout,
        current_info_max_results=4,
        current_info_max_documents=2,
    )
    return dispatcher, sent


def test_current_info_autoreply_synthesizes_and_appends_sources() -> None:
    answer = CurrentInfoAnswer(
        status="answered",
        answer_text="Raw evidence answer",
        request=CurrentInfoRequest(query="aktueller Python Release heute"),
        evidence=EvidencePackage(
            chunks=(
                EvidenceChunk(
                    text="Python 3.13.5 is the current release.",
                    source_url="https://python.example/release",
                    source_title="Python release",
                ),
            ),
            freshness="fresh",
            confidence=0.72,
        ),
        sources=("https://python.example/release",),
        confidence=0.72,
    )
    service = _CurrentInfoService(answer)
    ai = _AIService(response="Python ist aktuell bei Version 3.13.5.")
    dispatcher, sent = _dispatcher(service=service, ai=ai)

    handled = asyncio.run(
        dispatcher._maybe_handle_current_info_autoreply(
            message=_message("@amo_bot aktueller Python Release heute?"),
            role=Role.ADMIN,
            normalized_text="aktueller Python Release heute?",
            locale="de",
        )
    )

    assert handled is True
    assert sent == ["Python ist aktuell bei Version 3.13.5.\n\nQuellen:\n1. https://python.example/release"]
    assert service.requests[0].max_results == 4
    assert service.requests[0].max_documents == 2
    assert service.requests[0].role == Role.ADMIN
    assert service.requests[0].metadata["require_gpt_researcher"] is True
    assert service.requests[0].metadata["capability"] == "webresearch"
    assert "Current date:" in service.requests[0].metadata["current_time_context_text"]
    assert service.requests[0].metadata["now"].endswith("Z")
    assert service.requests[0].metadata["timezone"] == "Europe/Berlin"
    assert ai.task_types == ["answer_synthesis"]
    assert "Checked evidence" in (ai.prompts or [""])[0]
    assert "Source class: verified_external_evidence" in (ai.prompts or [""])[0]
    assert (
        "Do not treat user claims, prior bot answers, topic summaries, semantic memory, or model prior as evidence"
        in (ai.prompts or [""])[0]
    )
    assert "do not use prior model knowledge" in (ai.prompts or [""])[0]


def test_current_info_synthesis_prompt_marks_past_planned_dates_as_past() -> None:
    answer = CurrentInfoAnswer(
        status="answered",
        answer_text="SpaceX IPO ist fuer den 12. Juni 2026 geplant.",
        request=CurrentInfoRequest(
            query="Ist SpaceX schon boersennotiert?",
            metadata={
                "current_time_context_text": "\n".join(
                    (
                        "Context:",
                        "Current date: 2026-06-26",
                        "Timezone: Europe/Berlin",
                        "Local timestamp: 2026-06-26T12:00:00+02:00",
                        "UTC timestamp: 2026-06-26T10:00:00Z",
                    )
                ),
                "timezone": "Europe/Berlin",
            },
        ),
        evidence=EvidencePackage(
            chunks=(
                EvidenceChunk(
                    text="Die Quellen nennen einen geplanten SpaceX IPO am 12. Juni 2026 an der Nasdaq.",
                    source_url="https://finance.example/spacex-ipo",
                    source_title="SpaceX IPO",
                ),
            ),
            freshness="current",
            confidence=0.78,
        ),
        sources=("https://finance.example/spacex-ipo",),
        confidence=0.78,
    )

    prompt = Dispatcher._current_info_synthesis_prompt(answer=answer, locale="de")

    assert "Current date: 2026-06-26" in prompt
    assert "Timezone: Europe/Berlin" in prompt
    assert "12. Juni 2026" in prompt
    assert "already passed" in prompt
    assert "do not describe it as future, planned, upcoming, or 'bis dahin'" in prompt


def test_current_info_final_answer_rewrites_expired_planned_date_future_wording() -> None:
    answer = CurrentInfoAnswer(
        status="answered",
        answer_text="",
        request=CurrentInfoRequest(
            query="Ist SpaceX schon boersennotiert?",
            metadata={"now": "2026-06-26T10:00:00Z", "timezone": "Europe/Berlin"},
        ),
        sources=("https://finance.example/spacex-ipo",),
        confidence=0.78,
    )
    synthesized = (
        "Laut den verfügbaren Quellen ist SpaceX noch nicht börsennotiert. "
        "Ein Börsengang ist für den 12. Juni 2026 an der Nasdaq unter dem Ticker SPCX geplant, "
        "aber bis dahin ist die Aktie noch nicht direkt handelbar."
    )

    text = Dispatcher._format_current_info_telegram_answer(answer=answer, synthesized=synthesized, locale="de")

    body = text.split("\n\nQuellen:", 1)[0]
    assert "bis dahin" not in body.casefold()
    assert "ist für den 12. juni 2026" not in body.casefold()
    assert "wurde für den 12. Juni 2026" in body
    assert "Quellen:\n1. https://finance.example/spacex-ipo" in text


def test_current_info_timeout_fails_closed_without_late_search_or_ai_fallback() -> None:
    dispatcher, sent = _dispatcher(
        service=_SlowCurrentInfoService(),
        ai=_AIService(response="Late synthesized answer."),
        timeout=0.01,
    )

    async def _run() -> bool:
        handled = await dispatcher._maybe_handle_current_info_autoreply(
            message=_message("@amo_bot aktueller Python Release heute?"),
            role=Role.ADMIN,
            normalized_text="aktueller Python Release heute?",
            locale="de",
        )
        await asyncio.sleep(0.08)
        return handled

    handled = asyncio.run(_run())

    assert handled is True
    assert sent == [
        "Dafuer brauche ich GPT-Researcher-Webrecherche, aber die Recherche konnte gerade nicht erfolgreich abgeschlossen werden."
    ]


def test_current_info_gpt_researcher_auto_path_uses_longer_research_timeout() -> None:
    service = _DelayedCurrentInfoService(delay_seconds=0.03, answer_text="GPT-Researcher result")
    dispatcher, sent = _dispatcher(
        service=service,
        ai=_AIService(response="Synthetisierte Research-Antwort."),
        timeout=0.01,
        research_timeout=0.2,
    )

    handled = asyncio.run(
        dispatcher._maybe_handle_current_info_autoreply(
            message=_message("@amo_bot Welche Änderungen gab es heute in der OpenAI API?"),
            role=Role.ADMIN,
            normalized_text="Welche Änderungen gab es heute in der OpenAI API?",
            locale="de",
        )
    )

    assert handled is True
    assert sent == ["Synthetisierte Research-Antwort.\n\nQuellen:\n1. https://research.example/source"]
    assert len(service.requests) == 1
    assert service.requests[0].metadata["require_gpt_researcher"] is True
    assert service.requests[0].metadata["capability"] == "webresearch"


def test_current_info_synthesis_timeout_sends_compact_answer_with_sources() -> None:
    answer = CurrentInfoAnswer(
        status="answered",
        answer_text="Raw GPT-Researcher answer from checked evidence.",
        request=CurrentInfoRequest(query="aktueller Python Release heute"),
        evidence=EvidencePackage(
            chunks=(
                EvidenceChunk(
                    text="Python 3.13.5 is the current release.",
                    source_url="https://python.example/release",
                    source_title="Python release",
                ),
            ),
            freshness="fresh",
            confidence=0.72,
        ),
        sources=("https://python.example/release",),
        confidence=0.72,
    )
    dispatcher, sent = _dispatcher(
        service=_CurrentInfoService(answer),
        ai=_SlowAIService(),
        timeout=1.0,
        late_synthesis_timeout=0.01,
    )

    async def _run() -> bool:
        handled = await dispatcher._maybe_handle_current_info_autoreply(
            message=_message("@amo_bot aktueller Python Release heute?"),
            role=Role.ADMIN,
            normalized_text="aktueller Python Release heute?",
            locale="de",
        )
        await asyncio.sleep(0.12)
        return handled

    handled = asyncio.run(_run())

    assert handled is True
    assert sent == ["Raw GPT-Researcher answer from checked evidence.\n\nQuellen:\n1. https://python.example/release"]
    assert "Recherche konnte gerade nicht erfolgreich abgeschlossen werden" not in sent[0]


def test_current_info_synthesis_exception_sends_compact_answer_with_sources() -> None:
    answer = CurrentInfoAnswer(
        status="answered",
        answer_text="Raw GPT-Researcher answer after AI synthesis failed.",
        request=CurrentInfoRequest(query="aktueller Python Release heute"),
        evidence=EvidencePackage(
            chunks=(
                EvidenceChunk(
                    text="Python 3.13.5 is the current release.",
                    source_url="https://python.example/release",
                    source_title="Python release",
                ),
            ),
            freshness="fresh",
            confidence=0.72,
        ),
        sources=("https://python.example/release",),
        confidence=0.72,
    )
    dispatcher, sent = _dispatcher(
        service=_CurrentInfoService(answer),
        ai=_FailingAIService(),
    )

    handled = asyncio.run(
        dispatcher._maybe_handle_current_info_autoreply(
            message=_message("@amo_bot aktueller Python Release heute?"),
            role=Role.ADMIN,
            normalized_text="aktueller Python Release heute?",
            locale="de",
        )
    )

    assert handled is True
    assert sent == [
        "Raw GPT-Researcher answer after AI synthesis failed.\n\nQuellen:\n1. https://python.example/release"
    ]
    assert "Recherche konnte gerade nicht erfolgreich abgeschlossen werden" not in sent[0]


def test_current_info_synthesis_timeout_without_sources_still_fails_closed() -> None:
    answer = CurrentInfoAnswer(
        status="answered",
        answer_text="Raw answer without sources.",
        request=CurrentInfoRequest(query="aktueller Python Release heute"),
        evidence=EvidencePackage(
            chunks=(
                EvidenceChunk(
                    text="Python 3.13.5 is the current release.",
                    source_url="https://python.example/release",
                    source_title="Python release",
                ),
            ),
            freshness="fresh",
            confidence=0.72,
        ),
        sources=(),
        confidence=0.72,
    )
    dispatcher, sent = _dispatcher(
        service=_CurrentInfoService(answer),
        ai=_SlowAIService(),
        timeout=1.0,
        late_synthesis_timeout=0.01,
    )

    handled = asyncio.run(
        dispatcher._maybe_handle_current_info_autoreply(
            message=_message("@amo_bot aktueller Python Release heute?"),
            role=Role.ADMIN,
            normalized_text="aktueller Python Release heute?",
            locale="de",
        )
    )

    assert handled is True
    assert sent == [
        "Dafuer brauche ich GPT-Researcher-Webrecherche, aber die Recherche konnte gerade nicht erfolgreich abgeschlossen werden."
    ]


def test_current_info_synthesis_timeout_without_evidence_still_fails_closed() -> None:
    answer = CurrentInfoAnswer(
        status="answered",
        answer_text="Raw answer without evidence.",
        request=CurrentInfoRequest(query="aktueller Python Release heute"),
        evidence=None,
        sources=("https://python.example/release",),
        confidence=0.72,
    )
    dispatcher, sent = _dispatcher(
        service=_CurrentInfoService(answer),
        ai=_SlowAIService(),
        timeout=1.0,
        late_synthesis_timeout=0.01,
    )

    handled = asyncio.run(
        dispatcher._maybe_handle_current_info_autoreply(
            message=_message("@amo_bot aktueller Python Release heute?"),
            role=Role.ADMIN,
            normalized_text="aktueller Python Release heute?",
            locale="de",
        )
    )

    assert handled is True
    assert sent == [
        "Dafuer brauche ich GPT-Researcher-Webrecherche, aber die Recherche konnte gerade nicht erfolgreich abgeschlossen werden."
    ]


def test_current_info_compact_synthesis_timeout_applies_expired_planned_date_guard() -> None:
    answer = CurrentInfoAnswer(
        status="answered",
        answer_text=(
            "Laut Quellen ist SpaceX noch nicht börsennotiert. "
            "Ein Börsengang ist für den 12. Juni 2026 an der Nasdaq unter dem Ticker SPCX geplant, "
            "aber bis dahin ist die Aktie nicht direkt handelbar."
        ),
        request=CurrentInfoRequest(
            query="Ist SpaceX schon boersennotiert?",
            metadata={"now": "2026-06-26T10:00:00Z", "timezone": "Europe/Berlin"},
        ),
        evidence=EvidencePackage(
            chunks=(
                EvidenceChunk(
                    text="Die Quellen nennen einen geplanten SpaceX IPO am 12. Juni 2026 an der Nasdaq.",
                    source_url="https://finance.example/spacex-ipo",
                    source_title="SpaceX IPO",
                ),
            ),
            freshness="current",
            confidence=0.78,
        ),
        sources=("https://finance.example/spacex-ipo",),
        confidence=0.78,
    )
    dispatcher, sent = _dispatcher(
        service=_CurrentInfoService(answer),
        ai=_SlowAIService(),
        timeout=1.0,
        late_synthesis_timeout=0.01,
    )

    handled = asyncio.run(
        dispatcher._maybe_handle_current_info_autoreply(
            message=_message("@amo_bot Ist SpaceX schon boersennotiert?"),
            role=Role.ADMIN,
            normalized_text="Ist SpaceX schon boersennotiert?",
            locale="de",
        )
    )

    assert handled is True
    body = sent[0].split("\n\nQuellen:", 1)[0]
    assert "bis dahin" not in body.casefold()
    assert "ist für den 12. juni 2026" not in body.casefold()
    assert "wurde für den 12. Juni 2026" in body
    assert "Quellen:\n1. https://finance.example/spacex-ipo" in sent[0]


def test_current_info_unverified_evidence_sends_insufficient_answer_without_synthesis() -> None:
    answer = CurrentInfoAnswer(
        status="unverified_evidence",
        answer_text="",
        request=CurrentInfoRequest(query="Ist SpaceX börsennotiert oder SPCXUSDT ein Derivat?"),
        sources=("https://www.bybit.com/en/trade/usdt/SPCXUSDT",),
        warnings=("needs_independent_source", "finance_listing_requires_verified_sources"),
        confidence=0.58,
    )
    service = _CurrentInfoService(answer)
    ai = _AIService(response="SpaceX ist sicher nicht börsennotiert.")
    dispatcher, sent = _dispatcher(service=service, ai=ai)

    handled = asyncio.run(
        dispatcher._maybe_handle_current_info_autoreply(
            message=_message("@amo_bot Ist SpaceX börsennotiert oder SPCXUSDT ein Derivat?"),
            role=Role.ADMIN,
            normalized_text="Ist SpaceX börsennotiert oder SPCXUSDT ein Derivat?",
            locale="de",
        )
    )

    assert handled is True
    assert sent == [
        "Die verfügbaren Quellen und Kandidaten reichen nicht aus, um das verlässlich zu beantworten.\n\n"
        "Berücksichtigte Quellen/Kandidaten:\n"
        "1. https://www.bybit.com/en/trade/usdt/SPCXUSDT"
    ]
    assert "Geprüfte Quellen" not in sent[0]
    assert ai.prompts is None


def test_current_info_insufficient_answer_labels_sources_as_candidates_in_english() -> None:
    answer = CurrentInfoAnswer(
        status="unverified_evidence",
        answer_text="",
        request=CurrentInfoRequest(query="current listing status?"),
        sources=("https://search.example/result",),
        warnings=("snippet_only_evidence",),
    )

    text = Dispatcher._format_current_info_insufficient_answer(answer=answer, locale="en")

    assert text == (
        "The available sources and candidates are not sufficient to answer this reliably.\n\n"
        "Sources/candidates considered:\n"
        "1. https://search.example/result"
    )
    assert "Sources checked" not in text
    assert "checked sources" not in text


def test_current_info_websearch_signal_with_service_disabled_fails_closed_without_ai_fallback() -> None:
    dispatcher, sent = _dispatcher(service=_CurrentInfoService(CurrentInfoAnswer(status="answered")), ai=_AIService())
    dispatcher.current_info_enabled = False

    handled = asyncio.run(
        dispatcher._maybe_handle_current_info_autoreply(
            message=_message("@amo_bot aktueller Python Release heute?"),
            role=Role.ADMIN,
            normalized_text="aktueller Python Release heute?",
            locale="de",
            force=False,
        )
    )

    assert handled is True
    assert sent == [
        "Dafuer brauche ich aktuelle Recherche, aber Current-Info ist gerade nicht verfuegbar oder nicht konfiguriert."
    ]


def test_current_info_mutable_fact_queries_are_marked_gpt_researcher_only() -> None:
    answer = CurrentInfoAnswer(
        status="answered",
        answer_text="Research answer.",
        sources=("https://research.example/source",),
        confidence=0.8,
    )
    service = _CurrentInfoService(answer)
    dispatcher, sent = _dispatcher(service=service, ai=_AIService(response="Synthesis."))

    queries = (
        "Welche Firmen gehören aktuell zu OpenAI und Microsoft?",
        "Welche Änderungen gab es in der Stripe API?",
        "Was kostet ChatGPT Pro heute?",
        "Was sind die neuesten News zu Nvidia?",
        "Wer ist aktuell CEO von OpenAI?",
    )
    for index, query in enumerate(queries, start=1):
        handled = asyncio.run(
            dispatcher._maybe_handle_current_info_autoreply(
                message=_message(f"@amo_bot {query}"),
                role=Role.ADMIN,
                normalized_text=query,
                locale="de",
                force=True,
                strategy_reason="semantic_current_data_required",
            )
        )
        assert handled is True, query
        assert service.requests[index - 1].metadata["require_gpt_researcher"] is True
        assert service.requests[index - 1].metadata["capability"] == "webresearch"

    assert len(service.requests) == len(queries)
    assert len(sent) == len(queries)


def test_current_info_autoreply_accepts_spacex_listing_url_as_user_evidence() -> None:
    url = (
        "https://www.reutersconnect.com/item/"
        "spacexs-initial-public-offering-ipo-at-the-nasdaq-marketsite-in-new-york-city/"
        "dGFnOnJldXRlcnMuY29tLDIwMjY6bmV3c21sX1JDMktTTEFSWE05Vw"
    )
    answer = CurrentInfoAnswer(
        status="unverified_evidence",
        answer_text="",
        request=CurrentInfoRequest(query=f"Ist SpaceX an der Börse? Quelle: {url}"),
        sources=(url,),
        warnings=("finance_listing_requires_verified_sources",),
        confidence=0.58,
    )
    service = _CurrentInfoService(answer)
    ai = _AIService(response="SpaceX ist sicher börsennotiert.")
    dispatcher, sent = _dispatcher(service=service, ai=ai)

    handled = asyncio.run(
        dispatcher._maybe_handle_current_info_autoreply(
            message=_message(f"@amo_bot Ist SpaceX an der Börse? Quelle: {url}"),
            role=Role.ADMIN,
            normalized_text=f"Ist SpaceX an der Börse? Quelle: {url}",
            locale="de",
        )
    )

    assert handled is True
    assert service.requests[0].domain_hint == "stock"
    assert service.requests[0].query == f"Ist SpaceX an der Börse? Quelle: {url}"
    assert sent == [
        "Die verfügbaren Quellen und Kandidaten reichen nicht aus, um das verlässlich zu beantworten.\n\n"
        "Berücksichtigte Quellen/Kandidaten:\n"
        f"1. {url}"
    ]
    assert "Geprüfte Quellen" not in sent[0]
    assert ai.prompts is None


def test_current_info_autoreply_keeps_full_long_url_in_request() -> None:
    url = "https://example.com/research/" + "very-long-path-segment-" * 18 + "final"
    prompt = f"@amo_bot Bitte prüfe diese Quelle und sag, ob sie belastbar ist: {url}"
    answer = CurrentInfoAnswer(
        status="answered",
        answer_text="Die Quelle wurde geprüft.",
        request=CurrentInfoRequest(query=prompt),
        sources=(url,),
        confidence=0.72,
    )
    service = _CurrentInfoService(answer)
    dispatcher, sent = _dispatcher(service=service, ai=_AIService(response="Die Quelle wurde geprüft."))

    handled = asyncio.run(
        dispatcher._maybe_handle_current_info_autoreply(
            message=_message(prompt),
            role=Role.ADMIN,
            normalized_text=prompt,
            locale="de",
        )
    )

    assert handled is True
    assert service.requests[0].query == prompt
    assert url in service.requests[0].query
    assert service.requests[0].metadata["direct_url"] == url
    assert service.requests[0].metadata["capability"] == "webresearch"
    assert service.requests[0].metadata["requested_capability"] == "browser"
    assert sent == [f"Die Quelle wurde geprüft.\n\nQuellen:\n1. {url}"]


def test_current_info_autoreply_preserves_long_finance_listing_url() -> None:
    url = (
        "https://www.reutersconnect.com/item/"
        "spacexs-initial-public-offering-ipo-at-the-nasdaq-marketsite-in-new-york-city/"
        "dGFnOnJldXRlcnMuY29tLDIwMjY6bmV3c21sX1JDMktTTEFSWE05Vw"
    )
    filler = " ".join(["bitte sehr genau prüfen"] * 20)
    normalized = f"Ist SpaceX an der Börse? {filler} Quelle: {url}"
    answer = CurrentInfoAnswer(
        status="unverified_evidence",
        answer_text="",
        request=CurrentInfoRequest(query=normalized),
        sources=(url,),
        warnings=("finance_listing_requires_verified_sources",),
        confidence=0.58,
    )
    service = _CurrentInfoService(answer)
    dispatcher, sent = _dispatcher(service=service, ai=_AIService(response="SpaceX ist sicher börsennotiert."))

    handled = asyncio.run(
        dispatcher._maybe_handle_current_info_autoreply(
            message=_message(f"@amo_bot {normalized}"),
            role=Role.ADMIN,
            normalized_text=normalized,
            locale="de",
        )
    )

    assert handled is True
    assert service.requests[0].query == normalized
    assert service.requests[0].metadata["direct_url"] == url
    assert url in service.requests[0].query
    assert sent == [
        "Die verfügbaren Quellen und Kandidaten reichen nicht aus, um das verlässlich zu beantworten.\n\n"
        "Berücksichtigte Quellen/Kandidaten:\n"
        f"1. {url}"
    ]
    assert "Geprüfte Quellen" not in sent[0]


def test_current_info_synthesizes_with_separate_budget_after_research_budget_elapsed(monkeypatch) -> None:
    times = iter((100.0, 400.5))

    def _perf_counter() -> float:
        return next(times, 400.5)

    monkeypatch.setattr(dispatcher_module.time, "perf_counter", _perf_counter)
    answer = CurrentInfoAnswer(
        status="answered",
        answer_text="Raw evidence answer",
        request=CurrentInfoRequest(query="aktueller Python Release heute"),
        evidence=EvidencePackage(
            chunks=(
                EvidenceChunk(
                    text="Python 3.13.5 is the current release.",
                    source_url="https://python.example/release",
                    source_title="Python release",
                ),
            ),
            freshness="fresh",
            confidence=0.72,
        ),
        sources=("https://python.example/release",),
        confidence=0.72,
    )
    ai = _AIService(response="Python ist aktuell bei Version 3.13.5.")
    dispatcher, sent = _dispatcher(
        service=_CurrentInfoService(answer),
        ai=ai,
        timeout=300,
        research_timeout=300,
        late_synthesis_timeout=0.1,
    )

    handled = asyncio.run(
        dispatcher._maybe_handle_current_info_autoreply(
            message=_message("@amo_bot aktueller Python Release heute?"),
            role=Role.ADMIN,
            normalized_text="aktueller Python Release heute?",
            locale="de",
        )
    )

    assert handled is True
    assert sent == ["Python ist aktuell bei Version 3.13.5.\n\nQuellen:\n1. https://python.example/release"]
    assert ai.task_types == ["answer_synthesis"]
