from __future__ import annotations

import pytest

from amo_bot.ai.ollama import OllamaClient
from amo_bot.ai.openai_provider import OpenAIProviderConfig
from amo_bot.ai.providers import OpenAIProvider, build_ai_provider
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


@pytest.mark.parametrize("value", ["", "anthropic", "OPEN_AI", "123"])
def test_invalid_provider_fails_validation(value: str) -> None:
    with pytest.raises(ValueError):
        _settings(AI_PROVIDER=value)
