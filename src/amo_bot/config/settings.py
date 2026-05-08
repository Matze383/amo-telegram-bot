from __future__ import annotations

import os

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, env_file_encoding="utf-8", extra="ignore")

    bot_token: str = Field(alias="BOT_TOKEN")
    telegram_api_base: str = Field(default="https://api.telegram.org", alias="TELEGRAM_API_BASE")
    bot_username: str | None = Field(default=None, alias="BOT_USERNAME")
    poll_timeout_seconds: int = Field(default=30, alias="POLL_TIMEOUT_SECONDS")
    poll_limit: int = Field(default=100, alias="POLL_LIMIT")
    poll_retry_max_seconds: int = Field(default=30, alias="POLL_RETRY_MAX_SECONDS")
    offset_state_file: str = Field(default=".state/offset.json", alias="OFFSET_STATE_FILE")

    ollama_base_url: str = Field(default="http://127.0.0.1:11434", alias="OLLAMA_URL")
    ollama_model: str = Field(default="llama3.1", alias="OLLAMA_MODEL")
    ollama_timeout_seconds: int = Field(default=20, alias="OLLAMA_TIMEOUT_SECONDS")
    ollama_max_response_chars: int = Field(default=1500, alias="OLLAMA_MAX_RESPONSE_CHARS")

    database_url: str = Field(default="sqlite:///./data/amo_bot.db", alias="DATABASE_URL")
    amo_plugin_dir: str = Field(default="./plugins", alias="AMO_PLUGIN_DIR")

    webui_host: str = Field(default="127.0.0.1", alias="WEBUI_HOST")
    webui_port: int = Field(default=8080, alias="WEBUI_PORT")
    webui_password: str = Field(alias="WEBUI_PASSWORD")
    webui_owner_telegram_id: int | None = Field(default=None, alias="WEBUI_OWNER_TELEGRAM_ID")
    webui_session_ttl_seconds: int = Field(default=3600, alias="WEBUI_SESSION_TTL_SECONDS")


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