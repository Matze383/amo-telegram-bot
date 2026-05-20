from __future__ import annotations

import asyncio
import logging

import pytest

from amo_bot.ai.ollama import OllamaError, OllamaHTTPStatusError
from amo_bot.ai.service import AIService


class _FakeClient:
    def __init__(self, *, base_url: str = "http://ollama", model: str = "primary", timeout_seconds: float = 1.0, max_response_chars: int = 1000, outcomes: list[object] | None = None) -> None:
        self.base_url = base_url
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_response_chars = max_response_chars
        self._outcomes = list(outcomes or [])
        self.calls: list[str] = []

    async def generate(self, prompt: str) -> str:
        self.calls.append(prompt)
        if not self._outcomes:
            raise AssertionError("missing outcome")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return str(outcome)


class _FallbackClient:
    def __init__(self, *, response: object) -> None:
        self.response = response
        self.calls: list[str] = []

    async def generate(self, prompt: str) -> str:
        self.calls.append(prompt)
        if isinstance(self.response, Exception):
            raise self.response
        return str(self.response)


def test_primary_transient_failure_then_retry_success(monkeypatch) -> None:
    client = _FakeClient(outcomes=[OllamaHTTPStatusError(503), "ok after retry"])
    service = AIService(client=client, retry_on_transient_error=True, retry_delay_seconds=0)

    out = asyncio.run(service.ask("hello"))

    assert out == "ok after retry"
    assert len(client.calls) == 2


def test_primary_retry_fail_then_fallback_model_success(monkeypatch) -> None:
    client = _FakeClient(outcomes=[OllamaHTTPStatusError(503), OllamaHTTPStatusError(503)])
    fallback = _FallbackClient(response="fallback success")

    def _mk_fallback(*, base_url: str, model: str, timeout_seconds: float, max_response_chars: int, request_endpoint: str):
        assert base_url == "http://ollama"
        assert model == "kimi-k2.5:cloud"
        assert timeout_seconds == 1.0
        assert max_response_chars == 1000
        assert request_endpoint == "generate"
        return fallback

    monkeypatch.setattr("amo_bot.ai.service.OllamaClient", _mk_fallback)

    service = AIService(
        client=client,
        retry_on_transient_error=True,
        retry_delay_seconds=0,
        fallback_model="kimi-k2.5:cloud",
    )

    out = asyncio.run(service.ask("hello"))

    assert out == "fallback success"
    assert len(client.calls) == 2
    assert len(fallback.calls) == 1


def test_primary_retry_and_fallback_fail_raises(monkeypatch) -> None:
    client = _FakeClient(outcomes=[OllamaHTTPStatusError(503), OllamaHTTPStatusError(503)])

    def _mk_fallback(*, base_url: str, model: str, timeout_seconds: float, max_response_chars: int, request_endpoint: str):
        assert request_endpoint == "generate"
        return _FallbackClient(response=OllamaError("request timed out"))

    monkeypatch.setattr("amo_bot.ai.service.OllamaClient", _mk_fallback)

    service = AIService(
        client=client,
        retry_on_transient_error=True,
        retry_delay_seconds=0,
        fallback_model="kimi-k2.5:cloud",
    )

    with pytest.raises(OllamaError):
        asyncio.run(service.ask("hello"))


def test_non_transient_http_4xx_does_not_retry() -> None:
    client = _FakeClient(outcomes=[OllamaHTTPStatusError(400)])
    service = AIService(client=client, retry_on_transient_error=True, retry_delay_seconds=0)

    with pytest.raises(OllamaHTTPStatusError):
        asyncio.run(service.ask("hello"))

    assert len(client.calls) == 1


def test_logs_primary_success_metadata_only(caplog: pytest.LogCaptureFixture) -> None:
    client = _FakeClient(model="qwen3", outcomes=["ok"])
    service = AIService(client=client, retry_on_transient_error=True, retry_delay_seconds=0)

    with caplog.at_level(logging.INFO, logger="amo_bot.ai.service"):
        out = asyncio.run(service.ask("hello world"))

    assert out == "ok"
    messages = [rec.message for rec in caplog.records if rec.name == "amo_bot.ai.service"]
    assert any("phase=primary" in msg and "model=qwen3" in msg and "prompt_len=11" in msg and "outcome=success" in msg for msg in messages)
    assert all("hello world" not in msg for msg in messages)


def test_logs_retry_error_then_success_metadata_only(caplog: pytest.LogCaptureFixture) -> None:
    client = _FakeClient(model="qwen3", outcomes=[OllamaHTTPStatusError(503), "ok after retry"])
    service = AIService(client=client, retry_on_transient_error=True, retry_delay_seconds=0)

    with caplog.at_level(logging.INFO, logger="amo_bot.ai.service"):
        out = asyncio.run(service.ask("hello"))

    assert out == "ok after retry"
    messages = [rec.message for rec in caplog.records if rec.name == "amo_bot.ai.service"]
    assert any("phase=primary" in msg and "outcome=error" in msg and "error_category=transient_http" in msg for msg in messages)
    assert any("phase=retry" in msg and "outcome=success" in msg for msg in messages)
    assert all("hello" not in msg for msg in messages)


def test_logs_fallback_success_metadata_only(monkeypatch, caplog: pytest.LogCaptureFixture) -> None:
    client = _FakeClient(model="qwen3", outcomes=[OllamaHTTPStatusError(503), OllamaHTTPStatusError(503)])
    fallback = _FallbackClient(response="fallback success")

    def _mk_fallback(*, base_url: str, model: str, timeout_seconds: float, max_response_chars: int, request_endpoint: str):
        assert request_endpoint == "generate"
        return fallback

    monkeypatch.setattr("amo_bot.ai.service.OllamaClient", _mk_fallback)

    service = AIService(
        client=client,
        retry_on_transient_error=True,
        retry_delay_seconds=0,
        fallback_model="kimi-k2.5:cloud",
    )

    with caplog.at_level(logging.INFO, logger="amo_bot.ai.service"):
        out = asyncio.run(service.ask("sensitive prompt text"))

    assert out == "fallback success"
    messages = [rec.message for rec in caplog.records if rec.name == "amo_bot.ai.service"]
    assert any("phase=fallback" in msg and "outcome=success" in msg for msg in messages)
    assert all("sensitive prompt text" not in msg for msg in messages)


def test_ai_service_retries_on_empty_response_when_enabled() -> None:
    client = _FakeClient(outcomes=[OllamaError("empty response"), "ok-after-empty"])
    service = AIService(client=client, retry_on_transient_error=True, retry_delay_seconds=0)

    answer = asyncio.run(service.ask("hello"))

    assert answer == "ok-after-empty"
    assert len(client.calls) == 2


def test_ai_service_collect_only_stream_contract_error_retries_then_fallback(monkeypatch) -> None:
    client = _FakeClient(model="qwen3", outcomes=[OllamaError("request timed out"), OllamaError("request timed out")])
    client.request_endpoint = "chat"
    fallback = _FallbackClient(response="fallback final")

    def _mk_fallback(*, base_url: str, model: str, timeout_seconds: float, max_response_chars: int, request_endpoint: str):
        assert request_endpoint == "chat"
        return fallback

    monkeypatch.setattr("amo_bot.ai.service.OllamaClient", _mk_fallback)

    service = AIService(
        client=client,
        retry_on_transient_error=True,
        retry_delay_seconds=0,
        fallback_model="kimi-k2.5:cloud",
    )

    out = asyncio.run(service.ask("prompt content that must never be logged"))

    assert out == "fallback final"
    assert len(client.calls) == 2
    assert len(fallback.calls) == 1


def test_logs_timeout_as_generic_metadata_only(caplog: pytest.LogCaptureFixture) -> None:
    client = _FakeClient(model="qwen3", outcomes=[OllamaError("request timed out"), "ok after retry"])
    service = AIService(client=client, retry_on_transient_error=True, retry_delay_seconds=0)

    with caplog.at_level(logging.INFO, logger="amo_bot.ai.service"):
        out = asyncio.run(service.ask("super-secret prompt"))

    assert out == "ok after retry"
    messages = [rec.message for rec in caplog.records if rec.name == "amo_bot.ai.service"]
    assert any("phase=primary" in msg and "outcome=error" in msg and "error_category=timeout" in msg for msg in messages)
    assert any("phase=retry" in msg and "outcome=success" in msg for msg in messages)
    assert all("super-secret prompt" not in msg for msg in messages)
