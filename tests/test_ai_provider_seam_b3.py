from __future__ import annotations

import pytest

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


@pytest.mark.parametrize("value", ["", "anthropic", "OPEN_AI", "123"])
def test_invalid_provider_fails_validation(value: str) -> None:
    with pytest.raises(ValueError):
        _settings(AI_PROVIDER=value)
