from __future__ import annotations

import asyncio
import json
from urllib import error

import pytest

from amo_bot.ai.xai_provider import XAIProviderConfig, XAIProviderError, XAIRequestClient


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


def _config() -> XAIProviderConfig:
    return XAIProviderConfig(
        api_key="xai-credential-placeholder",
        model="xai/grok-4.3",
        timeout_seconds=3.0,
        base_url="https://api.x.ai/v1",
    )


def test_xai_request_client_success(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["auth"] = req.headers.get("Authorization")
        captured["content_type"] = req.headers.get("Content-type")
        captured["timeout"] = timeout
        payload = json.loads(req.data.decode("utf-8"))
        captured["payload"] = payload
        return _Response(payload={"choices": [{"message": {"content": "  hello  "}}]})

    monkeypatch.setattr("amo_bot.ai.xai_provider.request.urlopen", fake_urlopen)

    client = XAIRequestClient(config=_config())
    result = asyncio.run(client.ask("hi"))

    assert result == "hello"
    assert captured["url"] == "https://api.x.ai/v1/chat/completions"
    assert captured["auth"] == "Bearer xai-credential-placeholder"
    assert captured["content_type"] == "application/json"
    assert captured["timeout"] == 3.0
    assert captured["payload"] == {
        "model": "xai/grok-4.3",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": False,
    }


def test_xai_request_client_missing_key() -> None:
    client = XAIRequestClient(
        config=XAIProviderConfig(api_key="", model="x", timeout_seconds=1.0, base_url="https://api.x.ai/v1")
    )
    with pytest.raises(XAIProviderError, match="api key missing"):
        asyncio.run(client.ask("hi"))


@pytest.mark.parametrize("status", [401, 403])
def test_xai_request_client_auth_error(monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    def fake_urlopen(req, timeout):
        raise error.HTTPError(req.full_url, status, "Unauthorized", {}, None)

    monkeypatch.setattr("amo_bot.ai.xai_provider.request.urlopen", fake_urlopen)
    client = XAIRequestClient(config=_config())

    with pytest.raises(XAIProviderError, match="auth error"):
        asyncio.run(client.ask("hi"))


def test_xai_request_client_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req, timeout):
        raise error.HTTPError(req.full_url, 429, "Too Many Requests", {}, None)

    monkeypatch.setattr("amo_bot.ai.xai_provider.request.urlopen", fake_urlopen)
    client = XAIRequestClient(config=_config())

    with pytest.raises(XAIProviderError, match="rate limit"):
        asyncio.run(client.ask("hi"))


def test_xai_request_client_timeout_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req, timeout):
        raise TimeoutError("slow")

    monkeypatch.setattr("amo_bot.ai.xai_provider.request.urlopen", fake_urlopen)
    client = XAIRequestClient(config=_config())

    with pytest.raises(XAIProviderError, match="request timeout"):
        asyncio.run(client.ask("hi"))


def test_xai_request_client_generic_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req, timeout):
        raise error.HTTPError(req.full_url, 500, "Server Error", {}, None)

    monkeypatch.setattr("amo_bot.ai.xai_provider.request.urlopen", fake_urlopen)
    client = XAIRequestClient(config=_config())

    with pytest.raises(XAIProviderError, match="status=500"):
        asyncio.run(client.ask("hi"))


@pytest.mark.parametrize("status", [401, 429, 500])
def test_xai_request_client_non_2xx_response_status(monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    monkeypatch.setattr(
        "amo_bot.ai.xai_provider.request.urlopen",
        lambda req, timeout: _Response(status=status, payload={"choices": [{"message": {"content": "ignored"}}]}),
    )
    client = XAIRequestClient(config=_config())

    if status == 401:
        match = "auth error"
    elif status == 429:
        match = "rate limit"
    else:
        match = f"status={status}"

    with pytest.raises(XAIProviderError, match=match):
        asyncio.run(client.ask("hi"))


def test_xai_request_client_transport_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req, timeout):
        raise error.URLError("boom")

    monkeypatch.setattr("amo_bot.ai.xai_provider.request.urlopen", fake_urlopen)
    client = XAIRequestClient(config=_config())

    with pytest.raises(XAIProviderError, match="transport error"):
        asyncio.run(client.ask("hi"))


def test_xai_request_client_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    class _BadResponse(_Response):
        def read(self) -> bytes:
            return b"not-json"

    monkeypatch.setattr("amo_bot.ai.xai_provider.request.urlopen", lambda req, timeout: _BadResponse())
    client = XAIRequestClient(config=_config())

    with pytest.raises(XAIProviderError, match="invalid json"):
        asyncio.run(client.ask("hi"))


def test_xai_request_client_malformed_or_empty_content(monkeypatch: pytest.MonkeyPatch) -> None:
    client = XAIRequestClient(config=_config())

    for payload in (
        {},
        {"choices": []},
        {"choices": [{"message": {"content": "   "}}]},
        {"choices": [{"message": {}}]},
    ):
        monkeypatch.setattr(
            "amo_bot.ai.xai_provider.request.urlopen",
            lambda req, timeout, _payload=payload: _Response(payload=_payload),
        )
        with pytest.raises(XAIProviderError, match="malformed response"):
            asyncio.run(client.ask("hi"))


def test_xai_request_client_ask_maps_thread_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_to_thread(func, payload):
        raise asyncio.TimeoutError

    monkeypatch.setattr("amo_bot.ai.xai_provider.asyncio.to_thread", fake_to_thread)
    client = XAIRequestClient(config=_config())

    with pytest.raises(XAIProviderError, match="request timeout"):
        asyncio.run(client.ask("hi"))


def test_xai_provider_config_redacted_dict_masks_key() -> None:
    config = _config()
    redacted = config.redacted_dict()
    assert redacted["provider"] == "xai"
    assert redacted["api_key_present"] is True
    assert redacted["api_key_preview"] == "***"
