from __future__ import annotations

import pytest
from pydantic import ValidationError

from amo_bot.config.settings import get_settings


def test_dotenv_overrides_existing_environment_variables(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "BOT_TOKEN=token-from-dotenv",
                "WEBUI_PASSWORD=pw-from-dotenv",
                "WEBUI_HOST=0.0.0.0",
                "OLLAMA_MODEL=llama3.1:8b",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BOT_TOKEN", "token-from-shell")
    monkeypatch.setenv("WEBUI_PASSWORD", "pw-from-shell")
    monkeypatch.setenv("WEBUI_HOST", "127.0.0.1")
    monkeypatch.setenv("OLLAMA_MODEL", "legacy-shell-model")
    monkeypatch.delenv("AMO_ENV_OVERRIDE", raising=False)
    monkeypatch.setenv("DOTENV_PATH", str(env_file))

    settings = get_settings()

    assert settings.bot_token == "token-from-dotenv"
    assert settings.webui_password == "pw-from-dotenv"
    assert settings.webui_host == "0.0.0.0"
    assert settings.ollama_model == "llama3.1:8b"


def test_can_opt_out_from_dotenv_override_via_explicit_flag(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "BOT_TOKEN=token-from-dotenv",
                "WEBUI_PASSWORD=pw-from-dotenv",
                "WEBUI_HOST=0.0.0.0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BOT_TOKEN", "token-from-shell")
    monkeypatch.setenv("WEBUI_PASSWORD", "pw-from-shell")
    monkeypatch.setenv("WEBUI_HOST", "127.0.0.1")
    monkeypatch.setenv("AMO_ENV_OVERRIDE", "0")
    monkeypatch.setenv("DOTENV_PATH", str(env_file))

    settings = get_settings()

    assert settings.bot_token == "token-from-shell"
    assert settings.webui_password == "pw-from-shell"
    assert settings.webui_host == "127.0.0.1"


def test_no_secret_values_are_exposed_in_validation_error(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / ".env"
    secret_value = "super-secret-value"
    env_file.write_text(
        "\n".join(
            [
                "BOT_TOKEN=token-from-dotenv",
                "WEBUI_PASSWORD=pw-from-dotenv",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BOT_TOKEN", secret_value)
    monkeypatch.setenv("WEBUI_PASSWORD", secret_value)
    monkeypatch.setenv("WEBUI_PORT", "not-an-int")
    monkeypatch.setenv("AMO_ENV_OVERRIDE", "1")
    monkeypatch.setenv("DOTENV_PATH", str(env_file))

    with pytest.raises(ValidationError) as exc_info:
        get_settings()

    message = str(exc_info.value)
    assert "WEBUI_PORT" in message
    assert secret_value not in message
