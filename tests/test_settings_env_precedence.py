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
    monkeypatch.delenv("AMO_VECTOR_PROVIDER", raising=False)
    monkeypatch.delenv("AMO_VECTOR_EMBEDDING_PROVIDER", raising=False)
    monkeypatch.delenv("AMO_VECTOR_EMBEDDING_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("WEBUI_LOGIN_DELAY_BASE_SECONDS", raising=False)
    monkeypatch.delenv("WEBUI_LOGIN_DELAY_MAX_SECONDS", raising=False)

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


def test_current_info_searxng_uses_legacy_base_url_alias_without_overriding_primary(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "BOT_TOKEN=token-from-dotenv",
                "WEBUI_PASSWORD=pw-from-dotenv",
                "WEBUI_SECRET_KEY=dotenv-secret-key-0123456789-abcdefghij",
                "SEARXNG_BASE_URL=https://legacy-searx.example",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DOTENV_PATH", str(env_file))
    monkeypatch.delenv("AMO_ENV_OVERRIDE", raising=False)
    monkeypatch.delenv("AMO_SEARXNG_URL", raising=False)
    monkeypatch.delenv("WEBUI_LOGIN_DELAY_BASE_SECONDS", raising=False)
    monkeypatch.delenv("WEBUI_LOGIN_DELAY_MAX_SECONDS", raising=False)

    settings = get_settings()

    assert settings.amo_searxng_url == "https://legacy-searx.example"

    monkeypatch.setenv("AMO_SEARXNG_URL", "https://current-info-searx.example")

    overridden = get_settings()

    assert overridden.amo_searxng_url == "https://current-info-searx.example"


def test_vector_qdrant_env_aliases_are_parsed_without_secret_leak(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "BOT_TOKEN=token-from-dotenv",
                "WEBUI_PASSWORD=pw-from-dotenv",
                "WEBUI_SECRET_KEY=dotenv-secret-key-0123456789-abcdefghij",
                "AMO_VECTOR_ENABLED=true",
                "AMO_VECTOR_PROVIDER=qdrant",
                "QDRANT_URL=http://qdrant.local:6333/",
                "QDRANT_API_KEY=super-secret-qdrant-key",
                "AMO_VECTOR_EMBEDDING_MODEL=nomic-embed-text",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DOTENV_PATH", str(env_file))
    monkeypatch.delenv("AMO_ENV_OVERRIDE", raising=False)
    monkeypatch.delenv("AMO_VECTOR_URL", raising=False)
    monkeypatch.delenv("AMO_VECTOR_API_KEY", raising=False)
    monkeypatch.delenv("WEBUI_LOGIN_DELAY_BASE_SECONDS", raising=False)
    monkeypatch.delenv("WEBUI_LOGIN_DELAY_MAX_SECONDS", raising=False)

    settings = get_settings()

    assert settings.amo_vector_enabled is True
    assert settings.amo_vector_provider == "qdrant"
    assert settings.amo_vector_url == "http://qdrant.local:6333"
    assert settings.amo_vector_api_key == "super-secret-qdrant-key"


def test_vector_postgres_defaults_use_ollama_nomic_embedding_model(monkeypatch, tmp_path) -> None:
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
    monkeypatch.delenv("AMO_VECTOR_PROVIDER", raising=False)
    monkeypatch.delenv("AMO_VECTOR_EMBEDDING_PROVIDER", raising=False)
    monkeypatch.delenv("AMO_VECTOR_EMBEDDING_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("WEBUI_LOGIN_DELAY_BASE_SECONDS", raising=False)
    monkeypatch.delenv("WEBUI_LOGIN_DELAY_MAX_SECONDS", raising=False)

    settings = get_settings()

    assert settings.ollama_model == "kimi-k2.6"
    assert settings.amo_vector_provider == "postgres"
    assert settings.amo_vector_embedding_provider == "ollama"
    assert settings.amo_vector_embedding_model == "nomic-embed-text-v2-moe:latest"


def test_current_info_release_defaults_and_issue_76_env_values_are_parsed(monkeypatch, tmp_path) -> None:
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
    monkeypatch.delenv("WEBUI_LOGIN_DELAY_BASE_SECONDS", raising=False)
    monkeypatch.delenv("WEBUI_LOGIN_DELAY_MAX_SECONDS", raising=False)
    for key in (
        "AMO_SEARCH_MAX_RESULTS",
        "AMO_SEARXNG_TIMEOUT_SECONDS",
        "AMO_BRAVE_SEARCH_TIMEOUT_SECONDS",
        "AMO_SEARCH_MIN_HOST_DIVERSITY",
        "AMO_CURRENT_INFO_MAX_SEARCH_PROVIDER_RUNS_PER_RESPONSE",
        "AMO_CURRENT_INFO_MAX_FETCH_RUNS_PER_RESPONSE",
        "AMO_CURRENT_INFO_MAX_TOTAL_PROVIDER_RUNS_PER_RESPONSE",
        "AMO_CURRENT_INFO_PROVIDER_RATE_LIMIT_PER_MINUTE",
        "AMO_BRAVE_SEARCH_QUOTA_PER_MINUTE",
        "AMO_CRAWLEE_MAX_CONCURRENT_PER_HOST",
        "AMO_CURRENT_INFO_DEBUG_OUTPUT",
    ):
        monkeypatch.delenv(key, raising=False)

    settings = get_settings()

    assert settings.amo_search_max_results == 10
    assert settings.amo_searxng_timeout_seconds == 30.0
    assert settings.amo_brave_search_timeout_seconds == 30.0
    assert settings.amo_search_min_host_diversity == 3
    assert settings.amo_current_info_max_search_provider_runs_per_response == 2
    assert settings.amo_current_info_max_fetch_runs_per_response == 3
    assert settings.amo_current_info_max_total_provider_runs_per_response == 8
    assert settings.amo_current_info_provider_rate_limit_per_minute == 60
    assert settings.amo_brave_search_quota_per_minute == 30
    assert settings.amo_crawlee_max_concurrent_per_host == 2
    assert settings.amo_current_info_debug_output is False

    monkeypatch.setenv("AMO_SEARCH_MAX_RESULTS", "7")
    monkeypatch.setenv("AMO_SEARXNG_TIMEOUT_SECONDS", "4.5")
    monkeypatch.setenv("AMO_BRAVE_SEARCH_TIMEOUT_SECONDS", "6.5")
    monkeypatch.setenv("AMO_SEARCH_MIN_HOST_DIVERSITY", "2")
    monkeypatch.setenv("AMO_CURRENT_INFO_MAX_SEARCH_PROVIDER_RUNS_PER_RESPONSE", "3")
    monkeypatch.setenv("AMO_CURRENT_INFO_MAX_FETCH_RUNS_PER_RESPONSE", "4")
    monkeypatch.setenv("AMO_CURRENT_INFO_MAX_TOTAL_PROVIDER_RUNS_PER_RESPONSE", "9")
    monkeypatch.setenv("AMO_CURRENT_INFO_PROVIDER_RATE_LIMIT_PER_MINUTE", "120")
    monkeypatch.setenv("AMO_BRAVE_SEARCH_QUOTA_PER_MINUTE", "40")
    monkeypatch.setenv("AMO_CRAWLEE_MAX_CONCURRENT_PER_HOST", "5")
    monkeypatch.setenv("AMO_CURRENT_INFO_DEBUG_OUTPUT", "true")

    overridden = get_settings()

    assert overridden.amo_search_max_results == 7
    assert overridden.amo_searxng_timeout_seconds == 4.5
    assert overridden.amo_brave_search_timeout_seconds == 6.5
    assert overridden.amo_search_min_host_diversity == 2
    assert overridden.amo_current_info_max_search_provider_runs_per_response == 3
    assert overridden.amo_current_info_max_fetch_runs_per_response == 4
    assert overridden.amo_current_info_max_total_provider_runs_per_response == 9
    assert overridden.amo_current_info_provider_rate_limit_per_minute == 120
    assert overridden.amo_brave_search_quota_per_minute == 40
    assert overridden.amo_crawlee_max_concurrent_per_host == 5
    assert overridden.amo_current_info_debug_output is True


def test_gpt_researcher_env_values_are_parsed(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "BOT_TOKEN=token-from-dotenv",
                "WEBUI_PASSWORD=pw-from-dotenv",
                "WEBUI_SECRET_KEY=dotenv-secret-key-0123456789-abcdefghij",
                "AMO_GPT_RESEARCHER_ENABLED=true",
                "AMO_RESEARCH_MODEL_PROVIDER=OLLAMA",
                "AMO_RESEARCH_FAST_MODEL=fast-local",
                "AMO_RESEARCH_SMART_MODEL=smart-local",
                "AMO_RESEARCH_STRATEGIC_MODEL=strategic-local",
                "AMO_RESEARCH_TIMEOUT_SECONDS=180",
                "AMO_RESEARCH_MAX_SOURCES=9",
                "AMO_RESEARCH_MAX_CONTEXT_CHARS=15000",
                "AMO_RESEARCH_DEEP_BREADTH=4",
                "AMO_RESEARCH_DEEP_DEPTH=3",
                "AMO_RESEARCH_DEEP_CONCURRENCY=2",
                "AMO_RESEARCH_VECTOR_COLLECTION=custom_research_chunks",
                "AMO_RESEARCH_REPORT_WORDS=1200",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DOTENV_PATH", str(env_file))
    monkeypatch.delenv("AMO_ENV_OVERRIDE", raising=False)
    monkeypatch.delenv("WEBUI_LOGIN_DELAY_BASE_SECONDS", raising=False)
    monkeypatch.delenv("WEBUI_LOGIN_DELAY_MAX_SECONDS", raising=False)

    settings = get_settings()

    assert settings.amo_gpt_researcher_enabled is True
    assert settings.amo_research_model_provider == "ollama"
    assert settings.amo_research_fast_model == "fast-local"
    assert settings.amo_research_smart_model == "smart-local"
    assert settings.amo_research_strategic_model == "strategic-local"
    assert settings.amo_research_timeout_seconds == 180
    assert settings.amo_research_max_sources == 9
    assert settings.amo_research_max_context_chars == 15000
    assert settings.amo_research_deep_breadth == 4
    assert settings.amo_research_deep_depth == 3
    assert settings.amo_research_deep_concurrency == 2
    assert settings.amo_research_vector_collection == "custom_research_chunks"
    assert settings.amo_research_report_words == 1200


def test_gpt_researcher_default_timeout_allows_multi_minute_answers(monkeypatch, tmp_path) -> None:
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
    monkeypatch.delenv("AMO_RESEARCH_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("WEBUI_LOGIN_DELAY_BASE_SECONDS", raising=False)
    monkeypatch.delenv("WEBUI_LOGIN_DELAY_MAX_SECONDS", raising=False)

    settings = get_settings()

    assert settings.amo_research_timeout_seconds == 300


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
