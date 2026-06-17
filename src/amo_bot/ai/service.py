from __future__ import annotations

from dataclasses import dataclass, field
import asyncio
import logging
import time
from typing import Any

_TERMINAL_OUTCOMES = {"done", "error", "cancel", "timeout"}

import httpx

from amo_bot.ai.model_policy import AIModelPolicyConfig, AIModelTaskType, route_model
from amo_bot.ai.ollama import OllamaClient, OllamaError, OllamaHTTPStatusError


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AIService:
    client: OllamaClient
    retry_on_transient_error: bool = True
    retry_delay_seconds: float = 1.0
    fallback_model: str | None = None
    model_policy: AIModelPolicyConfig = field(default_factory=AIModelPolicyConfig)
    last_stream_events: list[dict[str, Any]] = field(default_factory=list)

    async def ask(self, prompt: str, *, task_type: str | AIModelTaskType | None = None) -> str:
        return await self.ask_with_images(prompt, image_paths=(), task_type=task_type)

    async def ask_with_images(
        self,
        prompt: str,
        *,
        image_paths: tuple[str, ...] = (),
        task_type: str | AIModelTaskType | None = None,
    ) -> str:
        cleaned = prompt.strip()
        if not cleaned:
            raise ValueError("empty prompt")

        prompt_len = len(cleaned)
        self.last_stream_events = []
        route = route_model(
            prompt=cleaned,
            default_model=getattr(self.client, "model", "unknown"),
            default_timeout_seconds=float(getattr(self.client, "timeout_seconds", 30.0)),
            default_max_prompt_chars=int(getattr(self.client, "max_prompt_chars", 4000)),
            config=self.model_policy,
            task_type=task_type,
        )
        primary_client = self._client_for_route_model(
            model=route.model,
            think=route.think,
            timeout_seconds=route.timeout_seconds,
            max_prompt_chars=route.max_prompt_chars,
        )
        fallback_model = (route.fallback_model or self.fallback_model or "").strip()

        try:
            response = await self._timed_generate(
                client=primary_client,
                prompt=cleaned,
                phase="primary",
                prompt_len=prompt_len,
                image_paths=image_paths,
                task_type=route.task_type.value,
                route_decision=route.decision,
                route_reason=route.reason,
            )
            self.last_stream_events = self._normalize_stream_events(getattr(primary_client, "last_stream_events", []) or [])
            return response
        except OllamaError as exc:
            if not (self.retry_on_transient_error and self._is_transient_error(exc)):
                raise

        if self.retry_delay_seconds > 0:
            await asyncio.sleep(self.retry_delay_seconds)

        try:
            response = await self._timed_generate(
                client=primary_client,
                prompt=cleaned,
                phase="retry",
                prompt_len=prompt_len,
                image_paths=image_paths,
                task_type=route.task_type.value,
                route_decision=route.decision,
                route_reason=route.reason,
            )
            self.last_stream_events = self._normalize_stream_events(getattr(primary_client, "last_stream_events", []) or [])
            return response
        except OllamaError as retry_exc:
            if not fallback_model:
                raise retry_exc
            if not self._is_transient_error(retry_exc):
                raise retry_exc

        fallback_client = self._client_for_route_model(
            model=fallback_model,
            think=route.fallback_think,
            timeout_seconds=route.fallback_timeout_seconds,
            max_prompt_chars=route.fallback_max_prompt_chars,
        )
        response = await self._timed_generate(
            client=fallback_client,
            prompt=cleaned,
            phase="fallback",
            prompt_len=prompt_len,
            image_paths=image_paths,
            task_type=route.task_type.value,
            route_decision="fallback",
            route_reason="primary_transient_failure",
        )
        self.last_stream_events = self._normalize_stream_events(getattr(fallback_client, "last_stream_events", []) or [])
        return response

    def _client_for_route_model(
        self,
        *,
        model: str,
        think: bool,
        timeout_seconds: float,
        max_prompt_chars: int,
    ) -> OllamaClient:
        client_model = getattr(self.client, "model", model)
        if (
            model == client_model
            and bool(think) == bool(getattr(self.client, "think", False))
            and float(timeout_seconds) == float(getattr(self.client, "timeout_seconds", timeout_seconds))
            and int(max_prompt_chars) == int(getattr(self.client, "max_prompt_chars", max_prompt_chars))
        ):
            return self.client
        return OllamaClient(
            base_url=self.client.base_url,
            model=model,
            timeout_seconds=timeout_seconds,
            max_prompt_chars=max_prompt_chars,
            max_predict_tokens=getattr(self.client, "max_predict_tokens", 512),
            max_response_chars=getattr(self.client, "max_response_chars", 1500),
            request_endpoint=getattr(self.client, "request_endpoint", "generate"),
            streaming_mode=getattr(self.client, "streaming_mode", "off"),
            think=think,
        )

    async def _timed_generate(
        self,
        *,
        client: OllamaClient,
        prompt: str,
        phase: str,
        prompt_len: int,
        image_paths: tuple[str, ...] = (),
        task_type: str = "",
        route_decision: str = "",
        route_reason: str = "",
    ) -> str:
        model = getattr(client, "model", "unknown")
        started = time.perf_counter()
        try:
            if image_paths:
                response = await client.generate_with_images(prompt, image_paths=image_paths)
            else:
                response = await client.generate(prompt)
        except OllamaError as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            logger.info(
                "ai_request phase=%s model=%s task_type=%s route_decision=%s route_reason=%s think=%s prompt_len=%s duration_ms=%s outcome=error error_category=%s",
                phase,
                model,
                task_type,
                route_decision,
                route_reason,
                bool(getattr(client, "think", False)),
                prompt_len,
                duration_ms,
                self._error_category(exc),
            )
            raise

        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "ai_request phase=%s model=%s task_type=%s route_decision=%s route_reason=%s think=%s prompt_len=%s duration_ms=%s outcome=success",
            phase,
            model,
            task_type,
            route_decision,
            route_reason,
            bool(getattr(client, "think", False)),
            prompt_len,
            duration_ms,
        )
        return response

    @staticmethod
    def _normalize_stream_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        terminal_seen = False

        for event in events:
            if terminal_seen:
                continue

            normalized.append(event)
            event_type = str(event.get("type", "")).casefold()
            if event_type == "terminal":
                outcome = str(event.get("outcome", "")).casefold()
                if outcome in _TERMINAL_OUTCOMES:
                    terminal_seen = True

        return normalized

    @staticmethod
    def _error_category(exc: OllamaError) -> str:
        if isinstance(exc, OllamaHTTPStatusError):
            if exc.status_code in {429, 500, 502, 503, 504}:
                return "transient_http"
            return "http"

        msg = str(exc).casefold()
        if "timed out" in msg or "timeout" in msg:
            return "timeout"
        if "empty response" in msg:
            return "empty_response"
        if "invalid response" in msg:
            return "invalid_response"
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
