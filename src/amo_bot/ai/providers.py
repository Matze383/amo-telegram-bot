from __future__ import annotations

from dataclasses import dataclass
import asyncio
from typing import Protocol

from amo_bot.ai.image_analyze_orchestrator import ImageAnalyzeProviderRequest, ImageAnalyzeProviderResult

from amo_bot.ai.ollama import OllamaClient
from amo_bot.ai.openai_provider import OpenAIProviderConfig, OpenAIRequestClient
from amo_bot.ai.anthropic_provider import AnthropicProviderConfig, AnthropicRequestClient
from amo_bot.ai.gemini_provider import GeminiProviderConfig, GeminiRequestClient
from amo_bot.ai.openrouter_provider import OpenRouterProviderConfig, OpenRouterRequestClient
from amo_bot.ai.groq_provider import GroqProviderConfig, GroqRequestClient
from amo_bot.ai.mistral_provider import MistralProviderConfig, MistralRequestClient
from amo_bot.ai.xai_provider import XAIProviderConfig, XAIRequestClient
from amo_bot.ai.deepseek_provider import DeepSeekProviderConfig, DeepSeekRequestClient
from amo_bot.ai.bedrock_provider import BedrockProviderConfig, BedrockRequestClient
from amo_bot.ai.together_provider import TogetherProviderConfig, TogetherRequestClient
from amo_bot.ai.fireworks_provider import FireworksProviderConfig, FireworksRequestClient
from amo_bot.ai.litellm_provider import LiteLLMProviderConfig, LiteLLMRequestClient
from amo_bot.ai.lmstudio_provider import LMStudioProviderConfig, LMStudioRequestClient
from amo_bot.ai.vllm_provider import VLLMProviderConfig, VLLMRequestClient
from amo_bot.ai.sglang_provider import SGLangProviderConfig, SGLangRequestClient
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


@dataclass(frozen=True, slots=True)
class GeminiProvider:
    config: GeminiProviderConfig

    @property
    def client(self) -> GeminiRequestClient:
        return GeminiRequestClient(config=self.config)

    async def ask(self, prompt: str) -> str:
        return await self.client.ask(prompt)


@dataclass(frozen=True, slots=True)
class OpenRouterProvider:
    config: OpenRouterProviderConfig

    @property
    def client(self) -> OpenRouterRequestClient:
        return OpenRouterRequestClient(config=self.config)

    async def ask(self, prompt: str) -> str:
        return await self.client.ask(prompt)


@dataclass(frozen=True, slots=True)
class GroqProvider:
    config: GroqProviderConfig

    @property
    def client(self) -> GroqRequestClient:
        return GroqRequestClient(config=self.config)

    async def ask(self, prompt: str) -> str:
        return await self.client.ask(prompt)


@dataclass(frozen=True, slots=True)
class MistralProvider:
    config: MistralProviderConfig

    @property
    def client(self) -> MistralRequestClient:
        return MistralRequestClient(config=self.config)

    async def ask(self, prompt: str) -> str:
        return await self.client.ask(prompt)




@dataclass(frozen=True, slots=True)
class XAIProvider:
    config: XAIProviderConfig

    @property
    def client(self) -> XAIRequestClient:
        return XAIRequestClient(config=self.config)

    async def ask(self, prompt: str) -> str:
        return await self.client.ask(prompt)


@dataclass(frozen=True, slots=True)
class DeepSeekProvider:
    config: DeepSeekProviderConfig

    @property
    def client(self) -> DeepSeekRequestClient:
        return DeepSeekRequestClient(config=self.config)

    async def ask(self, prompt: str) -> str:
        return await self.client.ask(prompt)


@dataclass(frozen=True, slots=True)
class TogetherProvider:
    config: TogetherProviderConfig

    @property
    def client(self) -> TogetherRequestClient:
        return TogetherRequestClient(config=self.config)

    async def ask(self, prompt: str) -> str:
        return await self.client.ask(prompt)


@dataclass(frozen=True, slots=True)
class FireworksProvider:
    config: FireworksProviderConfig

    @property
    def client(self) -> FireworksRequestClient:
        return FireworksRequestClient(config=self.config)

    async def ask(self, prompt: str) -> str:
        return await self.client.ask(prompt)


@dataclass(frozen=True, slots=True)
class BedrockProvider:
    config: BedrockProviderConfig

    @property
    def client(self) -> BedrockRequestClient:
        return BedrockRequestClient(config=self.config)

    async def ask(self, prompt: str) -> str:
        return await self.client.ask(prompt)


@dataclass(frozen=True, slots=True)
class LiteLLMProvider:
    config: LiteLLMProviderConfig

    @property
    def client(self) -> LiteLLMRequestClient:
        return LiteLLMRequestClient(config=self.config)

    async def ask(self, prompt: str) -> str:
        return await self.client.ask(prompt)


@dataclass(frozen=True, slots=True)
class LMStudioProvider:
    config: LMStudioProviderConfig

    @property
    def client(self) -> LMStudioRequestClient:
        return LMStudioRequestClient(config=self.config)

    async def ask(self, prompt: str) -> str:
        return await self.client.ask(prompt)


@dataclass(frozen=True, slots=True)
class VLLMProvider:
    config: VLLMProviderConfig

    @property
    def client(self) -> VLLMRequestClient:
        return VLLMRequestClient(config=self.config)


    async def ask(self, prompt: str) -> str:
        return await self.client.ask(prompt)


@dataclass(frozen=True, slots=True)
class SGLangProvider:
    config: SGLangProviderConfig

    @property
    def client(self) -> SGLangRequestClient:
        return SGLangRequestClient(config=self.config)

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

    if provider == "google":
        return GeminiProvider(
            config=GeminiProviderConfig(
                api_key=settings.gemini_api_key or "",
                model=settings.gemini_model,
                timeout_seconds=settings.gemini_timeout_seconds,
                base_url=settings.gemini_base_url,
            )
        )

    if provider == "openrouter":
        return OpenRouterProvider(
            config=OpenRouterProviderConfig(
                api_key=settings.openrouter_api_key or "",
                model=settings.openrouter_model,
                timeout_seconds=settings.openrouter_timeout_seconds,
                base_url=settings.openrouter_base_url,
            )
        )

    if provider == "groq":
        return GroqProvider(
            config=GroqProviderConfig(
                api_key=settings.groq_api_key or "",
                model=settings.groq_model,
                timeout_seconds=settings.groq_timeout_seconds,
                base_url=settings.groq_base_url,
            )
        )

    if provider == "mistral":
        return MistralProvider(
            config=MistralProviderConfig(
                api_key=settings.mistral_api_key or "",
                model=settings.mistral_model,
                timeout_seconds=settings.mistral_timeout_seconds,
                base_url=settings.mistral_base_url,
            )
        )

    if provider == "xai":
        return XAIProvider(
            config=XAIProviderConfig(
                api_key=settings.xai_api_key or "",
                model=settings.xai_model,
                timeout_seconds=settings.xai_timeout_seconds,
                base_url=settings.xai_base_url,
            )
        )

    if provider == "deepseek":
        return DeepSeekProvider(
            config=DeepSeekProviderConfig(
                api_key=settings.deepseek_api_key or "",
                model=settings.deepseek_model,
                timeout_seconds=settings.deepseek_timeout_seconds,
                base_url=settings.deepseek_base_url,
            )
        )

    if provider == "together":
        return TogetherProvider(
            config=TogetherProviderConfig(
                api_key=settings.together_api_key or "",
                model=settings.together_model,
                timeout_seconds=settings.together_timeout_seconds,
                base_url=settings.together_base_url,
            )
        )

    if provider == "fireworks":
        return FireworksProvider(
            config=FireworksProviderConfig(
                api_key=settings.fireworks_api_key or "",
                model=settings.fireworks_model,
                timeout_seconds=settings.fireworks_timeout_seconds,
                base_url=settings.fireworks_base_url,
            )
        )

    if provider == "amazon-bedrock":
        return BedrockProvider(
            config=BedrockProviderConfig(
                model=settings.bedrock_model,
                region=settings.bedrock_region or "",
                timeout_seconds=settings.bedrock_timeout_seconds,
                access_key_id=settings.aws_access_key_id,
                secret_access_key=settings.aws_secret_access_key,
                session_token=settings.aws_session_token,
            )
        )

    if provider == "litellm":
        return LiteLLMProvider(
            config=LiteLLMProviderConfig(
                api_key=settings.litellm_api_key or "",
                model=settings.litellm_model,
                timeout_seconds=settings.litellm_timeout_seconds,
                base_url=settings.litellm_base_url,
            )
        )

    if provider == "lmstudio":
        return LMStudioProvider(
            config=LMStudioProviderConfig(
                api_key=settings.lmstudio_api_key,
                model=settings.lmstudio_model,
                timeout_seconds=settings.lmstudio_timeout_seconds,
                base_url=settings.lmstudio_base_url,
            )
        )

    if provider == "vllm":
        return VLLMProvider(
            config=VLLMProviderConfig(
                api_key=settings.vllm_api_key,
                model=settings.vllm_model,
                timeout_seconds=settings.vllm_timeout_seconds,
                base_url=settings.vllm_base_url,
            )
        )

    if provider == "sglang":
        return SGLangProvider(
            config=SGLangProviderConfig(
                api_key=settings.sglang_api_key,
                model=settings.sglang_model,
                timeout_seconds=settings.sglang_timeout_seconds,
                base_url=settings.sglang_base_url,
            )
        )

    return _build_ollama_provider(settings)
