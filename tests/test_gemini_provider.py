from __future__ import annotations

import asyncio
import json
from urllib import error

import pytest

from amo_bot.ai.gemini_provider import GeminiProviderConfig, GeminiProviderError, GeminiRequestClient


class _Response:
    def __init__(self, status: int = 200, payload: dict | None = None) -> None:
        self.status = status
        self._payload = payload or {}

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def _config() -> GeminiProviderConfig:
    return GeminiProviderConfig(
        api_key="gk-test",
        model="google/gemini-3-flash-preview",
        timeout_seconds=3.0,
        base_url="https://generativelanguage.googleapis.com",
    )


def test_gemini_request_client_success(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["content_type"] = req.headers.get("Content-type")
        captured["headers"] = dict(req.headers)
        captured["timeout"] = timeout
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        return _Response(payload={"candidates": [{"content": {"parts": [{"text": "  hello  "}]}}]})

    monkeypatch.setattr("amo_bot.ai.gemini_provider.request.urlopen", fake_urlopen)

    client = GeminiRequestClient(config=_config())
    result = asyncio.run(client.ask("hi"))

    assert result == "hello"
    assert "key=" not in captured["url"]
    assert (
        captured["url"]
        == "https://generativelanguage.googleapis.com/v1beta/models/gemini-3-flash-preview:generateContent"
    )
    assert captured["content_type"] == "application/json"
    assert captured["headers"]["X-goog-api-key"] == "gk-test"
    assert captured["timeout"] == 3.0
    assert captured["payload"] == {
        "contents": [{"role": "user", "parts": [{"text": "hi"}]}],
    }


def test_gemini_request_client_missing_key() -> None:
    client = GeminiRequestClient(
        config=GeminiProviderConfig(
            api_key="", model="google/gemini-3-flash-preview", timeout_seconds=1.0, base_url="https://generativelanguage.googleapis.com"
        )
    )
    with pytest.raises(GeminiProviderError, match="api key missing"):
        asyncio.run(client.ask("hi"))


@pytest.mark.parametrize("status", [401, 403])
def test_gemini_request_client_auth_error(monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    def fake_urlopen(req, timeout):
        raise error.HTTPError(req.full_url, status, "Auth", {}, None)

    monkeypatch.setattr("amo_bot.ai.gemini_provider.request.urlopen", fake_urlopen)
    client = GeminiRequestClient(config=_config())

    with pytest.raises(GeminiProviderError, match="auth error"):
        asyncio.run(client.ask("hi"))


def test_gemini_request_client_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req, timeout):
        raise error.HTTPError(req.full_url, 429, "Too Many Requests", {}, None)

    monkeypatch.setattr("amo_bot.ai.gemini_provider.request.urlopen", fake_urlopen)
    client = GeminiRequestClient(config=_config())

    with pytest.raises(GeminiProviderError, match="rate limit"):
        asyncio.run(client.ask("hi"))


def test_gemini_request_client_timeout_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req, timeout):
        raise TimeoutError("slow")

    monkeypatch.setattr("amo_bot.ai.gemini_provider.request.urlopen", fake_urlopen)
    client = GeminiRequestClient(config=_config())

    with pytest.raises(GeminiProviderError, match="request timeout"):
        asyncio.run(client.ask("hi"))


def test_gemini_request_client_generic_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req, timeout):
        raise error.HTTPError(req.full_url, 500, "Server", {}, None)

    monkeypatch.setattr("amo_bot.ai.gemini_provider.request.urlopen", fake_urlopen)
    client = GeminiRequestClient(config=_config())

    with pytest.raises(GeminiProviderError, match="status=500"):
        asyncio.run(client.ask("hi"))


def test_gemini_request_client_ask_maps_thread_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_to_thread(func, payload):
        raise TimeoutError("thread timed out")

    monkeypatch.setattr("amo_bot.ai.gemini_provider.asyncio.to_thread", fake_to_thread)
    client = GeminiRequestClient(config=_config())

    with pytest.raises(GeminiProviderError, match="request timeout"):
        asyncio.run(client.ask("hi"))
