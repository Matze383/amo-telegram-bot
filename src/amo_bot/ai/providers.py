from __future__ import annotations

from dataclasses import dataclass
import asyncio
from typing import Protocol

from amo_bot.ai.image_analyze_orchestrator import ImageAnalyzeProviderRequest, ImageAnalyzeProviderResult

from amo_bot.ai.ollama import OllamaClient
from amo_bot.ai.openai_provider import OpenAIProviderConfig, OpenAIRequestClient
from amo_bot.ai.anthropic_provider import AnthropicProviderConfig, AnthropicRequestClient
from amo_bot.ai.service import AIService
from amo_bot.config.settings import Settings


class AIProvider(Protocol):
    async def ask(self, prompt: str) -> str: ...


@dataclass(frozen=True, slots=True)
class OllamaProvider:
    service: AIService

    @property
    def name(self) -> str:
        return "ollama"

    async def ask(self, prompt: str) -> str:
        return await self.service.ask(prompt)

    async def analyze_async(self, request: ImageAnalyzeProviderRequest) -> ImageAnalyzeProviderResult:
        if not request.image_path:
            raise RuntimeError("missing image path")
        summary = await self.service.ask_with_images(
            request.prompt or "Describe this image.",
            image_paths=(request.image_path,),
        )
        return ImageAnalyzeProviderResult(provider=self.name, summary=summary)

    def analyze(self, request: ImageAnalyzeProviderRequest) -> ImageAnalyzeProviderResult:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.analyze_async(request))
        raise RuntimeError("OllamaProvider.analyze() cannot run inside an active event loop; use analyze_async()")


@dataclass(frozen=True, slots=True)
class OpenAIProvider:
    config: OpenAIProviderConfig

    @property
    def client(self) -> OpenAIRequestClient:
        return OpenAIRequestClient(config=self.config)

    async def ask(self, prompt: str) -> str:
        return await self.client.ask(prompt)


@dataclass(frozen=True, slots=True)
class AnthropicProvider:
    config: AnthropicProviderConfig

    @property
    def client(self) -> AnthropicRequestClient:
        return AnthropicRequestClient(config=self.config)

    async def ask(self, prompt: str) -> str:
        return await self.client.ask(prompt)


def _build_ollama_provider(settings: Settings) -> OllamaProvider:
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


def build_ai_provider(settings: Settings) -> AIProvider:
    provider = settings.ai_provider.strip().casefold()

    if provider == "ollama":
        return _build_ollama_provider(settings)

    if provider == "openai":
        return OpenAIProvider(
            config=OpenAIProviderConfig(
                api_key=settings.openai_api_key or "",
                model=settings.openai_model,
                timeout_seconds=settings.openai_timeout_seconds,
            )
        )

    if provider == "anthropic":
        return AnthropicProvider(
            config=AnthropicProviderConfig(
                api_key=settings.anthropic_api_key or "",
                model=settings.anthropic_model,
                timeout_seconds=settings.anthropic_timeout_seconds,
                base_url=settings.anthropic_base_url,
            )
        )

    return _build_ollama_provider(settings)
