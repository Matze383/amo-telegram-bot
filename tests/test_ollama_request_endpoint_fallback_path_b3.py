from __future__ import annotations

import asyncio

from amo_bot.ai.ollama import OllamaHTTPStatusError
from amo_bot.ai.service import AIService


class _PrimaryClient:
    def __init__(self, *, request_endpoint: str) -> None:
        self.base_url = "http://ollama"
        self.model = "primary"
        self.timeout_seconds = 1.0
        self.max_response_chars = 1000
        self.request_endpoint = request_endpoint
        self.calls: list[str] = []

    async def generate(self, prompt: str) -> str:
        self.calls.append(prompt)
        raise OllamaHTTPStatusError(503)


class _FallbackClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def generate(self, prompt: str) -> str:
        self.calls.append(prompt)
        return "fallback success"


def test_fallback_client_preserves_primary_request_endpoint(monkeypatch) -> None:
    primary = _PrimaryClient(request_endpoint="chat")
    fallback = _FallbackClient()

    captured: dict[str, object] = {}

    def _mk_fallback(
        *,
        base_url: str,
        model: str,
        timeout_seconds: float,
        max_prompt_chars: int,
        max_predict_tokens: int,
        max_response_chars: int,
        request_endpoint: str,
        streaming_mode: str,
        think: bool,
    ):
        captured.update(
            {
                "base_url": base_url,
                "model": model,
                "timeout_seconds": timeout_seconds,
                "max_prompt_chars": max_prompt_chars,
                "max_predict_tokens": max_predict_tokens,
                "max_response_chars": max_response_chars,
                "request_endpoint": request_endpoint,
                "streaming_mode": streaming_mode,
                "think": think,
            }
        )
        return fallback

    monkeypatch.setattr("amo_bot.ai.service.OllamaClient", _mk_fallback)

    service = AIService(
        client=primary,
        retry_on_transient_error=True,
        retry_delay_seconds=0,
        fallback_model="fallback-model",
    )

    out = asyncio.run(service.ask("hello"))

    assert out == "fallback success"
    assert primary.calls == ["hello", "hello"]
    assert fallback.calls == ["hello"]
    assert captured == {
        "base_url": "http://ollama",
        "model": "fallback-model",
        "timeout_seconds": 1.0,
        "max_prompt_chars": 4000,
        "max_predict_tokens": 512,
        "max_response_chars": 1000,
        "request_endpoint": "chat",
        "streaming_mode": "off",
        "think": False,
    }
