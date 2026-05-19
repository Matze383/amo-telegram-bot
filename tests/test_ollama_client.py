import asyncio
from typing import Any

import httpx

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
    assert seen_payload["options"] == {"num_predict": 42}


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
    ):
        try:
            OllamaClient(base_url="http://ollama", model="m1", timeout_seconds=1.0, **kwargs)
            assert False, "expected ValueError"
        except ValueError as exc:
            text = str(exc)
            assert "must be > 0" in text or "request_endpoint must be one of: generate, chat" in text


def test_ollama_client_chat_endpoint_payload_and_response(monkeypatch) -> None:
    seen_url = ""
    seen_payload: dict[str, Any] = {}

    async def post_impl(url: str, payload: dict[str, Any]) -> httpx.Response:
        nonlocal seen_url, seen_payload
        seen_url = url
        seen_payload = payload
        req = httpx.Request("POST", url)
        return httpx.Response(200, json={"message": {"role": "assistant", "content": "chat-ok"}}, request=req)

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
    assert seen_payload["messages"] == [{"role": "user", "content": "abcde"}]
    assert seen_payload["options"] == {"num_predict": 7}


def test_ollama_client_chat_endpoint_invalid_message_shape(monkeypatch) -> None:
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
