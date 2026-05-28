from __future__ import annotations

import asyncio
import json
from urllib import error

import pytest

from amo_bot.ai.bedrock_provider import BedrockProviderConfig, BedrockProviderError, BedrockRequestClient


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


def _config() -> BedrockProviderConfig:
    return BedrockProviderConfig(
        model="amazon-bedrock/anthropic.claude-3-haiku-20240307-v1:0",
        region="eu-central-1",
        timeout_seconds=3.0,
        access_key_id="credential-placeholder",
        secret_access_key="credential-placeholder",
        session_token="credential-token-placeholder",
    )


def test_bedrock_request_client_success(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["content_type"] = req.headers.get("Content-type")
        captured["timeout"] = timeout
        captured["headers"] = dict(req.headers)
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        return _Response(payload={"output": {"message": {"content": [{"text": " hello "}]}}})

    monkeypatch.setattr("amo_bot.ai.bedrock_provider.request.urlopen", fake_urlopen)

    client = BedrockRequestClient(config=_config())
    result = asyncio.run(client.ask("hi"))

    assert result == "hello"
    assert captured["url"] == "https://bedrock-runtime.eu-central-1.amazonaws.com/model/amazon-bedrock/anthropic.claude-3-haiku-20240307-v1:0/converse"
    assert captured["content_type"] == "application/json"
    assert captured["timeout"] == 3.0
    assert "X-amo-aws-access-key-id" not in captured["headers"]
    assert "X-amo-aws-secret-access-key" not in captured["headers"]
    assert "X-amo-aws-session-token" not in captured["headers"]


def test_bedrock_request_client_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req, timeout):
        raise error.HTTPError(req.full_url, 500, "Server Error", {}, None)

    monkeypatch.setattr("amo_bot.ai.bedrock_provider.request.urlopen", fake_urlopen)
    client = BedrockRequestClient(config=_config())

    with pytest.raises(BedrockProviderError, match="status=500"):
        asyncio.run(client.ask("hi"))


@pytest.mark.parametrize("status", [401, 403])
def test_bedrock_request_client_auth_error(monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    def fake_urlopen(req, timeout):
        raise error.HTTPError(req.full_url, status, "Unauthorized", {}, None)

    monkeypatch.setattr("amo_bot.ai.bedrock_provider.request.urlopen", fake_urlopen)
    client = BedrockRequestClient(config=_config())

    with pytest.raises(BedrockProviderError, match="auth error"):
        asyncio.run(client.ask("hi"))


def test_bedrock_request_client_timeout_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req, timeout):
        raise TimeoutError("slow")

    monkeypatch.setattr("amo_bot.ai.bedrock_provider.request.urlopen", fake_urlopen)
    client = BedrockRequestClient(config=_config())

    with pytest.raises(BedrockProviderError, match="request timeout"):
        asyncio.run(client.ask("hi"))


def test_bedrock_request_client_transport_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req, timeout):
        raise error.URLError("boom")

    monkeypatch.setattr("amo_bot.ai.bedrock_provider.request.urlopen", fake_urlopen)
    client = BedrockRequestClient(config=_config())

    with pytest.raises(BedrockProviderError, match="transport error"):
        asyncio.run(client.ask("hi"))


def test_bedrock_request_client_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    class _BadResponse(_Response):
        def read(self) -> bytes:
            return b"not-json"

    monkeypatch.setattr("amo_bot.ai.bedrock_provider.request.urlopen", lambda req, timeout: _BadResponse())
    client = BedrockRequestClient(config=_config())

    with pytest.raises(BedrockProviderError, match="invalid json"):
        asyncio.run(client.ask("hi"))


def test_bedrock_request_client_malformed_or_empty_content(monkeypatch: pytest.MonkeyPatch) -> None:
    client = BedrockRequestClient(config=_config())

    for payload in (
        {},
        {"output": {}},
        {"output": {"message": {"content": []}}},
        {"output": {"message": {"content": [{"text": "   "}]}}},
    ):
        monkeypatch.setattr(
            "amo_bot.ai.bedrock_provider.request.urlopen",
            lambda req, timeout, _payload=payload: _Response(payload=_payload),
        )
        with pytest.raises(BedrockProviderError, match="malformed response"):
            asyncio.run(client.ask("hi"))


def test_bedrock_request_client_ask_maps_thread_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_to_thread(func, payload):
        raise asyncio.TimeoutError

    monkeypatch.setattr("amo_bot.ai.bedrock_provider.asyncio.to_thread", fake_to_thread)
    client = BedrockRequestClient(config=_config())

    with pytest.raises(BedrockProviderError, match="request timeout"):
        asyncio.run(client.ask("hi"))


def test_bedrock_provider_config_redacted_dict_masks_credential_presence() -> None:
    config = _config()
    redacted = config.redacted_dict()
    assert redacted["provider"] == "amazon-bedrock"
    assert redacted["aws_access_key_id_present"] is True
    assert redacted["aws_secret_access_key_present"] is True
    assert redacted["aws_session_token_present"] is True
