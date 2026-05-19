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
                "WEBUI_SECRET_KEY=dotenv-secret-key-0123456789-abcdefghij",
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
    monkeypatch.setenv("WEBUI_SECRET_KEY", "shell-secret-key-0123456789-abcdefghij")
    monkeypatch.setenv("WEBUI_HOST", "127.0.0.1")
    monkeypatch.setenv("OLLAMA_MODEL", "legacy-shell-model")
    monkeypatch.delenv("AMO_ENV_OVERRIDE", raising=False)
    monkeypatch.setenv("DOTENV_PATH", str(env_file))

    settings = get_settings()

    assert settings.bot_token == "token-from-dotenv"
    assert settings.webui_password == "pw-from-dotenv"
    assert settings.webui_secret_key == "dotenv-secret-key-0123456789-abcdefghij"
    assert settings.webui_host == "0.0.0.0"
    assert settings.ollama_model == "llama3.1:8b"


def test_can_opt_out_from_dotenv_override_via_explicit_flag(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "BOT_TOKEN=token-from-dotenv",
                "WEBUI_PASSWORD=pw-from-dotenv",
                "WEBUI_SECRET_KEY=dotenv-secret-key-0123456789-abcdefghij",
                "WEBUI_HOST=0.0.0.0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BOT_TOKEN", "token-from-shell")
    monkeypatch.setenv("WEBUI_PASSWORD", "pw-from-shell")
    monkeypatch.setenv("WEBUI_SECRET_KEY", "shell-secret-key-0123456789-abcdefghij")
    monkeypatch.setenv("WEBUI_HOST", "127.0.0.1")
    monkeypatch.setenv("AMO_ENV_OVERRIDE", "0")
    monkeypatch.setenv("DOTENV_PATH", str(env_file))

    settings = get_settings()

    assert settings.bot_token == "token-from-shell"
    assert settings.webui_password == "pw-from-shell"
    assert settings.webui_secret_key == "shell-secret-key-0123456789-abcdefghij"
    assert settings.webui_host == "127.0.0.1"


def test_new_webui_security_env_values_are_parsed(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "BOT_TOKEN=token-from-dotenv",
                "WEBUI_PASSWORD=pw-from-dotenv",
                "WEBUI_SECRET_KEY=dotenv-secret-key-0123456789-abcdefghij",
                "WEBUI_PUBLIC_MODE=true",
                "WEBUI_REQUIRE_HTTPS=1",
                "WEBUI_SESSION_COOKIE_SECURE=false",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AMO_ENV_OVERRIDE", raising=False)
    monkeypatch.setenv("DOTENV_PATH", str(env_file))

    settings = get_settings()

    assert settings.webui_public_mode is True
    assert settings.webui_require_https is True
    assert settings.webui_session_cookie_secure is False


def test_ollama_retry_and_fallback_env_values_are_parsed(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "BOT_TOKEN=token-from-dotenv",
                "WEBUI_PASSWORD=pw-from-dotenv",
                "WEBUI_SECRET_KEY=dotenv-secret-key-0123456789-abcdefghij",
                "OLLAMA_RETRY_ON_TRANSIENT_ERROR=false",
                "OLLAMA_RETRY_DELAY_SECONDS=1.5",
                "OLLAMA_FALLBACK_MODEL=kimi-k2.5:cloud",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DOTENV_PATH", str(env_file))
    monkeypatch.delenv("AMO_ENV_OVERRIDE", raising=False)

    settings = get_settings()

    assert settings.ollama_retry_on_transient_error is False
    assert settings.ollama_retry_delay_seconds == 1.5
    assert settings.ollama_fallback_model == "kimi-k2.5:cloud"


def test_webui_login_delay_rejects_negative_values(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "BOT_TOKEN=token-from-dotenv",
                "WEBUI_PASSWORD=pw-from-dotenv",
                "WEBUI_SECRET_KEY=dotenv-secret-key-0123456789-abcdefghij",
                "WEBUI_LOGIN_DELAY_BASE_SECONDS=-0.1",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DOTENV_PATH", str(env_file))
    monkeypatch.delenv("AMO_ENV_OVERRIDE", raising=False)

    with pytest.raises(ValidationError) as exc_info:
        get_settings()

    monkeypatch.delenv("WEBUI_LOGIN_DELAY_BASE_SECONDS", raising=False)

    assert "WEBUI_LOGIN_DELAY_BASE_SECONDS" in str(exc_info.value)


def test_webui_login_delay_rejects_max_below_base(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "BOT_TOKEN=token-from-dotenv",
                "WEBUI_PASSWORD=pw-from-dotenv",
                "WEBUI_SECRET_KEY=dotenv-secret-key-0123456789-abcdefghij",
                "WEBUI_LOGIN_DELAY_BASE_SECONDS=1.5",
                "WEBUI_LOGIN_DELAY_MAX_SECONDS=1.0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DOTENV_PATH", str(env_file))
    monkeypatch.delenv("AMO_ENV_OVERRIDE", raising=False)

    with pytest.raises(ValidationError) as exc_info:
        get_settings()

    monkeypatch.delenv("WEBUI_LOGIN_DELAY_BASE_SECONDS", raising=False)
    monkeypatch.delenv("WEBUI_LOGIN_DELAY_MAX_SECONDS", raising=False)

    assert "WEBUI_LOGIN_DELAY_MAX_SECONDS must be >= WEBUI_LOGIN_DELAY_BASE_SECONDS" in str(exc_info.value)


def test_plugin_command_sandbox_enabled_defaults_false_and_can_be_enabled(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "BOT_TOKEN=token-from-dotenv",
                "WEBUI_PASSWORD=pw-from-dotenv",
                "WEBUI_SECRET_KEY=dotenv-secret-key-0123456789-abcdefghij",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DOTENV_PATH", str(env_file))
    monkeypatch.delenv("AMO_ENV_OVERRIDE", raising=False)
    monkeypatch.delenv("PLUGIN_COMMAND_SANDBOX_ENABLED", raising=False)
    monkeypatch.delenv("WEBUI_LOGIN_DELAY_BASE_SECONDS", raising=False)
    monkeypatch.delenv("WEBUI_LOGIN_DELAY_MAX_SECONDS", raising=False)

    settings_default = get_settings()
    assert settings_default.plugin_command_sandbox_enabled is False

    monkeypatch.setenv("PLUGIN_COMMAND_SANDBOX_ENABLED", "true")
    settings_enabled = get_settings()
    assert settings_enabled.plugin_command_sandbox_enabled is True


def test_no_secret_values_are_exposed_in_validation_error(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / ".env"
    secret_value = "super-secret-value"
    env_file.write_text(
        "\n".join(
            [
                "BOT_TOKEN=token-from-dotenv",
                "WEBUI_PASSWORD=pw-from-dotenv",
                "WEBUI_SECRET_KEY=dotenv-secret-key-0123456789-abcdefghij",
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
