from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any
from urllib import error, request


class GeminiProviderError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class GeminiProviderConfig:
    api_key: str
    model: str
    timeout_seconds: float
    base_url: str

    def redacted_dict(self) -> dict[str, object]:
        return {
            "provider": "gemini",
            "model": self.model,
            "timeout_seconds": self.timeout_seconds,
            "base_url": self.base_url,
            "api_key_present": bool(self.api_key),
            "api_key_preview": "***",
        }


@dataclass(frozen=True, slots=True)
class GeminiRequestClient:
    config: GeminiProviderConfig

    async def ask(self, prompt: str) -> str:
        if not self.config.api_key:
            raise GeminiProviderError("gemini auth error: api key missing")

        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": prompt}],
                }
            ]
        }

        try:
            return await asyncio.to_thread(self._ask_sync, payload)
        except (asyncio.TimeoutError, TimeoutError) as exc:
            raise GeminiProviderError("gemini request timeout") from exc

    def _ask_sync(self, payload: dict[str, Any]) -> str:
        api_model = self._api_model_name(self.config.model)
        endpoint = (
            self.config.base_url.rstrip("/")
            + f"/v1beta/models/{api_model}:generateContent"
        )
        data = json.dumps(payload).encode("utf-8")
        req = request.Request(
            endpoint,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": self.config.api_key,
            },
        )

        try:
            with request.urlopen(req, timeout=self.config.timeout_seconds) as resp:
                status = getattr(resp, "status", 200)
                body = resp.read()
        except error.HTTPError as exc:
            if exc.code in {401, 403}:
                raise GeminiProviderError("gemini auth error") from exc
            if exc.code == 429:
                raise GeminiProviderError("gemini rate limit") from exc
            raise GeminiProviderError(f"gemini http error: status={exc.code}") from exc
        except error.URLError as exc:
            raise GeminiProviderError("gemini transport error") from exc
        except TimeoutError as exc:
            raise GeminiProviderError("gemini request timeout") from exc

        if not (200 <= int(status) < 300):
            if int(status) in {401, 403}:
                raise GeminiProviderError("gemini auth error")
            if int(status) == 429:
                raise GeminiProviderError("gemini rate limit")
            raise GeminiProviderError(f"gemini http error: status={status}")

        try:
            decoded = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GeminiProviderError("gemini invalid json response") from exc

        content = self._extract_content(decoded)
        if not content:
            raise GeminiProviderError("gemini malformed response: empty content")

        return content

    @staticmethod
    def _api_model_name(model: str) -> str:
        candidate = model.strip()
        if candidate.casefold().startswith("google/"):
            candidate = candidate[len("google/") :]
        return candidate

    @staticmethod
    def _extract_content(decoded: dict[str, Any]) -> str | None:
        candidates = decoded.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            return None

        first = candidates[0]
        if not isinstance(first, dict):
            return None

        content = first.get("content")
        if not isinstance(content, dict):
            return None

        parts = content.get("parts")
        if not isinstance(parts, list) or not parts:
            return None

        text_parts: list[str] = []
        for part in parts:
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                text_parts.append(text.strip())

        if not text_parts:
            return None

        return "\n".join(text_parts)
