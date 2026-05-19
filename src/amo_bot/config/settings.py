from __future__ import annotations

import os

from dotenv import load_dotenv
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, env_file_encoding="utf-8", extra="ignore", env_ignore_empty=True)

    bot_token: str = Field(alias="BOT_TOKEN")
    telegram_api_base: str = Field(default="https://api.telegram.org", alias="TELEGRAM_API_BASE")
    bot_username: str | None = Field(default=None, alias="BOT_USERNAME")
    poll_timeout_seconds: int = Field(default=30, alias="POLL_TIMEOUT_SECONDS")
    poll_limit: int = Field(default=100, alias="POLL_LIMIT")
    poll_retry_max_seconds: int = Field(default=30, alias="POLL_RETRY_MAX_SECONDS")
    offset_state_file: str = Field(default=".state/offset.json", alias="OFFSET_STATE_FILE")

    ai_provider: str = Field(default="openai", alias="AI_PROVIDER")

    ollama_base_url: str = Field(default="http://127.0.0.1:11434", alias="OLLAMA_URL")
    ollama_model: str = Field(default="llama3.1", alias="OLLAMA_MODEL")
    ollama_timeout_seconds: int = Field(default=20, alias="OLLAMA_TIMEOUT_SECONDS")
    ollama_max_prompt_chars: int = Field(default=4000, alias="OLLAMA_MAX_PROMPT_CHARS", gt=0)
    ollama_max_predict_tokens: int = Field(default=512, alias="OLLAMA_MAX_PREDICT_TOKENS", gt=0)
    ollama_max_response_chars: int = Field(default=1500, alias="OLLAMA_MAX_RESPONSE_CHARS")
    ollama_retry_on_transient_error: bool = Field(default=True, alias="OLLAMA_RETRY_ON_TRANSIENT_ERROR")
    ollama_retry_delay_seconds: float = Field(default=1.0, alias="OLLAMA_RETRY_DELAY_SECONDS", ge=0)
    ollama_fallback_model: str | None = Field(default=None, alias="OLLAMA_FALLBACK_MODEL")
    ollama_request_endpoint: str = Field(default="generate", alias="OLLAMA_REQUEST_ENDPOINT")

    database_url: str = Field(default="sqlite:///./data/amo_bot.db", alias="DATABASE_URL")
    amo_plugin_dir: str = Field(default="./plugins", alias="AMO_PLUGIN_DIR")
    plugin_command_sandbox_enabled: bool = Field(default=False, alias="PLUGIN_COMMAND_SANDBOX_ENABLED")

    webui_host: str = Field(default="127.0.0.1", alias="WEBUI_HOST")
    webui_port: int = Field(default=8080, alias="WEBUI_PORT")
    webui_password: str = Field(alias="WEBUI_PASSWORD")
    webui_secret_key: str = Field(alias="WEBUI_SECRET_KEY")
    webui_owner_telegram_id: int | None = Field(default=None, alias="WEBUI_OWNER_TELEGRAM_ID")
    webui_session_ttl_seconds: int = Field(default=3600, alias="WEBUI_SESSION_TTL_SECONDS")
    webui_public_mode: bool = Field(default=False, alias="WEBUI_PUBLIC_MODE")
    webui_require_https: bool = Field(default=False, alias="WEBUI_REQUIRE_HTTPS")
    webui_session_cookie_secure: bool | None = Field(default=None, alias="WEBUI_SESSION_COOKIE_SECURE")
    webui_login_delay_base_seconds: float = Field(default=0.25, alias="WEBUI_LOGIN_DELAY_BASE_SECONDS", ge=0)
    webui_login_delay_max_seconds: float = Field(default=2.0, alias="WEBUI_LOGIN_DELAY_MAX_SECONDS", ge=0)

    @model_validator(mode="after")
    def _validate_login_delay_bounds(self) -> Settings:
        if self.webui_login_delay_max_seconds < self.webui_login_delay_base_seconds:
            raise ValueError("WEBUI_LOGIN_DELAY_MAX_SECONDS must be >= WEBUI_LOGIN_DELAY_BASE_SECONDS")

        provider = self.ai_provider.strip().casefold()
        if provider not in {"openai", "ollama"}:
            raise ValueError("AI_PROVIDER must be one of: openai, ollama")

        self.ai_provider = provider

        endpoint = self.ollama_request_endpoint.strip().casefold()
        if endpoint not in {"generate", "chat"}:
            raise ValueError("OLLAMA_REQUEST_ENDPOINT must be one of: generate, chat")
        self.ollama_request_endpoint = endpoint

        return self


def get_settings() -> Settings:
    # Projekt-.env soll fuer lokale Starts Standard sein und alte Shell-Exports
    # gezielt uebersteuern, um stille Fehlkonfigurationen zu vermeiden.
    override_from_env_file = os.getenv("AMO_ENV_OVERRIDE", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
    dotenv_path = os.getenv("DOTENV_PATH", ".env")
    load_dotenv(dotenv_path=dotenv_path, override=override_from_env_file)
    return Settings()