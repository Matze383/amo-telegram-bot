from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any
from urllib import error, request


class AnthropicProviderError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class AnthropicProviderConfig:
    api_key: str
    model: str
    timeout_seconds: float
    base_url: str

    def redacted_dict(self) -> dict[str, object]:
        return {
            "provider": "anthropic",
            "model": self.model,
            "timeout_seconds": self.timeout_seconds,
            "base_url": self.base_url,
            "api_key_present": bool(self.api_key),
            "api_key_preview": "***",
        }


@dataclass(frozen=True, slots=True)
class AnthropicRequestClient:
    config: AnthropicProviderConfig

    async def ask(self, prompt: str) -> str:
        if not self.config.api_key:
            raise AnthropicProviderError("anthropic auth error: api key missing")

        payload = {
            "model": self.config.model,
            "max_tokens": 512,
            "messages": [{"role": "user", "content": prompt}],
        }

        try:
            return await asyncio.to_thread(self._ask_sync, payload)
        except (asyncio.TimeoutError, TimeoutError) as exc:
            raise AnthropicProviderError("anthropic request timeout") from exc

    def _ask_sync(self, payload: dict[str, Any]) -> str:
        endpoint = self.config.base_url.rstrip("/") + "/v1/messages"
        data = json.dumps(payload).encode("utf-8")
        req = request.Request(
            endpoint,
            data=data,
            method="POST",
            headers={
                "x-api-key": self.config.api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
        )

        try:
            with request.urlopen(req, timeout=self.config.timeout_seconds) as resp:
                status = getattr(resp, "status", 200)
                body = resp.read()
        except error.HTTPError as exc:
            if exc.code in {401, 403}:
                raise AnthropicProviderError("anthropic auth error") from exc
            if exc.code == 429:
                raise AnthropicProviderError("anthropic rate limit") from exc
            raise AnthropicProviderError(f"anthropic http error: status={exc.code}") from exc
        except error.URLError as exc:
            raise AnthropicProviderError("anthropic transport error") from exc
        except TimeoutError as exc:
            raise AnthropicProviderError("anthropic request timeout") from exc

        if not (200 <= int(status) < 300):
            if int(status) in {401, 403}:
                raise AnthropicProviderError("anthropic auth error")
            if int(status) == 429:
                raise AnthropicProviderError("anthropic rate limit")
            raise AnthropicProviderError(f"anthropic http error: status={status}")

        try:
            decoded = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AnthropicProviderError("anthropic invalid json response") from exc

        content = self._extract_content(decoded)
        if not content:
            raise AnthropicProviderError("anthropic malformed response: empty content")

        return content

    @staticmethod
    def _extract_content(decoded: dict[str, Any]) -> str | None:
        content = decoded.get("content")
        if not isinstance(content, list) or not content:
            return None

        text_parts: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") != "text":
                continue
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                text_parts.append(text.strip())

        if not text_parts:
            return None
        return "\n".join(text_parts)
