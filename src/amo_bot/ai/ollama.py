from __future__ import annotations

from dataclasses import dataclass
import logging

import httpx


class OllamaError(RuntimeError):
    pass


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class OllamaClient:
    base_url: str
    model: str
    timeout_seconds: float
    max_response_chars: int

    async def generate(self, prompt: str) -> str:
        payload = {
            "model": self.model,
            "prompt": prompt[:4000],
            "stream": False,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(f"{self.base_url}/api/generate", json=payload)
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException as exc:
            raise OllamaError("request timed out") from exc
        except httpx.HTTPStatusError as exc:
            endpoint = str(exc.request.url) if exc.request is not None else f"{self.base_url}/api/generate"
            response_preview = (exc.response.text or "")[:300]
            logger.error(
                "ollama http error endpoint=%s status_code=%s prompt_len=%s response_preview=%r",
                endpoint,
                exc.response.status_code,
                len(prompt),
                response_preview,
            )
            raise OllamaError(f"http {exc.response.status_code}") from exc
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise OllamaError("invalid ollama response") from exc

        if not isinstance(data, dict):
            raise OllamaError("invalid ollama response")

        response_text = data.get("response")
        if not isinstance(response_text, str):
            raise OllamaError("invalid ollama response")

        text = response_text.strip()
        if not text:
            raise OllamaError("empty response")
        return text[: self.max_response_chars]
