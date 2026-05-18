import asyncio
from typing import Any

import httpx

from amo_bot.ai.ollama import OllamaClient, OllamaError, OllamaHTTPStatusError


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
