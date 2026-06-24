from __future__ import annotations

import asyncio

import httpx

from amo_bot.telegram.client import TelegramClient
from amo_bot.telegram.outbound_text import TELEGRAM_SAFE_MESSAGE_LIMIT, html_chunk_is_balanced, split_telegram_message_text


class _DummyResponse:
    status_code = 200

    @staticmethod
    def json() -> dict[str, object]:
        return {"ok": True, "result": []}


class _CaptureClient:
    def __init__(self, *, timeout, seen: list[object]) -> None:  # noqa: ANN001
        self._seen = seen
        self._timeout = timeout

    async def __aenter__(self):
        self._seen.append(self._timeout)
        return self

    async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001
        return None

    async def post(self, url: str, json: dict[str, object]) -> _DummyResponse:  # noqa: ARG002
        return _DummyResponse()


def test_get_updates_uses_read_timeout_above_poll_timeout(monkeypatch) -> None:
    seen: list[object] = []

    def _factory(*, timeout):  # noqa: ANN001
        return _CaptureClient(timeout=timeout, seen=seen)

    monkeypatch.setattr(httpx, "AsyncClient", _factory)

    client = TelegramClient(token="t", timeout_seconds=30.0)
    asyncio.run(client.get_updates(offset=0, timeout=30, limit=1))

    assert len(seen) == 1
    timeout = seen[0]
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.read == 40.0


def test_non_poll_calls_keep_shared_timeout_float(monkeypatch) -> None:
    seen: list[object] = []

    def _factory(*, timeout):  # noqa: ANN001
        return _CaptureClient(timeout=timeout, seen=seen)

    monkeypatch.setattr(httpx, "AsyncClient", _factory)

    client = TelegramClient(token="t", timeout_seconds=7.0)
    asyncio.run(client.send_message(chat_id=1, text="hello"))

    assert seen == [7.0]


def test_get_chat_member_calls_telegram_api(monkeypatch) -> None:
    seen: list[dict[str, object]] = []

    class _MemberResponse:
        status_code = 200

        @staticmethod
        def json() -> dict[str, object]:
            return {"ok": True, "result": {"status": "administrator", "user": {"id": 42}}}

    class _CaptureMemberClient:
        def __init__(self, *, timeout) -> None:  # noqa: ANN001
            self._timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001
            return None

        async def post(self, url: str, json: dict[str, object]) -> _MemberResponse:  # noqa: ARG002
            seen.append(dict(json))
            return _MemberResponse()

    def _factory(*, timeout):  # noqa: ANN001, ARG001
        return _CaptureMemberClient(timeout=timeout)

    monkeypatch.setattr(httpx, "AsyncClient", _factory)

    client = TelegramClient(token="t")
    result = asyncio.run(client.get_chat_member(chat_id=-100, user_id=42))

    assert seen == [{"chat_id": -100, "user_id": 42}]
    assert result["status"] == "administrator"


class _MessageResponse:
    status_code = 200

    def __init__(self, message_id: int) -> None:
        self._message_id = message_id

    def json(self) -> dict[str, object]:
        return {"ok": True, "result": {"message_id": self._message_id}}


class _CaptureMessageClient:
    def __init__(self, *, seen: list[dict[str, object]]) -> None:
        self._seen = seen

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001
        return None

    async def post(self, url: str, json: dict[str, object]) -> _MessageResponse:  # noqa: ARG002
        self._seen.append(dict(json))
        return _MessageResponse(len(self._seen))


def test_send_message_splits_long_text_without_truncating(monkeypatch) -> None:
    seen: list[dict[str, object]] = []

    def _factory(*, timeout):  # noqa: ANN001, ARG001
        return _CaptureMessageClient(seen=seen)

    monkeypatch.setattr(httpx, "AsyncClient", _factory)

    text = "\n".join(f"line {idx} " + ("x" * 80) for idx in range(120))
    client = TelegramClient(token="t")
    result = asyncio.run(client.send_message(chat_id=1, text=text, message_thread_id=22))

    assert result == {"message_id": 1}
    assert len(seen) > 1
    assert all(len(str(payload["text"])) <= TELEGRAM_SAFE_MESSAGE_LIMIT for payload in seen)
    assert "".join(str(payload["text"]) for payload in seen) == text
    assert {payload["message_thread_id"] for payload in seen} == {22}


def test_split_telegram_message_text_keeps_4000_boundary_unsplit() -> None:
    text = "x" * TELEGRAM_SAFE_MESSAGE_LIMIT

    chunks = split_telegram_message_text(text)

    assert chunks == [text]


def test_split_telegram_message_text_splits_just_above_4000() -> None:
    text = ("x" * TELEGRAM_SAFE_MESSAGE_LIMIT) + "y"

    chunks = split_telegram_message_text(text)

    assert len(chunks) == 2
    assert all(len(chunk) <= TELEGRAM_SAFE_MESSAGE_LIMIT for chunk in chunks)
    assert "".join(chunks) == text


def test_split_telegram_message_text_prefers_newline_then_space_boundaries() -> None:
    newline_text = ("a" * 30) + "\n" + ("b" * 30)
    space_text = ("a" * 30) + " " + ("b" * 30)

    newline_chunks = split_telegram_message_text(newline_text, limit=40)
    space_chunks = split_telegram_message_text(space_text, limit=40)

    assert newline_chunks == [("a" * 30) + "\n", "b" * 30]
    assert space_chunks == [("a" * 30) + " ", "b" * 30]


def test_split_telegram_message_text_falls_back_for_long_unbroken_token() -> None:
    text = "x" * (TELEGRAM_SAFE_MESSAGE_LIMIT * 2 + 17)

    chunks = split_telegram_message_text(text)

    assert len(chunks) == 3
    assert [len(chunk) for chunk in chunks] == [
        TELEGRAM_SAFE_MESSAGE_LIMIT,
        TELEGRAM_SAFE_MESSAGE_LIMIT,
        17,
    ]
    assert "".join(chunks) == text


def test_send_message_keeps_reply_and_markup_on_first_split_chunk_only(monkeypatch) -> None:
    seen: list[dict[str, object]] = []

    def _factory(*, timeout):  # noqa: ANN001, ARG001
        return _CaptureMessageClient(seen=seen)

    monkeypatch.setattr(httpx, "AsyncClient", _factory)

    client = TelegramClient(token="t")
    asyncio.run(
        client.send_message(
            chat_id=1,
            text="word " * 1000,
            reply_to_message_id=99,
            message_thread_id=22,
            reply_markup={"inline_keyboard": []},
        )
    )

    assert len(seen) == 2
    assert seen[0]["reply_to_message_id"] == 99
    assert seen[0]["reply_markup"] == {"inline_keyboard": []}
    assert "reply_to_message_id" not in seen[1]
    assert "reply_markup" not in seen[1]
    assert seen[1]["message_thread_id"] == 22


def test_send_message_applies_parse_mode_to_every_split_chunk(monkeypatch) -> None:
    seen: list[dict[str, object]] = []

    def _factory(*, timeout):  # noqa: ANN001, ARG001
        return _CaptureMessageClient(seen=seen)

    monkeypatch.setattr(httpx, "AsyncClient", _factory)

    client = TelegramClient(token="t")
    asyncio.run(client.send_message(chat_id=1, text="<b>" + ("bold " * 900) + "</b>", parse_mode="HTML"))

    assert len(seen) > 1
    assert {payload["parse_mode"] for payload in seen} == {"HTML"}
    assert all(len(str(payload["text"])) <= TELEGRAM_SAFE_MESSAGE_LIMIT for payload in seen)
    assert all(html_chunk_is_balanced(str(payload["text"])) for payload in seen)


def test_split_telegram_message_text_prefers_balanced_html_boundaries() -> None:
    text = "<b>" + ("bold " * 6) + "</b>\n<i>" + ("italic " * 6) + "</i>"

    chunks = split_telegram_message_text(text, limit=50, parse_mode="HTML")

    assert len(chunks) == 2
    assert chunks[0].startswith("<b>")
    assert chunks[0].rstrip().endswith("</b>")
    assert chunks[1].startswith("<i>")
    assert chunks[1].endswith("</i>")
    assert all(len(chunk) <= 50 for chunk in chunks)
    assert all(html_chunk_is_balanced(chunk) for chunk in chunks)


def test_split_telegram_html_reopens_tags_and_preserves_entities() -> None:
    text = "<b>" + ("alpha &amp; beta " * 12) + "</b>"

    chunks = split_telegram_message_text(text, limit=60, parse_mode="HTML")

    assert len(chunks) > 1
    assert all(len(chunk) <= 60 for chunk in chunks)
    assert all(html_chunk_is_balanced(chunk) for chunk in chunks)
    assert all("&" not in chunk or "&amp;" in chunk for chunk in chunks)
    assert "".join(chunk.replace("<b>", "").replace("</b>", "") for chunk in chunks) == "alpha &amp; beta " * 12


def test_split_telegram_message_text_prefers_closed_markdown_fence_boundary() -> None:
    text = "intro\n```\ncode\n```\n" + ("outro " * 30)

    chunks = split_telegram_message_text(text, limit=30, parse_mode="Markdown")

    assert len(chunks) > 1
    assert chunks[0] == "intro\n```\ncode\n```\n"
    assert "".join(chunks) == text


def test_split_telegram_message_text_keeps_long_markdown_fences_safe() -> None:
    text = "intro\n```\n" + ("value = `literal` * 2\n" * 260) + "```\noutro"

    for parse_mode in ("Markdown", "MarkdownV2"):
        chunks = split_telegram_message_text(text, limit=4000, parse_mode=parse_mode)

        assert len(chunks) > 1
        assert all(len(chunk) <= 4000 for chunk in chunks)
        assert all(_markdown_fence_chunk_is_safe(chunk) for chunk in chunks)
        assert "value = `literal` * 2" in "".join(chunks)


def test_split_telegram_message_text_does_not_cut_partial_markdown_fence_marker() -> None:
    text = ("x" * 28) + "```\ncode\n```"

    chunks = split_telegram_message_text(text, limit=30, parse_mode="MarkdownV2")

    assert len(chunks) == 2
    assert chunks[0] == "x" * 28
    assert chunks[1] == "```\ncode\n```"
    assert all(_markdown_fence_chunk_is_safe(chunk) for chunk in chunks)


def _markdown_fence_chunk_is_safe(chunk: str) -> bool:
    stripped = chunk.rstrip()
    trailing_backticks = len(stripped) - len(stripped.rstrip("`"))
    return trailing_backticks not in {1, 2} and stripped.count("```") % 2 == 0
