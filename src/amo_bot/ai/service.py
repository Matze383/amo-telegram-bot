from __future__ import annotations

from dataclasses import dataclass, field
import asyncio
import logging
import time
from typing import Any

import httpx

from amo_bot.ai.ollama import OllamaClient, OllamaError, OllamaHTTPStatusError


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AIService:
    client: OllamaClient
    retry_on_transient_error: bool = True
    retry_delay_seconds: float = 1.0
    fallback_model: str | None = None
    last_stream_events: list[dict[str, Any]] = field(default_factory=list)

    async def ask(self, prompt: str) -> str:
        cleaned = prompt.strip()
        if not cleaned:
            raise ValueError("empty prompt")

        prompt_len = len(cleaned)
        self.last_stream_events = []

        try:
            response = await self._timed_generate(client=self.client, prompt=cleaned, phase="primary", prompt_len=prompt_len)
            self.last_stream_events = list(getattr(self.client, "last_stream_events", []) or [])
            return response
        except OllamaError as exc:
            if not (self.retry_on_transient_error and self._is_transient_error(exc)):
                raise

        if self.retry_delay_seconds > 0:
            await asyncio.sleep(self.retry_delay_seconds)

        try:
            response = await self._timed_generate(client=self.client, prompt=cleaned, phase="retry", prompt_len=prompt_len)
            self.last_stream_events = list(getattr(self.client, "last_stream_events", []) or [])
            return response
        except OllamaError as retry_exc:
            if not self.fallback_model:
                raise retry_exc
            if not self._is_transient_error(retry_exc):
                raise retry_exc

            fallback_model = self.fallback_model.strip()
            if not fallback_model:
                raise retry_exc

        request_endpoint = getattr(self.client, "request_endpoint", "generate")
        fallback_client = OllamaClient(
            base_url=self.client.base_url,
            model=fallback_model,
            timeout_seconds=self.client.timeout_seconds,
            max_response_chars=self.client.max_response_chars,
            request_endpoint=request_endpoint,
        )
        response = await self._timed_generate(client=fallback_client, prompt=cleaned, phase="fallback", prompt_len=prompt_len)
        self.last_stream_events = list(getattr(fallback_client, "last_stream_events", []) or [])
        return response

    async def _timed_generate(self, *, client: OllamaClient, prompt: str, phase: str, prompt_len: int) -> str:
        model = getattr(client, "model", "unknown")
        started = time.perf_counter()
        try:
            response = await client.generate(prompt)
        except OllamaError as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            logger.info(
                "ai_request phase=%s model=%s prompt_len=%s duration_ms=%s outcome=error error_category=%s",
                phase,
                model,
                prompt_len,
                duration_ms,
                self._error_category(exc),
            )
            raise

        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "ai_request phase=%s model=%s prompt_len=%s duration_ms=%s outcome=success",
            phase,
            model,
            prompt_len,
            duration_ms,
        )
        return response

    @staticmethod
    def _error_category(exc: OllamaError) -> str:
        if isinstance(exc, OllamaHTTPStatusError):
            if exc.status_code in {429, 500, 502, 503, 504}:
                return "transient_http"
            return "http"

        msg = str(exc).casefold()
        if "timed out" in msg or "timeout" in msg:
            return "timeout"
        if "transport" in msg or "network" in msg or "connection" in msg:
            return "network"
        if isinstance(exc.__cause__, httpx.TimeoutException):
            return "timeout"
        if isinstance(exc.__cause__, httpx.TransportError):
            return "network"
        return "other"

    @staticmethod
    def _is_transient_error(exc: OllamaError) -> bool:
        if isinstance(exc, OllamaHTTPStatusError):
            return exc.status_code in {429, 500, 502, 503, 504}

        msg = str(exc).casefold()
        if "timed out" in msg or "timeout" in msg:
            return True
        if "empty response" in msg:
            return True
        if "transport" in msg or "network" in msg or "connection" in msg:
            return True
        if isinstance(exc.__cause__, (httpx.TimeoutException, httpx.TransportError)):
            return True
        return False


__all__ = ["AIService", "OllamaError"]
