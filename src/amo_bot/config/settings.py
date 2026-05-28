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

    ai_provider: str = Field(default="ollama", alias="AI_PROVIDER")

    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o-mini", alias="OPENAI_MODEL")
    openai_timeout_seconds: float = Field(default=30.0, alias="OPENAI_TIMEOUT_SECONDS", gt=0)

    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    anthropic_model: str = Field(default="anthropic/claude-opus-4-6", alias="ANTHROPIC_MODEL")
    anthropic_timeout_seconds: float = Field(default=30.0, alias="ANTHROPIC_TIMEOUT_SECONDS", gt=0)
    anthropic_base_url: str = Field(default="https://api.anthropic.com", alias="ANTHROPIC_BASE_URL")

    gemini_api_key: str | None = Field(default=None, alias="GEMINI_API_KEY")
    google_api_key: str | None = Field(default=None, alias="GOOGLE_API_KEY")
    gemini_model: str = Field(default="google/gemini-3-flash-preview", alias="GEMINI_MODEL")
    gemini_timeout_seconds: float = Field(default=30.0, alias="GEMINI_TIMEOUT_SECONDS", gt=0)
    gemini_base_url: str = Field(default="https://generativelanguage.googleapis.com", alias="GEMINI_BASE_URL")

    openrouter_api_key: str | None = Field(default=None, alias="OPENROUTER_API_KEY")
    openrouter_model: str = Field(default="openrouter/auto", alias="OPENROUTER_MODEL")
    openrouter_timeout_seconds: float = Field(default=30.0, alias="OPENROUTER_TIMEOUT_SECONDS", gt=0)
    openrouter_base_url: str = Field(default="https://openrouter.ai/api/v1", alias="OPENROUTER_BASE_URL")

    groq_api_key: str | None = Field(default=None, alias="GROQ_API_KEY")
    groq_model: str = Field(default="groq/llama-3.1-8b-instant", alias="GROQ_MODEL")
    groq_timeout_seconds: float = Field(default=30.0, alias="GROQ_TIMEOUT_SECONDS", gt=0)
    groq_base_url: str = Field(default="https://api.groq.com/openai/v1", alias="GROQ_BASE_URL")

    mistral_api_key: str | None = Field(default=None, alias="MISTRAL_API_KEY")
    mistral_model: str = Field(default="mistral/mistral-large-latest", alias="MISTRAL_MODEL")
    mistral_timeout_seconds: float = Field(default=30.0, alias="MISTRAL_TIMEOUT_SECONDS", gt=0)
    mistral_base_url: str = Field(default="https://api.mistral.ai/v1", alias="MISTRAL_BASE_URL")

    xai_api_key: str | None = Field(default=None, alias="XAI_API_KEY")
    xai_model: str = Field(default="xai/grok-4.3", alias="XAI_MODEL")
    xai_timeout_seconds: float = Field(default=30.0, alias="XAI_TIMEOUT_SECONDS", gt=0)
    xai_base_url: str = Field(default="https://api.x.ai/v1", alias="XAI_BASE_URL")

    deepseek_api_key: str | None = Field(default=None, alias="DEEPSEEK_API_KEY")
    deepseek_model: str = Field(default="deepseek/deepseek-v4-flash", alias="DEEPSEEK_MODEL")
    deepseek_timeout_seconds: float = Field(default=30.0, alias="DEEPSEEK_TIMEOUT_SECONDS", gt=0)
    deepseek_base_url: str = Field(default="https://api.deepseek.com/v1", alias="DEEPSEEK_BASE_URL")

    together_api_key: str | None = Field(default=None, alias="TOGETHER_API_KEY")
    together_model: str = Field(default="together/moonshotai/Kimi-K2.5", alias="TOGETHER_MODEL")
    together_timeout_seconds: float = Field(default=30.0, alias="TOGETHER_TIMEOUT_SECONDS", gt=0)
    together_base_url: str = Field(default="https://api.together.xyz/v1", alias="TOGETHER_BASE_URL")

    fireworks_api_key: str | None = Field(default=None, alias="FIREWORKS_API_KEY")
    fireworks_model: str = Field(default="fireworks/accounts/fireworks/models/llama-v3p1-8b-instruct", alias="FIREWORKS_MODEL")
    fireworks_timeout_seconds: float = Field(default=30.0, alias="FIREWORKS_TIMEOUT_SECONDS", gt=0)
    fireworks_base_url: str = Field(default="https://api.fireworks.ai/inference/v1", alias="FIREWORKS_BASE_URL")

    litellm_api_key: str | None = Field(default=None, alias="LITELLM_API_KEY")
    litellm_model: str = Field(default="openai/gpt-4o-mini", alias="LITELLM_MODEL")
    litellm_timeout_seconds: float = Field(default=30.0, alias="LITELLM_TIMEOUT_SECONDS", gt=0)
    litellm_base_url: str = Field(default="https://api.litellm.ai", alias="LITELLM_BASE_URL")

    lmstudio_api_key: str | None = Field(default=None, alias="LMSTUDIO_API_KEY")
    lmstudio_model: str = Field(default="local-model", alias="LMSTUDIO_MODEL")
    lmstudio_timeout_seconds: float = Field(default=60.0, alias="LMSTUDIO_TIMEOUT_SECONDS", gt=0)
    lmstudio_base_url: str = Field(default="http://127.0.0.1:1234/v1", alias="LMSTUDIO_BASE_URL")

    vllm_api_key: str | None = Field(default=None, alias="VLLM_API_KEY")
    vllm_model: str = Field(default="", alias="VLLM_MODEL")
    vllm_timeout_seconds: float = Field(default=60.0, alias="VLLM_TIMEOUT_SECONDS", gt=0)
    vllm_base_url: str = Field(default="http://127.0.0.1:8000/v1", alias="VLLM_BASE_URL")

    bedrock_model: str = Field(default="amazon-bedrock/anthropic.claude-3-haiku-20240307-v1:0", alias="BEDROCK_MODEL")
    bedrock_region: str | None = Field(default=None, alias="BEDROCK_REGION")
    aws_region: str | None = Field(default=None, alias="AWS_REGION")
    aws_default_region: str | None = Field(default=None, alias="AWS_DEFAULT_REGION")
    bedrock_timeout_seconds: float = Field(default=30.0, alias="BEDROCK_TIMEOUT_SECONDS", gt=0)

    aws_access_key_id: str | None = Field(default=None, alias="AWS_ACCESS_KEY_ID")
    aws_secret_access_key: str | None = Field(default=None, alias="AWS_SECRET_ACCESS_KEY")
    aws_session_token: str | None = Field(default=None, alias="AWS_SESSION_TOKEN")

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
    ollama_streaming_mode: str = Field(default="off", alias="OLLAMA_STREAMING_MODE")

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
        if provider not in {"openai", "ollama", "anthropic", "google", "openrouter", "groq", "mistral", "xai", "deepseek", "together", "fireworks", "amazon-bedrock", "litellm", "lmstudio", "vllm"}:
            raise ValueError("AI_PROVIDER must be one of: openai, ollama, anthropic, google, openrouter, groq, mistral, xai, deepseek, together, fireworks, amazon-bedrock, litellm, lmstudio, vllm")

        self.ai_provider = provider

        if provider == "openai":
            api_key = (self.openai_api_key or "").strip()
            if not api_key:
                raise ValueError("OPENAI_API_KEY is required when AI_PROVIDER=openai")
            self.openai_api_key = api_key

        if provider == "anthropic":
            api_key = (self.anthropic_api_key or "").strip()
            if not api_key:
                raise ValueError("ANTHROPIC_API_KEY is required when AI_PROVIDER=anthropic")
            self.anthropic_api_key = api_key

            model = self.anthropic_model.strip()
            if not model:
                raise ValueError("ANTHROPIC_MODEL is required when AI_PROVIDER=anthropic")
            self.anthropic_model = model

            base_url = self.anthropic_base_url.strip()
            if not base_url:
                raise ValueError("ANTHROPIC_BASE_URL must not be empty")
            self.anthropic_base_url = base_url

        if provider == "google":
            gemini_api_key = (self.gemini_api_key or "").strip()
            google_api_key = (self.google_api_key or "").strip()
            api_key = gemini_api_key or google_api_key
            if not api_key:
                raise ValueError(
                    "When AI_PROVIDER=google, set GEMINI_API_KEY or GOOGLE_API_KEY"
                )
            self.gemini_api_key = api_key
            self.google_api_key = google_api_key or None

            model = self.gemini_model.strip()
            if not model:
                raise ValueError("GEMINI_MODEL is required when AI_PROVIDER=google")
            self.gemini_model = model

            base_url = self.gemini_base_url.strip()
            if not base_url:
                raise ValueError("GEMINI_BASE_URL must not be empty")
            self.gemini_base_url = base_url

        if provider == "openrouter":
            api_key = (self.openrouter_api_key or "").strip()
            if not api_key:
                raise ValueError("OPENROUTER_API_KEY is required when AI_PROVIDER=openrouter")
            self.openrouter_api_key = api_key

            model = self.openrouter_model.strip()
            if not model:
                raise ValueError("OPENROUTER_MODEL is required when AI_PROVIDER=openrouter")
            self.openrouter_model = model

            base_url = self.openrouter_base_url.strip()
            if not base_url:
                raise ValueError("OPENROUTER_BASE_URL must not be empty")
            self.openrouter_base_url = base_url

        if provider == "groq":
            api_key = (self.groq_api_key or "").strip()
            if not api_key:
                raise ValueError("GROQ_API_KEY is required when AI_PROVIDER=groq")
            self.groq_api_key = api_key

            model = self.groq_model.strip()
            if not model:
                raise ValueError("GROQ_MODEL is required when AI_PROVIDER=groq")
            self.groq_model = model

            base_url = self.groq_base_url.strip()
            if not base_url:
                raise ValueError("GROQ_BASE_URL must not be empty")
            self.groq_base_url = base_url

        if provider == "mistral":
            api_key = (self.mistral_api_key or "").strip()
            if not api_key:
                raise ValueError("MISTRAL_API_KEY is required when AI_PROVIDER=mistral")
            self.mistral_api_key = api_key

            model = self.mistral_model.strip()
            if not model:
                raise ValueError("MISTRAL_MODEL is required when AI_PROVIDER=mistral")
            self.mistral_model = model

            base_url = self.mistral_base_url.strip()
            if not base_url:
                raise ValueError("MISTRAL_BASE_URL must not be empty")
            self.mistral_base_url = base_url

        if provider == "xai":
            api_key = (self.xai_api_key or "").strip()
            if not api_key:
                raise ValueError("XAI_API_KEY is required when AI_PROVIDER=xai")
            self.xai_api_key = api_key

            model = self.xai_model.strip()
            if not model:
                raise ValueError("XAI_MODEL is required when AI_PROVIDER=xai")
            self.xai_model = model

            base_url = self.xai_base_url.strip()
            if not base_url:
                raise ValueError("XAI_BASE_URL must not be empty")
            self.xai_base_url = base_url

        if provider == "deepseek":
            api_key = (self.deepseek_api_key or "").strip()
            if not api_key:
                raise ValueError("DEEPSEEK_API_KEY is required when AI_PROVIDER=deepseek")
            self.deepseek_api_key = api_key

            model = self.deepseek_model.strip()
            if not model:
                raise ValueError("DEEPSEEK_MODEL is required when AI_PROVIDER=deepseek")
            self.deepseek_model = model

            base_url = self.deepseek_base_url.strip()
            if not base_url:
                raise ValueError("DEEPSEEK_BASE_URL must not be empty")
            self.deepseek_base_url = base_url

        if provider == "together":
            api_key = (self.together_api_key or "").strip()
            if not api_key:
                raise ValueError("TOGETHER_API_KEY is required when AI_PROVIDER=together")
            self.together_api_key = api_key

            model = self.together_model.strip()
            if not model:
                raise ValueError("TOGETHER_MODEL is required when AI_PROVIDER=together")
            self.together_model = model

            base_url = self.together_base_url.strip()
            if not base_url:
                raise ValueError("TOGETHER_BASE_URL must not be empty")
            self.together_base_url = base_url

        if provider == "fireworks":
            api_key = (self.fireworks_api_key or "").strip()
            if not api_key:
                raise ValueError("FIREWORKS_API_KEY is required when AI_PROVIDER=fireworks")
            self.fireworks_api_key = api_key

            model = self.fireworks_model.strip()
            if not model:
                raise ValueError("FIREWORKS_MODEL is required when AI_PROVIDER=fireworks")
            self.fireworks_model = model

            base_url = self.fireworks_base_url.strip()
            if not base_url:
                raise ValueError("FIREWORKS_BASE_URL must not be empty")
            self.fireworks_base_url = base_url

        if provider == "amazon-bedrock":
            model = self.bedrock_model.strip()
            if not model:
                raise ValueError("BEDROCK_MODEL is required when AI_PROVIDER=amazon-bedrock")
            self.bedrock_model = model

            region = (self.bedrock_region or "").strip() or (self.aws_region or "").strip() or (self.aws_default_region or "").strip()
            if not region:
                raise ValueError("BEDROCK_REGION (or AWS_REGION/AWS_DEFAULT_REGION) is required when AI_PROVIDER=amazon-bedrock")
            self.bedrock_region = region

            access_key = (self.aws_access_key_id or "").strip()
            secret_key = (self.aws_secret_access_key or "").strip()
            session_token = (self.aws_session_token or "").strip()

            has_any = bool(access_key or secret_key or session_token)
            has_all_pair = bool(access_key and secret_key)

            if has_any and not has_all_pair:
                raise ValueError("For AI_PROVIDER=amazon-bedrock set both AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY, or rely on ambient AWS credentials")

            self.aws_access_key_id = access_key or None
            self.aws_secret_access_key = secret_key or None
            self.aws_session_token = session_token or None

        if provider == "litellm":
            api_key = (self.litellm_api_key or "").strip()
            if not api_key:
                raise ValueError("LITELLM_API_KEY is required when AI_PROVIDER=litellm")
            self.litellm_api_key = api_key

            model = self.litellm_model.strip()
            if not model:
                raise ValueError("LITELLM_MODEL is required when AI_PROVIDER=litellm")
            self.litellm_model = model

            base_url = self.litellm_base_url.strip()
            if not base_url:
                raise ValueError("LITELLM_BASE_URL must not be empty")
            self.litellm_base_url = base_url

        if provider == "lmstudio":
            api_key = (self.lmstudio_api_key or "").strip()
            self.lmstudio_api_key = api_key or None

            model = self.lmstudio_model.strip()
            if not model:
                raise ValueError("LMSTUDIO_MODEL is required when AI_PROVIDER=lmstudio")
            self.lmstudio_model = model

            base_url = self.lmstudio_base_url.strip()
            if not base_url:
                raise ValueError("LMSTUDIO_BASE_URL must not be empty")
            self.lmstudio_base_url = base_url

        if provider == "vllm":
            api_key = (self.vllm_api_key or "").strip()
            self.vllm_api_key = api_key or None

            model = self.vllm_model.strip()
            if not model:
                raise ValueError("VLLM_MODEL is required when AI_PROVIDER=vllm")
            self.vllm_model = model

            base_url = self.vllm_base_url.strip()
            if not base_url:
                raise ValueError("VLLM_BASE_URL must not be empty")
            self.vllm_base_url = base_url

        endpoint = self.ollama_request_endpoint.strip().casefold()
        if endpoint not in {"generate", "chat"}:
            raise ValueError("OLLAMA_REQUEST_ENDPOINT must be one of: generate, chat")
        self.ollama_request_endpoint = endpoint

        streaming_mode = self.ollama_streaming_mode.strip().casefold()
        if streaming_mode not in {"off", "collect_only", "live_edit"}:
            raise ValueError("OLLAMA_STREAMING_MODE must be one of: off, collect_only, live_edit")
        self.ollama_streaming_mode = streaming_mode

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