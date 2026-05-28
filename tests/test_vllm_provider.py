from __future__ import annotations

import asyncio
import json
from urllib import error

import pytest

from amo_bot.ai.vllm_provider import (
    VLLMProviderConfig,
    VLLMProviderError,
    VLLMRequestClient,
)


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


def _config() -> VLLMProviderConfig:
    return VLLMProviderConfig(
        api_key=None,
        model="meta-llama/Llama-3.1-8B-Instruct",
        timeout_seconds=3.0,
        base_url="http://127.0.0.1:8000/v1",
    )


def test_vllm_request_client_success_no_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["auth"] = req.headers.get("Authorization")
        captured["content_type"] = req.headers.get("Content-type")
        captured["timeout"] = timeout
        payload = json.loads(req.data.decode("utf-8"))
        captured["payload"] = payload
        return _Response(payload={"choices": [{"message": {"content": "  hello  "}}]})

    monkeypatch.setattr("amo_bot.ai.vllm_provider.request.urlopen", fake_urlopen)

    client = VLLMRequestClient(config=_config())
    result = asyncio.run(client.ask("hi"))

    assert result == "hello"
    assert captured["url"] == "http://127.0.0.1:8000/v1/chat/completions"
    assert captured["auth"] is None
    assert captured["content_type"] == "application/json"
    assert captured["timeout"] == 3.0
    assert captured["payload"] == {
        "model": "meta-llama/Llama-3.1-8B-Instruct",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": False,
    }


def test_vllm_request_client_success_with_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["auth"] = req.headers.get("Authorization")
        return _Response(payload={"choices": [{"message": {"content": "result"}}]})

    monkeypatch.setattr("amo_bot.ai.vllm_provider.request.urlopen", fake_urlopen)

    cfg = VLLMProviderConfig(
        api_key="vllm-secret-key",
        model="meta-llama/Llama-3.1-8B-Instruct",
        timeout_seconds=3.0,
        base_url="http://127.0.0.1:8000/v1",
    )
    client = VLLMRequestClient(config=cfg)
    result = asyncio.run(client.ask("hi"))

    assert result == "result"
    assert captured["auth"] == "Bearer vllm-secret-key"


def test_vllm_request_client_http_auth_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req, timeout):
        raise error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr("amo_bot.ai.vllm_provider.request.urlopen", fake_urlopen)
    client = VLLMRequestClient(config=_config())

    with pytest.raises(VLLMProviderError, match="auth error"):
        asyncio.run(client.ask("hi"))


def test_vllm_request_client_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req, timeout):
        raise error.HTTPError(req.full_url, 429, "Too Many Requests", {}, None)

    monkeypatch.setattr("amo_bot.ai.vllm_provider.request.urlopen", fake_urlopen)
    client = VLLMRequestClient(config=_config())

    with pytest.raises(VLLMProviderError, match="rate limit"):
        asyncio.run(client.ask("hi"))


def test_vllm_request_client_timeout_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req, timeout):
        raise TimeoutError("slow")

    monkeypatch.setattr("amo_bot.ai.vllm_provider.request.urlopen", fake_urlopen)
    client = VLLMRequestClient(config=_config())

    with pytest.raises(VLLMProviderError, match="request timeout"):
        asyncio.run(client.ask("hi"))


def test_vllm_request_client_transport_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req, timeout):
        raise error.URLError("boom")

    monkeypatch.setattr("amo_bot.ai.vllm_provider.request.urlopen", fake_urlopen)
    client = VLLMRequestClient(config=_config())

    with pytest.raises(VLLMProviderError, match="transport error"):
        asyncio.run(client.ask("hi"))


def test_vllm_request_client_generic_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req, timeout):
        raise error.HTTPError(req.full_url, 500, "Server Error", {}, None)

    monkeypatch.setattr("amo_bot.ai.vllm_provider.request.urlopen", fake_urlopen)
    client = VLLMRequestClient(config=_config())

    with pytest.raises(VLLMProviderError, match="status=500"):
        asyncio.run(client.ask("hi"))


@pytest.mark.parametrize("status", [401, 429, 500])
def test_vllm_request_client_non_2xx_response_status(monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    monkeypatch.setattr(
        "amo_bot.ai.vllm_provider.request.urlopen",
        lambda req, timeout: _Response(status=status, payload={"choices": [{"message": {"content": "ignored"}}]}),
    )
    client = VLLMRequestClient(config=_config())

    if status == 401:
        match = "auth error"
    elif status == 429:
        match = "rate limit"
    else:
        match = f"status={status}"

    with pytest.raises(VLLMProviderError, match=match):
        asyncio.run(client.ask("hi"))


def test_vllm_request_client_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    class _BadResponse(_Response):
        def read(self) -> bytes:
            return b"not-json"

    monkeypatch.setattr("amo_bot.ai.vllm_provider.request.urlopen", lambda req, timeout: _BadResponse())
    client = VLLMRequestClient(config=_config())

    with pytest.raises(VLLMProviderError, match="invalid json"):
        asyncio.run(client.ask("hi"))


def test_vllm_request_client_malformed_or_empty_content(monkeypatch: pytest.MonkeyPatch) -> None:
    client = VLLMRequestClient(config=_config())

    for payload in (
        {},
        {"choices": []},
        {"choices": [{"message": {"content": "   "}}]},
        {"choices": [{"message": {}}]},
    ):
        monkeypatch.setattr(
            "amo_bot.ai.vllm_provider.request.urlopen",
            lambda req, timeout, _payload=payload: _Response(payload=_payload),
        )
        with pytest.raises(VLLMProviderError, match="malformed response"):
            asyncio.run(client.ask("hi"))


def test_vllm_request_client_ask_maps_thread_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_to_thread(func, payload):
        raise asyncio.TimeoutError

    monkeypatch.setattr("amo_bot.ai.vllm_provider.asyncio.to_thread", fake_to_thread)
    client = VLLMRequestClient(config=_config())

    with pytest.raises(VLLMProviderError, match="request timeout"):
        asyncio.run(client.ask("hi"))


def test_vllm_provider_config_redacted_dict_no_key() -> None:
    cfg = VLLMProviderConfig(api_key=None, model="llama3.1", timeout_seconds=60.0, base_url="http://127.0.0.1:8000/v1")
    redacted = cfg.redacted_dict()
    assert redacted["provider"] == "vllm"
    assert redacted["model"] == "llama3.1"
    assert redacted["base_url"] == "http://127.0.0.1:8000/v1"
    assert redacted["api_key_present"] is False
    assert redacted["api_key_preview"] is None


def test_vllm_provider_config_redacted_dict_with_key() -> None:
    cfg = VLLMProviderConfig(api_key="secret-key", model="llama3.1", timeout_seconds=60.0, base_url="http://127.0.0.1:8000/v1")
    redacted = cfg.redacted_dict()
    assert redacted["api_key_present"] is True
    assert redacted["api_key_preview"] == "***"