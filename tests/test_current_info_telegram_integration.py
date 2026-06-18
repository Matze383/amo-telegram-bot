from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

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


class _SlowAIService:
    async def ask(self, prompt: str, *, task_type: str | None = None) -> str:
        await asyncio.sleep(0.05)
        return "too late"


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


def _dispatcher(*, service: object, ai: object | None = None, timeout: float = 1.0):
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
    assert ai.task_types == ["answer_synthesis"]
    assert "Checked evidence" in (ai.prompts or [""])[0]
    assert "do not use prior model knowledge" in (ai.prompts or [""])[0]


def test_current_info_timeout_falls_back_without_sending() -> None:
    dispatcher, sent = _dispatcher(service=_SlowCurrentInfoService(), timeout=0.01)

    handled = asyncio.run(
        dispatcher._maybe_handle_current_info_autoreply(
            message=_message("@amo_bot aktueller Python Release heute?"),
            role=Role.ADMIN,
            normalized_text="aktueller Python Release heute?",
            locale="de",
        )
    )

    assert handled is False
    assert sent == []


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
        "Die verfügbaren geprüften Quellen reichen nicht aus, um das verlässlich zu beantworten.\n\n"
        "Geprüfte Quellen:\n"
        "1. https://www.bybit.com/en/trade/usdt/SPCXUSDT"
    ]
    assert ai.prompts is None


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
        "Die verfügbaren geprüften Quellen reichen nicht aus, um das verlässlich zu beantworten.\n\n"
        "Geprüfte Quellen:\n"
        f"1. {url}"
    ]
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
        "Die verfügbaren geprüften Quellen reichen nicht aus, um das verlässlich zu beantworten.\n\n"
        "Geprüfte Quellen:\n"
        f"1. {url}"
    ]


def test_current_info_synthesis_timeout_falls_back_without_sending() -> None:
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
    dispatcher, sent = _dispatcher(
        service=_CurrentInfoService(answer),
        ai=_SlowAIService(),
        timeout=0.01,
    )

    handled = asyncio.run(
        dispatcher._maybe_handle_current_info_autoreply(
            message=_message("@amo_bot aktueller Python Release heute?"),
            role=Role.ADMIN,
            normalized_text="aktueller Python Release heute?",
            locale="de",
        )
    )

    assert handled is False
    assert sent == []
