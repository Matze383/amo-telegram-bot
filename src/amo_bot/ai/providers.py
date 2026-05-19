from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from amo_bot.ai.ollama import OllamaClient
from amo_bot.ai.service import AIService
from amo_bot.config.settings import Settings


class AIProvider(Protocol):
    async def ask(self, prompt: str) -> str: ...


@dataclass(frozen=True, slots=True)
class OllamaProvider:
    service: AIService

    async def ask(self, prompt: str) -> str:
        return await self.service.ask(prompt)


def build_ai_provider(settings: Settings) -> AIProvider:
    provider = settings.ai_provider.strip().casefold()

    # Incremental seam: keep existing behavior by routing all currently
    # supported values through the existing Ollama-backed AIService.
    if provider in {"ollama", "openai"}:
        return OllamaProvider(
            AIService(
                OllamaClient(
                    base_url=settings.ollama_base_url,
                    model=settings.ollama_model,
                    timeout_seconds=settings.ollama_timeout_seconds,
                    max_prompt_chars=settings.ollama_max_prompt_chars,
                    max_predict_tokens=settings.ollama_max_predict_tokens,
                    max_response_chars=settings.ollama_max_response_chars,
                    request_endpoint=settings.ollama_request_endpoint,
                    streaming_mode=settings.ollama_streaming_mode,
                ),
                retry_on_transient_error=settings.ollama_retry_on_transient_error,
                retry_delay_seconds=settings.ollama_retry_delay_seconds,
                fallback_model=settings.ollama_fallback_model,
            )
        )

    # Defensive fallback to avoid startup breakage if env parsing was bypassed.
    return OllamaProvider(
        AIService(
            OllamaClient(
                base_url=settings.ollama_base_url,
                model=settings.ollama_model,
                timeout_seconds=settings.ollama_timeout_seconds,
                max_prompt_chars=settings.ollama_max_prompt_chars,
                max_predict_tokens=settings.ollama_max_predict_tokens,
                max_response_chars=settings.ollama_max_response_chars,
                request_endpoint=settings.ollama_request_endpoint,
                streaming_mode=settings.ollama_streaming_mode,
            ),
            retry_on_transient_error=settings.ollama_retry_on_transient_error,
            retry_delay_seconds=settings.ollama_retry_delay_seconds,
            fallback_model=settings.ollama_fallback_model,
        )
    )
