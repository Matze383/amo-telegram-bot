from __future__ import annotations

from dataclasses import dataclass
import logging

import httpx

from amo_bot.ai.response_contract import (
    AIResponseContractError,
    envelope_from_full_response_text,
    envelope_from_provider_chat_response,
)


class OllamaError(RuntimeError):
    pass


class OllamaHTTPStatusError(OllamaError):
    def __init__(self, status_code: int, message: str | None = None) -> None:
        super().__init__(message or f"http {status_code}")
        self.status_code = status_code


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class OllamaClient:
    base_url: str
    model: str
    timeout_seconds: float
    max_prompt_chars: int = 4000
    max_predict_tokens: int = 512
    max_response_chars: int = 1500
    request_endpoint: str = "generate"

    def __post_init__(self) -> None:
        if self.max_prompt_chars <= 0:
            raise ValueError("max_prompt_chars must be > 0")
        if self.max_predict_tokens <= 0:
            raise ValueError("max_predict_tokens must be > 0")
        if self.request_endpoint not in {"generate", "chat"}:
            raise ValueError("request_endpoint must be one of: generate, chat")

    async def generate(self, prompt: str) -> str:
        request_prompt = prompt[: self.max_prompt_chars]
        if self.request_endpoint == "chat":
            payload = {
                "model": self.model,
                "messages": [{"role": "user", "content": request_prompt}],
                "stream": False,
                "options": {"num_predict": self.max_predict_tokens},
            }
            endpoint_path = "/api/chat"
        else:
            payload = {
                "model": self.model,
                "prompt": request_prompt,
                "stream": False,
                "options": {"num_predict": self.max_predict_tokens},
            }
            endpoint_path = "/api/generate"
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(f"{self.base_url}{endpoint_path}", json=payload)
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException as exc:
            raise OllamaError("request timed out") from exc
        except httpx.HTTPStatusError as exc:
            endpoint = str(exc.request.url) if exc.request is not None else f"{self.base_url}{endpoint_path}"
            response_preview = (exc.response.text or "")[:300]
            logger.error(
                "ollama http error endpoint=%s status_code=%s prompt_len=%s response_preview=%r",
                endpoint,
                exc.response.status_code,
                len(prompt),
                response_preview,
            )
            raise OllamaHTTPStatusError(exc.response.status_code) from exc
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise OllamaError("invalid ollama response") from exc

        if not isinstance(data, dict):
            raise OllamaError("invalid ollama response")

        try:
            if self.request_endpoint == "chat":
                envelope = envelope_from_provider_chat_response(data)
            else:
                response_text = data.get("response")
                if not isinstance(response_text, str):
                    raise OllamaError("invalid ollama response")
                envelope = envelope_from_full_response_text(response_text)
        except AIResponseContractError as exc:
            raise OllamaError(str(exc)) from exc

        text = envelope.final_text
        return text[: self.max_response_chars]
