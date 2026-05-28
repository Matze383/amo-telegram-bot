from __future__ import annotations

import pytest

from amo_bot.ai.ollama import OllamaClient
from amo_bot.ai.openai_provider import OpenAIProviderConfig
from amo_bot.ai.anthropic_provider import AnthropicProviderConfig
from amo_bot.ai.gemini_provider import GeminiProviderConfig
from amo_bot.ai.litellm_provider import LiteLLMProviderConfig
from amo_bot.ai.providers import AnthropicProvider, BedrockProvider, DeepSeekProvider, FireworksProvider, GeminiProvider, GroqProvider, LiteLLMProvider, MistralProvider, OpenAIProvider, OpenRouterProvider, TogetherProvider, XAIProvider, build_ai_provider
from amo_bot.ai.service import AIService
from amo_bot.config.settings import Settings


def _settings(**overrides: object) -> Settings:
    base = {
        "BOT_TOKEN": "x",
        "WEBUI_PASSWORD": "pw",
        "WEBUI_SECRET_KEY": "secret",
    }
    base.update(overrides)
    return Settings.model_validate(base)


def test_default_provider_is_ollama_and_builds_provider() -> None:
    settings = _settings()
    assert settings.ai_provider == "ollama"

    provider = build_ai_provider(settings)
    assert isinstance(provider.service, AIService)


def test_openai_provider_selection_builds_provider_config_only() -> None:
    settings = _settings(AI_PROVIDER="openai", OPENAI_API_KEY="sk-secret")
    provider = build_ai_provider(settings)
    assert isinstance(provider, OpenAIProvider)
    assert isinstance(provider.config, OpenAIProviderConfig)
    assert provider.config.model == "gpt-4o-mini"


def test_ollama_provider_selection_builds_provider() -> None:
    settings = _settings(AI_PROVIDER="ollama")
    provider = build_ai_provider(settings)
    assert isinstance(provider.service, AIService)


def test_ollama_request_endpoint_defaults_to_generate() -> None:
    settings = _settings()
    assert settings.ollama_request_endpoint == "generate"


def test_openai_requires_api_key() -> None:
    with pytest.raises(ValueError, match="OPENAI_API_KEY is required when AI_PROVIDER=openai"):
        _settings(AI_PROVIDER="openai")


def test_openai_api_key_is_trimmed_not_logged() -> None:
    settings = _settings(AI_PROVIDER="openai", OPENAI_API_KEY="  sk-real-key  ")
    provider = build_ai_provider(settings)
    assert isinstance(provider, OpenAIProvider)
    assert provider.config.api_key == "sk-real-key"
    assert provider.config.redacted_dict()["api_key_preview"] == "***"


def test_ollama_request_endpoint_chat_is_opt_in() -> None:
    settings = _settings(OLLAMA_REQUEST_ENDPOINT="chat")
    provider = build_ai_provider(settings)
    assert isinstance(provider.service.client, OllamaClient)
    assert provider.service.client.request_endpoint == "chat"


def test_ollama_request_endpoint_invalid_fails_closed() -> None:
    with pytest.raises(ValueError, match="OLLAMA_REQUEST_ENDPOINT must be one of: generate, chat"):
        _settings(OLLAMA_REQUEST_ENDPOINT="stream")


def test_ollama_streaming_mode_defaults_off() -> None:
    settings = _settings()
    provider = build_ai_provider(settings)
    assert provider.service.client.streaming_mode == "off"


@pytest.mark.parametrize("mode", ["off", "collect_only", "live_edit"])
def test_ollama_streaming_mode_accepts_known_values(mode: str) -> None:
    settings = _settings(OLLAMA_STREAMING_MODE=mode)
    provider = build_ai_provider(settings)
    assert provider.service.client.streaming_mode == mode


def test_ollama_streaming_mode_invalid_fails_closed() -> None:
    with pytest.raises(ValueError, match="OLLAMA_STREAMING_MODE must be one of: off, collect_only, live_edit"):
        _settings(OLLAMA_STREAMING_MODE="stream")


@pytest.mark.parametrize("value", ["", "OPEN_AI", "123"])
def test_invalid_provider_fails_validation(value: str) -> None:
    with pytest.raises(ValueError):
        _settings(AI_PROVIDER=value)


def test_together_provider_selection_builds_provider_config_only() -> None:
    settings = _settings(AI_PROVIDER="together", TOGETHER_API_KEY="together-credential-placeholder")
    provider = build_ai_provider(settings)
    assert isinstance(provider, TogetherProvider)
    assert provider.config.model == "together/moonshotai/Kimi-K2.5"


def test_together_requires_api_key() -> None:
    with pytest.raises(ValueError, match="TOGETHER_API_KEY is required when AI_PROVIDER=together"):
        _settings(AI_PROVIDER="together")


def test_together_api_key_is_trimmed_not_logged() -> None:
    settings = _settings(AI_PROVIDER="together", TOGETHER_API_KEY="  trimmed-together-credential  ")
    provider = build_ai_provider(settings)
    assert isinstance(provider, TogetherProvider)
    assert provider.config.api_key == "trimmed-together-credential"
    assert provider.config.redacted_dict()["api_key_preview"] == "***"


def test_groq_provider_selection_builds_provider_config_only() -> None:
    settings = _settings(AI_PROVIDER="groq", GROQ_API_KEY="fake-groq-api-key")
    provider = build_ai_provider(settings)
    assert isinstance(provider, GroqProvider)
    assert provider.config.model == "groq/llama-3.1-8b-instant"


def test_groq_requires_api_key() -> None:
    with pytest.raises(ValueError, match="GROQ_API_KEY is required when AI_PROVIDER=groq"):
        _settings(AI_PROVIDER="groq")


def test_groq_api_key_is_trimmed_not_logged() -> None:
    settings = _settings(AI_PROVIDER="groq", GROQ_API_KEY="  trimmed-groq-api-key  ")
    provider = build_ai_provider(settings)
    assert isinstance(provider, GroqProvider)
    assert provider.config.api_key == "trimmed-groq-api-key"
    assert provider.config.redacted_dict()["api_key_preview"] == "***"


def test_ollama_provider_analyze_uses_image_path(monkeypatch, tmp_path) -> None:
    settings = _settings(AI_PROVIDER="ollama")
    provider = build_ai_provider(settings)
    image_path = tmp_path / "img.png"
    image_path.write_bytes(b"abc")
    seen: dict[str, object] = {}

    async def _ask_with_images(self, prompt: str, *, image_paths: tuple[str, ...]):
        seen["prompt"] = prompt
        seen["image_paths"] = image_paths
        return "summary"

    monkeypatch.setattr(AIService, "ask_with_images", _ask_with_images)

    from amo_bot.ai.image_analyze_orchestrator import ImageAnalyzeProviderRequest

    result = provider.analyze(
        ImageAnalyzeProviderRequest(
            image_ref="telegram-file:u1",
            prompt="describe",
            user_id=1,
            chat_id=2,
            message_thread_id=3,
            image_path=str(image_path),
        )
    )

    assert result.provider == "ollama"
    assert result.summary == "summary"
    assert seen == {"prompt": "describe", "image_paths": (str(image_path),)}


def test_anthropic_provider_selection_builds_provider_config_only() -> None:
    settings = _settings(AI_PROVIDER="anthropic", ANTHROPIC_API_KEY="ak-secret")
    provider = build_ai_provider(settings)
    assert isinstance(provider, AnthropicProvider)
    assert isinstance(provider.config, AnthropicProviderConfig)
    assert provider.config.model == "anthropic/claude-opus-4-6"


def test_anthropic_requires_api_key() -> None:
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY is required when AI_PROVIDER=anthropic"):
        _settings(AI_PROVIDER="anthropic")


def test_anthropic_api_key_is_trimmed_not_logged() -> None:
    settings = _settings(AI_PROVIDER="anthropic", ANTHROPIC_API_KEY="  ak-real-key  ")
    provider = build_ai_provider(settings)
    assert isinstance(provider, AnthropicProvider)
    assert provider.config.api_key == "ak-real-key"
    assert provider.config.redacted_dict()["api_key_preview"] == "***"


def test_gemini_provider_selection_builds_provider_config_only() -> None:
    settings = _settings(AI_PROVIDER="google", GEMINI_API_KEY="gk-secret")
    provider = build_ai_provider(settings)
    assert isinstance(provider, GeminiProvider)
    assert isinstance(provider.config, GeminiProviderConfig)
    assert provider.config.model == "google/gemini-3-flash-preview"


def test_gemini_requires_either_api_key_env() -> None:
    with pytest.raises(ValueError, match="When AI_PROVIDER=google, set GEMINI_API_KEY or GOOGLE_API_KEY"):
        _settings(AI_PROVIDER="google")


def test_gemini_api_key_is_trimmed_not_logged() -> None:
    settings = _settings(AI_PROVIDER="google", GEMINI_API_KEY="  gk-real-key  ")
    provider = build_ai_provider(settings)
    assert isinstance(provider, GeminiProvider)
    assert provider.config.api_key == "gk-real-key"
    assert provider.config.redacted_dict()["api_key_preview"] == "***"


def test_google_api_key_is_used_when_gemini_api_key_missing() -> None:
    settings = _settings(AI_PROVIDER="google", GOOGLE_API_KEY="  gg-real-key  ")
    provider = build_ai_provider(settings)
    assert isinstance(provider, GeminiProvider)
    assert provider.config.api_key == "gg-real-key"


def test_gemini_api_key_preferred_when_both_set() -> None:
    settings = _settings(
        AI_PROVIDER="google",
        GEMINI_API_KEY="gk-real-key",
        GOOGLE_API_KEY="gg-real-key",
    )
    provider = build_ai_provider(settings)
    assert isinstance(provider, GeminiProvider)
    assert provider.config.api_key == "gk-real-key"


def test_openrouter_provider_selection_builds_provider_config_only() -> None:
    settings = _settings(AI_PROVIDER="openrouter", OPENROUTER_API_KEY="or-secret")
    provider = build_ai_provider(settings)
    assert isinstance(provider, OpenRouterProvider)
    assert provider.config.model == "openrouter/auto"


def test_openrouter_requires_api_key() -> None:
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY is required when AI_PROVIDER=openrouter"):
        _settings(AI_PROVIDER="openrouter")


def test_openrouter_api_key_is_trimmed_not_logged() -> None:
    settings = _settings(AI_PROVIDER="openrouter", OPENROUTER_API_KEY="  or-real-key  ")
    provider = build_ai_provider(settings)
    assert isinstance(provider, OpenRouterProvider)
    assert provider.config.api_key == "or-real-key"
    assert provider.config.redacted_dict()["api_key_preview"] == "***"


def test_mistral_provider_selection_builds_provider_config_only() -> None:
    settings = _settings(AI_PROVIDER="mistral", MISTRAL_API_KEY="mistral-credential-placeholder")
    provider = build_ai_provider(settings)
    assert isinstance(provider, MistralProvider)
    assert provider.config.model == "mistral/mistral-large-latest"


def test_mistral_requires_api_key() -> None:
    with pytest.raises(ValueError, match="MISTRAL_API_KEY is required when AI_PROVIDER=mistral"):
        _settings(AI_PROVIDER="mistral")


def test_mistral_api_key_is_trimmed_not_logged() -> None:
    settings = _settings(AI_PROVIDER="mistral", MISTRAL_API_KEY="  trimmed-mistral-credential  ")
    provider = build_ai_provider(settings)
    assert isinstance(provider, MistralProvider)
    assert provider.config.api_key == "trimmed-mistral-credential"
    assert provider.config.redacted_dict()["api_key_preview"] == "***"


def test_xai_provider_selection_builds_provider_config_only() -> None:
    settings = _settings(AI_PROVIDER="xai", XAI_API_KEY="xai-credential-placeholder")
    provider = build_ai_provider(settings)
    assert isinstance(provider, XAIProvider)
    assert provider.config.model == "xai/grok-4.3"


def test_xai_requires_api_key() -> None:
    with pytest.raises(ValueError, match="XAI_API_KEY is required when AI_PROVIDER=xai"):
        _settings(AI_PROVIDER="xai")


def test_xai_api_key_is_trimmed_not_logged() -> None:
    settings = _settings(AI_PROVIDER="xai", XAI_API_KEY="  xai-credential-placeholder  ")
    provider = build_ai_provider(settings)
    assert isinstance(provider, XAIProvider)
    assert provider.config.api_key == "xai-credential-placeholder"
    assert provider.config.redacted_dict()["api_key_preview"] == "***"


def test_deepseek_provider_selection_builds_provider_config_only() -> None:
    settings = _settings(AI_PROVIDER="deepseek", DEEPSEEK_API_KEY="deepseek-credential-placeholder")
    provider = build_ai_provider(settings)
    assert isinstance(provider, DeepSeekProvider)
    assert provider.config.model == "deepseek/deepseek-v4-flash"


def test_deepseek_requires_api_key() -> None:
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY is required when AI_PROVIDER=deepseek"):
        _settings(AI_PROVIDER="deepseek")


def test_deepseek_api_key_is_trimmed_not_logged() -> None:
    settings = _settings(AI_PROVIDER="deepseek", DEEPSEEK_API_KEY="  deepseek-credential-placeholder  ")
    provider = build_ai_provider(settings)
    assert isinstance(provider, DeepSeekProvider)
    assert provider.config.api_key == "deepseek-credential-placeholder"
    assert provider.config.redacted_dict()["api_key_preview"] == "***"


def test_bedrock_provider_selection_builds_provider_config_only() -> None:
    settings = _settings(
        AI_PROVIDER="amazon-bedrock",
        BEDROCK_REGION="eu-central-1",
        BEDROCK_MODEL="amazon-bedrock/anthropic.claude-3-haiku-20240307-v1:0",
    )
    provider = build_ai_provider(settings)
    assert isinstance(provider, BedrockProvider)
    assert provider.config.region == "eu-central-1"


def test_bedrock_requires_region() -> None:
    with pytest.raises(ValueError, match="BEDROCK_REGION \(or AWS_REGION/AWS_DEFAULT_REGION\) is required"):
        _settings(AI_PROVIDER="amazon-bedrock")


def test_bedrock_region_falls_back_to_aws_region() -> None:
    settings = _settings(AI_PROVIDER="amazon-bedrock", AWS_REGION="eu-west-1")
    provider = build_ai_provider(settings)
    assert isinstance(provider, BedrockProvider)
    assert provider.config.region == "eu-west-1"


def test_bedrock_partial_aws_credentials_fail_validation() -> None:
    with pytest.raises(ValueError, match="set both AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY"):
        _settings(
            AI_PROVIDER="amazon-bedrock",
            BEDROCK_REGION="eu-central-1",
            AWS_ACCESS_KEY_ID="access-placeholder",
        )


def test_bedrock_optional_credentials_trimmed_and_redacted() -> None:
    settings = _settings(
        AI_PROVIDER="amazon-bedrock",
        BEDROCK_REGION="eu-central-1",
        AWS_ACCESS_KEY_ID="  access-placeholder  ",
        AWS_SECRET_ACCESS_KEY="  credential-placeholder  ",
    )
    provider = build_ai_provider(settings)
    assert isinstance(provider, BedrockProvider)
    assert provider.config.access_key_id == "access-placeholder"
    assert provider.config.secret_access_key == "credential-placeholder"
    redacted = provider.config.redacted_dict()
    assert redacted["aws_access_key_id_present"] is True
    assert redacted["aws_secret_access_key_present"] is True


def test_fireworks_provider_selection_builds_provider_config_only() -> None:
    settings = _settings(AI_PROVIDER="fireworks", FIREWORKS_API_KEY="fireworks-credential-placeholder")
    provider = build_ai_provider(settings)
    assert isinstance(provider, FireworksProvider)
    assert provider.config.model == "fireworks/accounts/fireworks/models/llama-v3p1-8b-instruct"


def test_fireworks_requires_api_key() -> None:
    with pytest.raises(ValueError, match="FIREWORKS_API_KEY is required when AI_PROVIDER=fireworks"):
        _settings(AI_PROVIDER="fireworks")


def test_fireworks_api_key_is_trimmed_not_logged() -> None:
    settings = _settings(AI_PROVIDER="fireworks", FIREWORKS_API_KEY="  fireworks-credential-placeholder  ")
    provider = build_ai_provider(settings)
    assert isinstance(provider, FireworksProvider)
    assert provider.config.api_key == "fireworks-credential-placeholder"
    assert provider.config.redacted_dict()["api_key_preview"] == "***"


def test_litellm_provider_selection_builds_provider_config_only() -> None:
    settings = _settings(AI_PROVIDER="litellm", LITELLM_API_KEY="litellm-credential-placeholder")
    provider = build_ai_provider(settings)
    assert isinstance(provider, LiteLLMProvider)
    assert isinstance(provider.config, LiteLLMProviderConfig)
    assert provider.config.model == "openai/gpt-4o-mini"


def test_litellm_requires_api_key() -> None:
    with pytest.raises(ValueError, match="LITELLM_API_KEY is required when AI_PROVIDER=litellm"):
        _settings(AI_PROVIDER="litellm")


def test_litellm_api_key_is_trimmed_not_logged() -> None:
    settings = _settings(AI_PROVIDER="litellm", LITELLM_API_KEY="  trimmed-litellm-credential  ")
    provider = build_ai_provider(settings)
    assert isinstance(provider, LiteLLMProvider)
    assert provider.config.api_key == "trimmed-litellm-credential"
    assert provider.config.redacted_dict()["api_key_preview"] == "***"


def test_litellm_requires_model() -> None:
    with pytest.raises(ValueError, match="LITELLM_MODEL is required when AI_PROVIDER=litellm"):
        _settings(AI_PROVIDER="litellm", LITELLM_API_KEY="key", LITELLM_MODEL="  ")


def test_litellm_requires_non_empty_base_url() -> None:
    with pytest.raises(ValueError, match="LITELLM_BASE_URL must not be empty"):
        _settings(AI_PROVIDER="litellm", LITELLM_API_KEY="key", LITELLM_BASE_URL="  ")


def test_litellm_regression_ollama_still_works() -> None:
    settings = _settings(AI_PROVIDER="ollama")
    provider = build_ai_provider(settings)
    assert isinstance(provider.service, AIService)


def test_litellm_regression_openai_still_works() -> None:
    settings = _settings(AI_PROVIDER="openai", OPENAI_API_KEY="sk-test")
    provider = build_ai_provider(settings)
    assert isinstance(provider, OpenAIProvider)
