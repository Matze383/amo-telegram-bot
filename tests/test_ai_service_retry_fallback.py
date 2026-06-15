from __future__ import annotations

import asyncio
import logging

import pytest

from amo_bot.ai.model_policy import AIModelPolicyConfig
from amo_bot.ai.ollama import OllamaError, OllamaHTTPStatusError
from amo_bot.ai.service import AIService


class _FakeClient:
    def __init__(
        self,
        *,
        base_url: str = "http://ollama",
        model: str = "primary",
        timeout_seconds: float = 1.0,
        max_prompt_chars: int = 4000,
        max_predict_tokens: int = 512,
        max_response_chars: int = 1000,
        request_endpoint: str = "generate",
        streaming_mode: str = "off",
        think: bool = False,
        outcomes: list[object] | None = None,
        stream_events: list[dict[str, object]] | None = None,
    ) -> None:
        self.base_url = base_url
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_prompt_chars = max_prompt_chars
        self.max_predict_tokens = max_predict_tokens
        self.max_response_chars = max_response_chars
        self.request_endpoint = request_endpoint
        self.streaming_mode = streaming_mode
        self.think = think
        self._outcomes = list(outcomes or [])
        self.calls: list[str] = []
        self.last_stream_events = list(stream_events or [])

    async def generate(self, prompt: str) -> str:
        self.calls.append(prompt)
        if not self._outcomes:
            raise AssertionError("missing outcome")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return str(outcome)


class _FallbackClient:
    def __init__(
        self,
        *,
        response: object,
        model: str = "fallback",
        timeout_seconds: float = 1.0,
        max_prompt_chars: int = 4000,
        think: bool = False,
        stream_events: list[dict[str, object]] | None = None,
    ) -> None:
        self.response = response
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_prompt_chars = max_prompt_chars
        self.think = think
        self.calls: list[str] = []
        self.last_stream_events = list(stream_events or [])

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

    def _mk_fallback(**kwargs):
        base_url = kwargs["base_url"]
        model = kwargs["model"]
        timeout_seconds = kwargs["timeout_seconds"]
        max_response_chars = kwargs["max_response_chars"]
        request_endpoint = kwargs["request_endpoint"]
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

    def _mk_fallback(**kwargs):
        assert kwargs["request_endpoint"] == "generate"
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


def test_policy_routes_answer_synthesis_to_thinking_model(monkeypatch, caplog: pytest.LogCaptureFixture) -> None:
    routed_clients: list[_FallbackClient] = []

    def _mk_client(**kwargs):
        client = _FallbackClient(
            response="thinking success",
            model=kwargs["model"],
            timeout_seconds=kwargs["timeout_seconds"],
            max_prompt_chars=kwargs["max_prompt_chars"],
            think=kwargs["think"],
        )
        routed_clients.append(client)
        return client

    monkeypatch.setattr("amo_bot.ai.service.OllamaClient", _mk_client)

    service = AIService(
        client=_FakeClient(model="qwen-default"),
        retry_delay_seconds=0,
        model_policy=AIModelPolicyConfig(
            enabled=True,
            thinking_model="kimi-thinking",
            non_thinking_model="qwen-fast",
            thinking_timeout_seconds=45.0,
            thinking_budget_max_prompt_chars=8000,
        ),
    )

    with caplog.at_level(logging.INFO, logger="amo_bot.ai.service"):
        out = asyncio.run(service.ask("prompt content that must stay private", task_type="answer_synthesis"))

    assert out == "thinking success"
    assert len(routed_clients) == 1
    routed = routed_clients[0]
    assert routed.model == "kimi-thinking"
    assert routed.think is True
    assert routed.timeout_seconds == 45.0
    assert routed.max_prompt_chars == 8000
    messages = [rec.message for rec in caplog.records if rec.name == "amo_bot.ai.service"]
    assert any(
        "model=kimi-thinking" in msg
        and "task_type=answer_synthesis" in msg
        and "route_decision=thinking" in msg
        and "think=True" in msg
        for msg in messages
    )
    assert all("prompt content" not in msg for msg in messages)


def test_policy_falls_back_to_non_thinking_model_after_transient_failures(monkeypatch) -> None:
    thinking_client = _FakeClient(
        model="kimi-thinking",
        think=True,
        outcomes=[OllamaHTTPStatusError(503), OllamaHTTPStatusError(503)],
    )
    fallback_client = _FallbackClient(response="fallback final", model="qwen-fast", think=False)
    created: list[object] = []

    def _mk_client(**kwargs):
        if kwargs["model"] == "kimi-thinking":
            created.append(thinking_client)
            return thinking_client
        assert kwargs["model"] == "qwen-fast"
        assert kwargs["think"] is False
        created.append(fallback_client)
        return fallback_client

    monkeypatch.setattr("amo_bot.ai.service.OllamaClient", _mk_client)

    service = AIService(
        client=_FakeClient(model="qwen-default"),
        retry_delay_seconds=0,
        model_policy=AIModelPolicyConfig(
            enabled=True,
            thinking_model="kimi-thinking",
            non_thinking_model="qwen-fast",
        ),
    )

    out = asyncio.run(service.ask("latest sports news", task_type="sports"))

    assert out == "fallback final"
    assert len(thinking_client.calls) == 2
    assert len(fallback_client.calls) == 1
    assert created == [thinking_client, fallback_client]


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

    def _mk_fallback(**kwargs):
        assert kwargs["request_endpoint"] == "generate"
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

    def _mk_fallback(**kwargs):
        assert kwargs["request_endpoint"] == "chat"
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


def test_stream_handoff_dedupes_duplicate_terminal_done() -> None:
    client = _FakeClient(
        outcomes=["ok"],
        stream_events=[
            {"type": "delta", "text": "hel"},
            {"type": "terminal", "outcome": "done"},
            {"type": "terminal", "outcome": "done"},
        ],
    )
    service = AIService(client=client, retry_on_transient_error=True, retry_delay_seconds=0)

    out = asyncio.run(service.ask("hello"))

    assert out == "ok"
    assert service.last_stream_events == [
        {"type": "delta", "text": "hel"},
        {"type": "terminal", "outcome": "done"},
    ]


def test_stream_handoff_suppresses_post_terminal_delta() -> None:
    client = _FakeClient(
        outcomes=["ok"],
        stream_events=[
            {"type": "delta", "text": "before"},
            {"type": "terminal", "outcome": "done"},
            {"type": "delta", "text": "after"},
        ],
    )
    service = AIService(client=client, retry_on_transient_error=True, retry_delay_seconds=0)

    _ = asyncio.run(service.ask("hello"))

    assert service.last_stream_events == [
        {"type": "delta", "text": "before"},
        {"type": "terminal", "outcome": "done"},
    ]


@pytest.mark.parametrize("outcome", ["cancel", "timeout"])
def test_stream_handoff_cancel_timeout_terminals_win(outcome: str) -> None:
    client = _FakeClient(
        outcomes=["ok"],
        stream_events=[
            {"type": "delta", "text": "start"},
            {"type": "terminal", "outcome": outcome},
            {"type": "delta", "text": "ignored"},
            {"type": "terminal", "outcome": "done"},
        ],
    )
    service = AIService(client=client, retry_on_transient_error=True, retry_delay_seconds=0)

    _ = asyncio.run(service.ask("hello"))

    assert service.last_stream_events == [
        {"type": "delta", "text": "start"},
        {"type": "terminal", "outcome": outcome},
    ]


def test_stream_handoff_non_terminal_passthrough_before_terminal() -> None:
    client = _FakeClient(
        outcomes=["ok"],
        stream_events=[
            {"type": "status", "phase": "queued"},
            {"type": "delta", "text": "hel"},
            {"type": "delta", "text": "lo"},
            {"type": "terminal", "outcome": "done"},
        ],
    )
    service = AIService(client=client, retry_on_transient_error=True, retry_delay_seconds=0)

    _ = asyncio.run(service.ask("hello"))

    assert service.last_stream_events == [
        {"type": "status", "phase": "queued"},
        {"type": "delta", "text": "hel"},
        {"type": "delta", "text": "lo"},
        {"type": "terminal", "outcome": "done"},
    ]


def test_stream_handoff_uses_fallback_events_with_terminal_convergence(monkeypatch) -> None:
    client = _FakeClient(outcomes=[OllamaHTTPStatusError(503), OllamaHTTPStatusError(503)])
    fallback = _FallbackClient(
        response="fallback final",
        stream_events=[
            {"type": "delta", "text": "fb"},
            {"type": "terminal", "outcome": "error"},
            {"type": "delta", "text": "ignored"},
            {"type": "terminal", "outcome": "done"},
        ],
    )

    def _mk_fallback(**kwargs):
        assert kwargs["request_endpoint"] == "generate"
        return fallback

    monkeypatch.setattr("amo_bot.ai.service.OllamaClient", _mk_fallback)

    service = AIService(
        client=client,
        retry_on_transient_error=True,
        retry_delay_seconds=0,
        fallback_model="kimi-k2.5:cloud",
    )

    out = asyncio.run(service.ask("hello"))

    assert out == "fallback final"
    assert service.last_stream_events == [
        {"type": "delta", "text": "fb"},
        {"type": "terminal", "outcome": "error"},
    ]


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


def test_logs_empty_response_error_category_metadata_only(caplog: pytest.LogCaptureFixture) -> None:
    client = _FakeClient(model="qwen3", outcomes=[OllamaError("empty response"), "ok after retry"])
    service = AIService(client=client, retry_on_transient_error=True, retry_delay_seconds=0)

    with caplog.at_level(logging.INFO, logger="amo_bot.ai.service"):
        out = asyncio.run(service.ask("top-secret prompt"))

    assert out == "ok after retry"
    messages = [rec.message for rec in caplog.records if rec.name == "amo_bot.ai.service"]
    assert any("phase=primary" in msg and "outcome=error" in msg and "error_category=empty_response" in msg for msg in messages)
    assert any("phase=retry" in msg and "outcome=success" in msg for msg in messages)
    assert all("top-secret prompt" not in msg for msg in messages)
