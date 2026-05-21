from __future__ import annotations

import asyncio
import json
from urllib import error

import pytest

from amo_bot.ai.openai_provider import OpenAIProviderConfig, OpenAIProviderError, OpenAIRequestClient


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


def _config() -> OpenAIProviderConfig:
    return OpenAIProviderConfig(api_key="sk-test", model="gpt-4o-mini", timeout_seconds=3.0)


def test_openai_request_client_success(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["auth"] = req.headers.get("Authorization")
        captured["content_type"] = req.headers.get("Content-type")
        captured["timeout"] = timeout
        payload = json.loads(req.data.decode("utf-8"))
        captured["payload"] = payload
        return _Response(payload={"choices": [{"message": {"content": "  hello  "}}]})

    monkeypatch.setattr("amo_bot.ai.openai_provider.request.urlopen", fake_urlopen)

    client = OpenAIRequestClient(config=_config())
    result = asyncio.run(client.ask("hi"))

    assert result == "hello"
    assert captured["url"] == "https://api.openai.com/v1/chat/completions"
    assert captured["auth"] == "Bearer sk-test"
    assert captured["content_type"] == "application/json"
    assert captured["timeout"] == 3.0
    assert captured["payload"] == {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": False,
    }


def test_openai_request_client_missing_key() -> None:
    client = OpenAIRequestClient(config=OpenAIProviderConfig(api_key="", model="x", timeout_seconds=1.0))
    with pytest.raises(OpenAIProviderError, match="api key missing"):
        asyncio.run(client.ask("hi"))


def test_openai_request_client_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req, timeout):
        raise error.HTTPError(req.full_url, 429, "Too Many Requests", {}, None)

    monkeypatch.setattr("amo_bot.ai.openai_provider.request.urlopen", fake_urlopen)
    client = OpenAIRequestClient(config=_config())

    with pytest.raises(OpenAIProviderError, match="status=429"):
        asyncio.run(client.ask("hi"))


def test_openai_request_client_transport_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req, timeout):
        raise error.URLError("boom")

    monkeypatch.setattr("amo_bot.ai.openai_provider.request.urlopen", fake_urlopen)
    client = OpenAIRequestClient(config=_config())

    with pytest.raises(OpenAIProviderError, match="transport error"):
        asyncio.run(client.ask("hi"))


def test_openai_request_client_timeout_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req, timeout):
        raise TimeoutError("slow")

    monkeypatch.setattr("amo_bot.ai.openai_provider.request.urlopen", fake_urlopen)
    client = OpenAIRequestClient(config=_config())

    with pytest.raises(OpenAIProviderError, match="request timeout"):
        asyncio.run(client.ask("hi"))


def test_openai_request_client_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    class _BadResponse(_Response):
        def read(self) -> bytes:
            return b"not-json"

    monkeypatch.setattr("amo_bot.ai.openai_provider.request.urlopen", lambda req, timeout: _BadResponse())
    client = OpenAIRequestClient(config=_config())

    with pytest.raises(OpenAIProviderError, match="invalid json"):
        asyncio.run(client.ask("hi"))


def test_openai_request_client_malformed_or_empty_content(monkeypatch: pytest.MonkeyPatch) -> None:
    client = OpenAIRequestClient(config=_config())

    for payload in (
        {},
        {"choices": []},
        {"choices": [{"message": {"content": "   "}}]},
        {"choices": [{"message": {}}]},
    ):
        monkeypatch.setattr(
            "amo_bot.ai.openai_provider.request.urlopen",
            lambda req, timeout, _payload=payload: _Response(payload=_payload),
        )
        with pytest.raises(OpenAIProviderError, match="malformed response"):
            asyncio.run(client.ask("hi"))
