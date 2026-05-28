from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any
from urllib import error, request


class MistralProviderError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MistralProviderConfig:
    api_key: str
    model: str
    timeout_seconds: float
    base_url: str

    def redacted_dict(self) -> dict[str, object]:
        return {
            "provider": "mistral",
            "model": self.model,
            "timeout_seconds": self.timeout_seconds,
            "base_url": self.base_url,
            "api_key_present": bool(self.api_key),
            "api_key_preview": "***",
        }


@dataclass(frozen=True, slots=True)
class MistralRequestClient:
    config: MistralProviderConfig

    async def ask(self, prompt: str) -> str:
        if not self.config.api_key:
            raise MistralProviderError("mistral auth error: api key missing")

        payload = {
            "model": self.config.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }

        try:
            return await asyncio.to_thread(self._ask_sync, payload)
        except asyncio.TimeoutError as exc:
            raise MistralProviderError("mistral request timeout") from exc

    def _ask_sync(self, payload: dict[str, Any]) -> str:
        endpoint = f"{self.config.base_url.rstrip('/')}/chat/completions"
        data = json.dumps(payload).encode("utf-8")
        req = request.Request(
            endpoint,
            data=data,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
        )

        try:
            with request.urlopen(req, timeout=self.config.timeout_seconds) as resp:
                status = getattr(resp, "status", 200)
                body = resp.read()
        except error.HTTPError as exc:
            if exc.code in {401, 403}:
                raise MistralProviderError("mistral auth error") from exc
            if exc.code == 429:
                raise MistralProviderError("mistral rate limit") from exc
            raise MistralProviderError(f"mistral http error: status={exc.code}") from exc
        except error.URLError as exc:
            raise MistralProviderError("mistral transport error") from exc
        except TimeoutError as exc:
            raise MistralProviderError("mistral request timeout") from exc

        if not (200 <= int(status) < 300):
            if int(status) in {401, 403}:
                raise MistralProviderError("mistral auth error")
            if int(status) == 429:
                raise MistralProviderError("mistral rate limit")
            raise MistralProviderError(f"mistral http error: status={status}")

        try:
            decoded = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MistralProviderError("mistral invalid json response") from exc

        content = self._extract_content(decoded)
        if not content:
            raise MistralProviderError("mistral malformed response: empty content")

        return content

    @staticmethod
    def _extract_content(decoded: dict[str, Any]) -> str | None:
        choices = decoded.get("choices")
        if not isinstance(choices, list) or not choices:
            return None

        first = choices[0]
        if not isinstance(first, dict):
            return None

        message = first.get("message")
        if not isinstance(message, dict):
            return None

        content = message.get("content")
        if not isinstance(content, str):
            return None

        stripped = content.strip()
        return stripped or None
