import asyncio
import logging
from typing import Any

import httpx
import pytest

from amo_bot.ai.ollama import OllamaClient, OllamaError, OllamaHTTPStatusError
from amo_bot.config.settings import Settings


class _DummyAsyncClient:
    def __init__(self, timeout: float, post_impl: Any) -> None:
        self.timeout = timeout
        self._post_impl = post_impl

    async def __aenter__(self) -> "_DummyAsyncClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False

    async def post(self, url: str, json: dict[str, Any]) -> Any:
        return await self._post_impl(url, json)


class _DummyStreamingResponse:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    def raise_for_status(self) -> None:
        return None

    def iter_lines(self):
        return iter(self._lines)


def _patch_async_client(monkeypatch, post_impl):
    def _factory(*, timeout: float):
        return _DummyAsyncClient(timeout=timeout, post_impl=post_impl)

    monkeypatch.setattr(httpx, "AsyncClient", _factory)


def test_ollama_client_limits_response(monkeypatch) -> None:
    async def post_impl(url: str, payload: dict[str, Any]) -> httpx.Response:
        req = httpx.Request("POST", url)
        return httpx.Response(200, json={"response": "x" * 100}, request=req)

    _patch_async_client(monkeypatch, post_impl)

    client = OllamaClient(base_url="http://ollama", model="m1", timeout_seconds=1.0, max_response_chars=10)
    out = asyncio.run(client.generate("hello"))
    assert out == "x" * 10


def test_ollama_client_sends_server_side_request_limits(monkeypatch) -> None:
    seen_payload: dict[str, Any] = {}

    async def post_impl(url: str, payload: dict[str, Any]) -> httpx.Response:
        nonlocal seen_payload
        seen_payload = payload
        req = httpx.Request("POST", url)
        return httpx.Response(200, json={"response": "ok"}, request=req)

    _patch_async_client(monkeypatch, post_impl)

    client = OllamaClient(
        base_url="http://ollama",
        model="m1",
        timeout_seconds=1.0,
        max_prompt_chars=5,
        max_predict_tokens=42,
        max_response_chars=10,
    )
    out = asyncio.run(client.generate("abcdefghij"))

    assert out == "ok"
    assert seen_payload["model"] == "m1"
    assert seen_payload["prompt"] == "abcde"
    assert seen_payload["stream"] is False
    assert seen_payload["think"] is False
    assert seen_payload["options"] == {"num_predict": 42}


def test_ollama_client_can_enable_thinking_payload(monkeypatch) -> None:
    seen_payload: dict[str, Any] = {}

    async def post_impl(url: str, payload: dict[str, Any]) -> httpx.Response:
        nonlocal seen_payload
        seen_payload = payload
        req = httpx.Request("POST", url)
        return httpx.Response(200, json={"response": "ok"}, request=req)

    _patch_async_client(monkeypatch, post_impl)

    client = OllamaClient(
        base_url="http://ollama",
        model="thinking-model",
        timeout_seconds=1.0,
        think=True,
    )
    out = asyncio.run(client.generate("hello"))

    assert out == "ok"
    assert seen_payload["model"] == "thinking-model"
    assert seen_payload["think"] is True


def test_ollama_client_http_error(monkeypatch) -> None:
    async def post_impl(url: str, payload: dict[str, Any]) -> httpx.Response:
        req = httpx.Request("POST", url)
        return httpx.Response(500, json={"error": "boom"}, request=req)

    _patch_async_client(monkeypatch, post_impl)

    client = OllamaClient(base_url="http://ollama", model="m1", timeout_seconds=1.0, max_response_chars=10)
    try:
        asyncio.run(client.generate("hello"))
        assert False, "expected OllamaError"
    except OllamaHTTPStatusError as exc:
        assert exc.status_code == 500
        assert "http 500" in str(exc)


def test_ollama_client_timeout(monkeypatch) -> None:
    async def post_impl(url: str, payload: dict[str, Any]) -> httpx.Response:
        raise httpx.TimeoutException("timeout")

    _patch_async_client(monkeypatch, post_impl)

    client = OllamaClient(base_url="http://ollama", model="m1", timeout_seconds=1.0, max_response_chars=10)
    try:
        asyncio.run(client.generate("hello"))
        assert False, "expected OllamaError"
    except OllamaError as exc:
        assert "timed out" in str(exc)


def test_ollama_client_empty_response(monkeypatch) -> None:
    async def post_impl(url: str, payload: dict[str, Any]) -> httpx.Response:
        req = httpx.Request("POST", url)
        return httpx.Response(200, json={"response": "   "}, request=req)

    _patch_async_client(monkeypatch, post_impl)

    client = OllamaClient(base_url="http://ollama", model="m1", timeout_seconds=1.0, max_response_chars=10)
    try:
        asyncio.run(client.generate("hello"))
        assert False, "expected OllamaError"
    except OllamaError as exc:
        assert "empty response" in str(exc)



def test_ollama_client_empty_generate_response_with_thinking_fails_closed(monkeypatch, caplog: pytest.LogCaptureFixture) -> None:
    async def post_impl(url: str, payload: dict[str, Any]) -> httpx.Response:
        req = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={
                "response": "",
                "thinking": "internal reasoning that must not be returned",
                "done": True,
                "done_reason": "stop",
            },
            request=req,
        )

    _patch_async_client(monkeypatch, post_impl)

    client = OllamaClient(base_url="http://ollama", model="kimi-k2.5:cloud", timeout_seconds=1.0)

    with caplog.at_level(logging.WARNING, logger="amo_bot.ai.ollama"):
        with pytest.raises(OllamaError, match="empty response"):
            asyncio.run(client.generate("hello"))

    messages = [rec.message for rec in caplog.records if rec.name == "amo_bot.ai.ollama"]
    assert any(
        "ollama empty response" in msg
        and "done_reason=stop" in msg
        and "has_thinking=True" in msg
        and "thinking_len=44" in msg
        for msg in messages
    )
    assert all("internal reasoning" not in msg for msg in messages)


def test_ollama_client_empty_chat_content_with_thinking_fails_closed(monkeypatch, caplog: pytest.LogCaptureFixture) -> None:
    async def post_impl(url: str, payload: dict[str, Any]) -> httpx.Response:
        req = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={
                "message": {"role": "assistant", "content": "", "thinking": "hidden reasoning"},
                "done": True,
                "done_reason": "stop",
            },
            request=req,
        )

    _patch_async_client(monkeypatch, post_impl)

    client = OllamaClient(base_url="http://ollama", model="kimi-k2.5:cloud", timeout_seconds=1.0, request_endpoint="chat")

    with caplog.at_level(logging.WARNING, logger="amo_bot.ai.ollama"):
        with pytest.raises(OllamaError, match="empty response"):
            asyncio.run(client.generate("hello"))

    messages = [rec.message for rec in caplog.records if rec.name == "amo_bot.ai.ollama"]
    assert any(
        "ollama empty response" in msg
        and "done_reason=stop" in msg
        and "has_message_thinking=True" in msg
        and "message_thinking_len=16" in msg
        for msg in messages
    )
    assert all("hidden reasoning" not in msg for msg in messages)

def test_ollama_client_non_dict_json_response(monkeypatch) -> None:
    async def post_impl(url: str, payload: dict[str, Any]) -> httpx.Response:
        req = httpx.Request("POST", url)
        return httpx.Response(200, json=["not", "a", "dict"], request=req)

    _patch_async_client(monkeypatch, post_impl)

    client = OllamaClient(base_url="http://ollama", model="m1", timeout_seconds=1.0, max_response_chars=10)
    try:
        asyncio.run(client.generate("hello"))
        assert False, "expected OllamaError"
    except OllamaError as exc:
        assert "invalid ollama response" in str(exc)


def test_ollama_client_missing_response_field(monkeypatch) -> None:
    async def post_impl(url: str, payload: dict[str, Any]) -> httpx.Response:
        req = httpx.Request("POST", url)
        return httpx.Response(200, json={"foo": "bar"}, request=req)

    _patch_async_client(monkeypatch, post_impl)

    client = OllamaClient(base_url="http://ollama", model="m1", timeout_seconds=1.0, max_response_chars=10)
    try:
        asyncio.run(client.generate("hello"))
        assert False, "expected OllamaError"
    except OllamaError as exc:
        assert "invalid ollama response" in str(exc)


def test_ollama_client_non_string_response_field(monkeypatch) -> None:
    async def post_impl(url: str, payload: dict[str, Any]) -> httpx.Response:
        req = httpx.Request("POST", url)
        return httpx.Response(200, json={"response": 123}, request=req)

    _patch_async_client(monkeypatch, post_impl)

    client = OllamaClient(base_url="http://ollama", model="m1", timeout_seconds=1.0, max_response_chars=10)
    try:
        asyncio.run(client.generate("hello"))
        assert False, "expected OllamaError"
    except OllamaError as exc:
        assert "invalid ollama response" in str(exc)


def test_ollama_client_rejects_invalid_limit_config() -> None:
    for kwargs in (
        {"max_prompt_chars": 0},
        {"max_prompt_chars": -1},
        {"max_predict_tokens": 0},
        {"max_predict_tokens": -5},
        {"request_endpoint": "invalid"},
        {"streaming_mode": "stream"},
    ):
        try:
            OllamaClient(base_url="http://ollama", model="m1", timeout_seconds=1.0, **kwargs)
            assert False, "expected ValueError"
        except ValueError as exc:
            text = str(exc)
            assert (
                "must be > 0" in text
                or "request_endpoint must be one of: generate, chat" in text
                or "streaming_mode must be one of: off, collect_only, live_edit" in text
            )


def test_ollama_client_chat_endpoint_payload_and_response(monkeypatch) -> None:
    seen_url = ""
    seen_payload: dict[str, Any] = {}

    async def post_impl(url: str, payload: dict[str, Any]) -> httpx.Response:
        nonlocal seen_url, seen_payload
        seen_url = url
        seen_payload = payload
        req = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={"message": {"role": "assistant", "content": "chat-ok"}, "done": True},
            request=req,
        )

    _patch_async_client(monkeypatch, post_impl)

    client = OllamaClient(
        base_url="http://ollama",
        model="m1",
        timeout_seconds=1.0,
        max_prompt_chars=5,
        max_predict_tokens=7,
        request_endpoint="chat",
    )
    out = asyncio.run(client.generate("abcdefghij"))

    assert out == "chat-ok"
    assert seen_url == "http://ollama/api/chat"
    assert seen_payload["model"] == "m1"
    assert seen_payload["stream"] is False
    assert seen_payload["think"] is False
    assert seen_payload["messages"] == [{"role": "user", "content": "abcde"}]
    assert seen_payload["options"] == {"num_predict": 7}


def test_ollama_client_chat_endpoint_missing_done_flag(monkeypatch) -> None:
    async def post_impl(url: str, payload: dict[str, Any]) -> httpx.Response:
        req = httpx.Request("POST", url)
        return httpx.Response(200, json={"message": "nope"}, request=req)

    _patch_async_client(monkeypatch, post_impl)

    client = OllamaClient(
        base_url="http://ollama",
        model="m1",
        timeout_seconds=1.0,
        request_endpoint="chat",
    )
    try:
        asyncio.run(client.generate("hello"))
        assert False, "expected OllamaError"
    except OllamaError as exc:
        assert "invalid provider response contract" in str(exc)


def test_ollama_client_collect_only_mode_does_not_enable_live_streaming(monkeypatch) -> None:
    seen_payload: dict[str, Any] = {}

    async def post_impl(url: str, payload: dict[str, Any]) -> httpx.Response:
        nonlocal seen_payload
        seen_payload = payload
        req = httpx.Request("POST", url)
        return httpx.Response(200, json={"response": "ok"}, request=req)

    _patch_async_client(monkeypatch, post_impl)

    client = OllamaClient(
        base_url="http://ollama",
        model="m1",
        timeout_seconds=1.0,
        request_endpoint="generate",
        streaming_mode="collect_only",
    )
    out = asyncio.run(client.generate("hello"))

    assert out == "ok"
    assert seen_payload["stream"] is False


def test_ollama_client_collect_only_chat_stream_pre_delta_malformed_fails_closed(monkeypatch) -> None:
    async def post_impl(url: str, payload: dict[str, Any]) -> _DummyStreamingResponse:
        assert payload["stream"] is True
        return _DummyStreamingResponse(
            [
                '{"message": {"role": "assistant", "content": "partial"}}',
                '{"message": {"role": "assistant", "content": "oops"}',
                '{"message": {"role": "assistant", "content": "final"}, "done": true}',
            ]
        )

    _patch_async_client(monkeypatch, post_impl)

    client = OllamaClient(
        base_url="http://ollama",
        model="m1",
        timeout_seconds=1.0,
        request_endpoint="chat",
        streaming_mode="collect_only",
    )

    with pytest.raises(OllamaError, match="invalid ollama response"):
        asyncio.run(client.generate("hello"))


def test_ollama_client_collect_only_chat_stream_missing_done_fails_closed(monkeypatch) -> None:
    async def post_impl(url: str, payload: dict[str, Any]) -> _DummyStreamingResponse:
        assert payload["stream"] is True
        return _DummyStreamingResponse(
            [
                '{"message": {"role": "assistant", "content": "partial-1"}}',
                '{"message": {"role": "assistant", "content": "partial-2"}}',
            ]
        )

    _patch_async_client(monkeypatch, post_impl)

    client = OllamaClient(
        base_url="http://ollama",
        model="m1",
        timeout_seconds=1.0,
        request_endpoint="chat",
        streaming_mode="collect_only",
    )

    with pytest.raises(OllamaError, match="invalid ollama response"):
        asyncio.run(client.generate("hello"))


def test_ollama_client_chat_collect_only_requests_stream_and_collects_final(monkeypatch) -> None:
    seen_payload: dict[str, Any] = {}

    async def post_impl(url: str, payload: dict[str, Any]) -> httpx.Response:
        nonlocal seen_payload
        seen_payload = payload
        req = httpx.Request("POST", url)
        body = "\n".join(
            [
                '{"message":{"role":"assistant","content":"Hel"},"done":false}',
                '{"message":{"role":"assistant","content":"lo"},"done":true}',
            ]
        )
        return httpx.Response(200, text=body, request=req)

    _patch_async_client(monkeypatch, post_impl)

    client = OllamaClient(
        base_url="http://ollama",
        model="m1",
        timeout_seconds=1.0,
        request_endpoint="chat",
        streaming_mode="collect_only",
    )
    out = asyncio.run(client.generate("hello"))

    assert seen_payload["stream"] is True
    assert out == "Hello"


def test_ollama_client_chat_live_edit_mode_reserved_non_live_behavior(monkeypatch) -> None:
    seen_payload: dict[str, Any] = {}

    async def post_impl(url: str, payload: dict[str, Any]) -> httpx.Response:
        nonlocal seen_payload
        seen_payload = payload
        req = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={"message": {"role": "assistant", "content": "ok"}, "done": True},
            request=req,
        )

    _patch_async_client(monkeypatch, post_impl)

    client = OllamaClient(
        base_url="http://ollama",
        model="m1",
        timeout_seconds=1.0,
        request_endpoint="chat",
        streaming_mode="live_edit",
    )
    out = asyncio.run(client.generate("hello"))

    assert seen_payload["stream"] is False
    assert out == "ok"


def test_ollama_client_chat_collect_only_missing_done_chunk_fails_closed(monkeypatch) -> None:
    async def post_impl(url: str, payload: dict[str, Any]) -> httpx.Response:
        req = httpx.Request("POST", url)
        body = '{"message":{"role":"assistant","content":"partial"},"done":false}'
        return httpx.Response(200, text=body, request=req)

    _patch_async_client(monkeypatch, post_impl)

    client = OllamaClient(
        base_url="http://ollama",
        model="m1",
        timeout_seconds=1.0,
        request_endpoint="chat",
        streaming_mode="collect_only",
    )
    try:
        asyncio.run(client.generate("hello"))
        assert False, "expected OllamaError"
    except OllamaError as exc:
        assert "invalid ollama response" in str(exc)


def test_ollama_client_chat_collect_only_stream_events_canonical_contract(monkeypatch) -> None:
    async def post_impl(url: str, payload: dict[str, Any]) -> httpx.Response:
        req = httpx.Request("POST", url)
        body = "\n".join(
            [
                '{"message":{"role":"assistant","content":"Hel"},"done":false}',
                '{"message":{"role":"assistant","content":"lo"},"done":false}',
                '{"message":{"role":"assistant","content":""},"done":true,"done_reason":"stop"}',
            ]
        )
        return httpx.Response(200, text=body, request=req)

    _patch_async_client(monkeypatch, post_impl)

    client = OllamaClient(
        base_url="http://ollama",
        model="m1",
        timeout_seconds=1.0,
        request_endpoint="chat",
        streaming_mode="collect_only",
        stream_phase="retry",
    )

    response = asyncio.run(post_impl("http://ollama/api/chat", {"stream": True}))
    events = client.iter_chat_stream_events(response=response, prompt_len=5, fallback_used=False)

    assert [event["event"] for event in events] == ["start", "delta", "delta", "done"]
    assert events[0]["metadata"]["live_edit_enabled"] is False
    assert events[0]["metadata"]["prompt_len"] == 5
    assert events[0]["metadata"]["phase"] == "retry"
    assert events[1]["delta"] == "Hel"
    assert events[2]["delta"] == "lo"
    assert events[3]["metadata"]["done"] is True
    assert events[3]["metadata"]["done_reason"] == "stop"
    assert events[3]["metadata"]["cancelled"] is False
    assert events[3]["metadata"]["timed_out"] is False


def test_ollama_client_chat_collect_only_stream_terminal_cancellation_maps_and_stops(monkeypatch) -> None:
    async def post_impl(url: str, payload: dict[str, Any]) -> httpx.Response:
        req = httpx.Request("POST", url)
        body = "\n".join(
            [
                '{"message":{"role":"assistant","content":"Hel"},"done":false}',
                '{"message":{"role":"assistant","content":""},"done":true,"done_reason":"cancelled"}',
                '{"message":{"role":"assistant","content":"ignored"},"done":false}',
                '{"message":{"role":"assistant","content":"still ignored"},"done":true,"done_reason":"stop"}',
            ]
        )
        return httpx.Response(200, text=body, request=req)

    _patch_async_client(monkeypatch, post_impl)

    client = OllamaClient(
        base_url="http://ollama",
        model="m1",
        timeout_seconds=1.0,
        max_response_chars=100,
        request_endpoint="chat",
        streaming_mode="collect_only",
    )

    text = asyncio.run(client.generate("p"))

    assert text == "Hel"
    events = client.last_stream_events or []
    assert [event["event"] for event in events] == ["start", "delta", "cancel"]
    assert events[2]["metadata"]["done_reason"] == "cancelled"


def test_ollama_client_chat_collect_only_stream_terminal_timeout_maps_and_stops(monkeypatch) -> None:
    async def post_impl(url: str, payload: dict[str, Any]) -> httpx.Response:
        req = httpx.Request("POST", url)
        body = "\n".join(
            [
                '{"message":{"role":"assistant","content":"Hel"},"done":false}',
                '{"message":{"role":"assistant","content":""},"done":true,"done_reason":"timeout"}',
                '{"message":{"role":"assistant","content":"ignored"},"done":false}',
            ]
        )
        return httpx.Response(200, text=body, request=req)

    _patch_async_client(monkeypatch, post_impl)

    client = OllamaClient(
        base_url="http://ollama",
        model="m1",
        timeout_seconds=1.0,
        max_response_chars=100,
        request_endpoint="chat",
        streaming_mode="collect_only",
    )

    text = asyncio.run(client.generate("p"))

    assert text == "Hel"
    events = client.last_stream_events or []
    assert [event["event"] for event in events] == ["start", "delta", "timeout"]
    assert events[2]["metadata"]["done_reason"] == "timeout"


def test_ollama_client_chat_collect_only_final_blank_uses_accumulated_deltas(monkeypatch) -> None:
    async def post_impl(url: str, payload: dict[str, Any]) -> httpx.Response:
        req = httpx.Request("POST", url)
        body = "\n".join(
            [
                '{"message":{"role":"assistant","content":"Hel"},"done":false}',
                '{"message":{"role":"assistant","content":"lo"},"done":false}',
                '{"message":{"role":"assistant","content":""},"done":true}',
            ]
        )
        return httpx.Response(200, text=body, request=req)

    _patch_async_client(monkeypatch, post_impl)

    client = OllamaClient(
        base_url="http://ollama",
        model="m1",
        timeout_seconds=1.0,
        request_endpoint="chat",
        streaming_mode="collect_only",
    )
    out = asyncio.run(client.generate("hello"))

    assert out == "Hello"


def test_ollama_client_chat_collect_only_malformed_chunk_fails_closed(monkeypatch) -> None:
    async def post_impl(url: str, payload: dict[str, Any]) -> httpx.Response:
        req = httpx.Request("POST", url)
        body = "not-json"
        return httpx.Response(200, text=body, request=req)

    _patch_async_client(monkeypatch, post_impl)

    client = OllamaClient(
        base_url="http://ollama",
        model="m1",
        timeout_seconds=1.0,
        request_endpoint="chat",
        streaming_mode="collect_only",
    )
    try:
        asyncio.run(client.generate("hello"))
        assert False, "expected OllamaError"
    except OllamaError as exc:
        assert "invalid ollama response" in str(exc)


def test_ollama_client_chat_collect_only_pre_delta_then_malformed_fails_closed(monkeypatch) -> None:
    async def post_impl(url: str, payload: dict[str, Any]) -> httpx.Response:
        req = httpx.Request("POST", url)
        body = "\n".join(
            [
                '{"message":{"role":"assistant","content":"Hel"},"done":false}',
                "{bad-json",
            ]
        )
        return httpx.Response(200, text=body, request=req)

    _patch_async_client(monkeypatch, post_impl)

    client = OllamaClient(
        base_url="http://ollama",
        model="m1",
        timeout_seconds=1.0,
        request_endpoint="chat",
        streaming_mode="collect_only",
    )
    try:
        asyncio.run(client.generate("hello"))
        assert False, "expected OllamaError"
    except OllamaError as exc:
        assert "invalid ollama response" in str(exc)


def test_ollama_client_chat_collect_only_transport_error_propagates(monkeypatch) -> None:
    async def post_impl(url: str, payload: dict[str, Any]) -> httpx.Response:
        raise httpx.ReadError("stream broke")

    _patch_async_client(monkeypatch, post_impl)

    client = OllamaClient(
        base_url="http://ollama",
        model="m1",
        timeout_seconds=1.0,
        request_endpoint="chat",
        streaming_mode="collect_only",
    )
    try:
        asyncio.run(client.generate("hello"))
        assert False, "expected OllamaError"
    except OllamaError as exc:
        assert "invalid ollama response" in str(exc)


def test_settings_rejects_invalid_ollama_limit_config() -> None:
    base = {
        "BOT_TOKEN": "token",
        "WEBUI_PASSWORD": "pw",
        "WEBUI_SECRET_KEY": "secret",
    }

    for bad_env in (
        {"OLLAMA_MAX_PROMPT_CHARS": "0"},
        {"OLLAMA_MAX_PROMPT_CHARS": "-1"},
        {"OLLAMA_MAX_PREDICT_TOKENS": "0"},
        {"OLLAMA_MAX_PREDICT_TOKENS": "-2"},
    ):
        try:
            Settings.model_validate({**base, **bad_env})
            assert False, "expected validation failure"
        except Exception as exc:
            text = str(exc)
            assert (
                "OLLAMA_MAX_PROMPT_CHARS" in text
                or "OLLAMA_MAX_PREDICT_TOKENS" in text
                or "greater than 0" in text
            )


def test_ollama_generate_with_images_places_base64_top_level_for_generate(monkeypatch, tmp_path) -> None:
    seen_payload: dict[str, Any] = {}
    image_path = tmp_path / "img.png"
    image_path.write_bytes(b"abc")

    async def post_impl(url: str, payload: dict[str, Any]) -> httpx.Response:
        nonlocal seen_payload
        seen_payload = payload
        req = httpx.Request("POST", url)
        return httpx.Response(200, json={"response": "ok"}, request=req)

    _patch_async_client(monkeypatch, post_impl)

    client = OllamaClient(base_url="http://ollama", model="vision", timeout_seconds=1.0)
    out = asyncio.run(client.generate_with_images("describe", image_paths=(str(image_path),)))

    assert out == "ok"
    assert seen_payload["images"] == ["YWJj"]
    assert "data:image" not in seen_payload["images"][0]


def test_ollama_generate_with_images_places_base64_inside_chat_message(monkeypatch, tmp_path) -> None:
    seen_payload: dict[str, Any] = {}
    image_path = tmp_path / "img.png"
    image_path.write_bytes(b"abc")

    async def post_impl(url: str, payload: dict[str, Any]) -> httpx.Response:
        nonlocal seen_payload
        seen_payload = payload
        req = httpx.Request("POST", url)
        return httpx.Response(200, json={"message": {"role": "assistant", "content": "ok"}, "done": True}, request=req)

    _patch_async_client(monkeypatch, post_impl)

    client = OllamaClient(base_url="http://ollama", model="vision", timeout_seconds=1.0, request_endpoint="chat")
    out = asyncio.run(client.generate_with_images("describe", image_paths=(str(image_path),)))

    assert out == "ok"
    assert "images" not in seen_payload
    assert seen_payload["messages"] == [{"role": "user", "content": "describe", "images": ["YWJj"]}]
