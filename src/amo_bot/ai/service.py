from __future__ import annotations

from dataclasses import dataclass
import asyncio

import httpx

from amo_bot.ai.ollama import OllamaClient, OllamaError, OllamaHTTPStatusError


@dataclass(slots=True)
class AIService:
    client: OllamaClient
    retry_on_transient_error: bool = True
    retry_delay_seconds: float = 1.0
    fallback_model: str | None = None

    async def ask(self, prompt: str) -> str:
        cleaned = prompt.strip()
        if not cleaned:
            raise ValueError("empty prompt")

        try:
            return await self.client.generate(cleaned)
        except OllamaError as exc:
            if not (self.retry_on_transient_error and self._is_transient_error(exc)):
                raise

        if self.retry_delay_seconds > 0:
            await asyncio.sleep(self.retry_delay_seconds)

        try:
            return await self.client.generate(cleaned)
        except OllamaError as retry_exc:
            if not self.fallback_model:
                raise retry_exc
            if not self._is_transient_error(retry_exc):
                raise retry_exc

            fallback_model = self.fallback_model.strip()
            if not fallback_model:
                raise retry_exc

        fallback_client = OllamaClient(
            base_url=self.client.base_url,
            model=fallback_model,
            timeout_seconds=self.client.timeout_seconds,
            max_response_chars=self.client.max_response_chars,
        )
        return await fallback_client.generate(cleaned)

    @staticmethod
    def _is_transient_error(exc: OllamaError) -> bool:
        if isinstance(exc, OllamaHTTPStatusError):
            return exc.status_code in {429, 500, 502, 503, 504}

        msg = str(exc).casefold()
        if "timed out" in msg or "timeout" in msg:
            return True
        if "transport" in msg or "network" in msg or "connection" in msg:
            return True
        if isinstance(exc.__cause__, (httpx.TimeoutException, httpx.TransportError)):
            return True
        return False


__all__ = ["AIService", "OllamaError"]
