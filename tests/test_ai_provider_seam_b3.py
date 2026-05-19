from __future__ import annotations

import pytest

from amo_bot.ai.ollama import OllamaClient
from amo_bot.ai.providers import build_ai_provider
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


def test_default_provider_is_openai_and_builds_provider() -> None:
    settings = _settings()
    assert settings.ai_provider == "openai"

    provider = build_ai_provider(settings)
    assert isinstance(provider.service, AIService)


def test_ollama_provider_selection_builds_provider() -> None:
    settings = _settings(AI_PROVIDER="ollama")
    provider = build_ai_provider(settings)
    assert isinstance(provider.service, AIService)


def test_ollama_request_endpoint_defaults_to_generate() -> None:
    settings = _settings()
    assert settings.ollama_request_endpoint == "generate"


def test_ollama_request_endpoint_chat_is_opt_in() -> None:
    settings = _settings(OLLAMA_REQUEST_ENDPOINT="chat")
    provider = build_ai_provider(settings)
    assert isinstance(provider.service.client, OllamaClient)
    assert provider.service.client.request_endpoint == "chat"


def test_ollama_request_endpoint_invalid_fails_closed() -> None:
    with pytest.raises(ValueError, match="OLLAMA_REQUEST_ENDPOINT must be one of: generate, chat"):
        _settings(OLLAMA_REQUEST_ENDPOINT="stream")


@pytest.mark.parametrize("value", ["", "anthropic", "OPEN_AI", "123"])
def test_invalid_provider_fails_validation(value: str) -> None:
    with pytest.raises(ValueError):
        _settings(AI_PROVIDER=value)
