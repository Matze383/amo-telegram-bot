from __future__ import annotations

import asyncio
import json
from urllib import error

import pytest

from amo_bot.ai.anthropic_provider import AnthropicProviderConfig, AnthropicProviderError, AnthropicRequestClient


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


def _config() -> AnthropicProviderConfig:
    return AnthropicProviderConfig(
        api_key="ak-test",
        model="anthropic/claude-opus-4-6",
        timeout_seconds=3.0,
        base_url="https://api.anthropic.com",
    )


def test_anthropic_request_client_success(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["key"] = req.headers.get("X-api-key")
        captured["version"] = req.headers.get("Anthropic-version")
        captured["content_type"] = req.headers.get("Content-type")
        captured["timeout"] = timeout
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        return _Response(payload={"content": [{"type": "text", "text": "  hello  "}]})

    monkeypatch.setattr("amo_bot.ai.anthropic_provider.request.urlopen", fake_urlopen)

    client = AnthropicRequestClient(config=_config())
    result = asyncio.run(client.ask("hi"))

    assert result == "hello"
    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    assert captured["key"] == "ak-test"
    assert captured["version"] == "2023-06-01"
    assert captured["content_type"] == "application/json"
    assert captured["timeout"] == 3.0
    assert captured["payload"] == {
        "model": "anthropic/claude-opus-4-6",
        "max_tokens": 512,
        "messages": [{"role": "user", "content": "hi"}],
    }


def test_anthropic_request_client_missing_key() -> None:
    client = AnthropicRequestClient(
        config=AnthropicProviderConfig(api_key="", model="x", timeout_seconds=1.0, base_url="https://api.anthropic.com")
    )
    with pytest.raises(AnthropicProviderError, match="api key missing"):
        asyncio.run(client.ask("hi"))


@pytest.mark.parametrize("status", [401, 403])
def test_anthropic_request_client_auth_error(monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    def fake_urlopen(req, timeout):
        raise error.HTTPError(req.full_url, status, "Auth", {}, None)

    monkeypatch.setattr("amo_bot.ai.anthropic_provider.request.urlopen", fake_urlopen)
    client = AnthropicRequestClient(config=_config())

    with pytest.raises(AnthropicProviderError, match="auth error"):
        asyncio.run(client.ask("hi"))


def test_anthropic_request_client_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req, timeout):
        raise error.HTTPError(req.full_url, 429, "Too Many Requests", {}, None)

    monkeypatch.setattr("amo_bot.ai.anthropic_provider.request.urlopen", fake_urlopen)
    client = AnthropicRequestClient(config=_config())

    with pytest.raises(AnthropicProviderError, match="rate limit"):
        asyncio.run(client.ask("hi"))


def test_anthropic_request_client_timeout_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req, timeout):
        raise TimeoutError("slow")

    monkeypatch.setattr("amo_bot.ai.anthropic_provider.request.urlopen", fake_urlopen)
    client = AnthropicRequestClient(config=_config())

    with pytest.raises(AnthropicProviderError, match="request timeout"):
        asyncio.run(client.ask("hi"))


def test_anthropic_request_client_generic_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req, timeout):
        raise error.HTTPError(req.full_url, 500, "Server", {}, None)

    monkeypatch.setattr("amo_bot.ai.anthropic_provider.request.urlopen", fake_urlopen)
    client = AnthropicRequestClient(config=_config())

    with pytest.raises(AnthropicProviderError, match="status=500"):
        asyncio.run(client.ask("hi"))


def test_anthropic_request_client_ask_maps_thread_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_to_thread(func, payload):
        raise TimeoutError("thread timed out")

    monkeypatch.setattr("amo_bot.ai.anthropic_provider.asyncio.to_thread", fake_to_thread)
    client = AnthropicRequestClient(config=_config())

    with pytest.raises(AnthropicProviderError, match="request timeout"):
        asyncio.run(client.ask("hi"))
