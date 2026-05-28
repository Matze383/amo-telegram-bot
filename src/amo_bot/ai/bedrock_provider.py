from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any
from urllib import error, request


class BedrockProviderError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class BedrockProviderConfig:
    model: str
    region: str
    timeout_seconds: float
    access_key_id: str | None = None
    secret_access_key: str | None = None
    session_token: str | None = None

    def redacted_dict(self) -> dict[str, object]:
        return {
            "provider": "amazon-bedrock",
            "model": self.model,
            "region": self.region,
            "timeout_seconds": self.timeout_seconds,
            "aws_access_key_id_present": bool(self.access_key_id),
            "aws_secret_access_key_present": bool(self.secret_access_key),
            "aws_session_token_present": bool(self.session_token),
        }


@dataclass(frozen=True, slots=True)
class BedrockRequestClient:
    config: BedrockProviderConfig

    async def ask(self, prompt: str) -> str:
        payload = {
            "schemaVersion": "messages-v1",
            "messages": [{"role": "user", "content": [{"text": prompt}]}],
            "inferenceConfig": {"maxTokens": 512},
        }

        try:
            return await asyncio.to_thread(self._ask_sync, payload)
        except asyncio.TimeoutError as exc:
            raise BedrockProviderError("amazon-bedrock request timeout") from exc

    def _ask_sync(self, payload: dict[str, Any]) -> str:
        endpoint = (
            f"https://bedrock-runtime.{self.config.region}.amazonaws.com"
            f"/model/{self.config.model}/converse"
        )
        data = json.dumps(payload).encode("utf-8")
        req = request.Request(
            endpoint,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
            },
        )

        try:
            with request.urlopen(req, timeout=self.config.timeout_seconds) as resp:
                status = getattr(resp, "status", 200)
                body = resp.read()
        except error.HTTPError as exc:
            if exc.code in {401, 403}:
                raise BedrockProviderError("amazon-bedrock auth error") from exc
            raise BedrockProviderError(f"amazon-bedrock http error: status={exc.code}") from exc
        except error.URLError as exc:
            raise BedrockProviderError("amazon-bedrock transport error") from exc
        except TimeoutError as exc:
            raise BedrockProviderError("amazon-bedrock request timeout") from exc

        if not (200 <= int(status) < 300):
            if int(status) in {401, 403}:
                raise BedrockProviderError("amazon-bedrock auth error")
            raise BedrockProviderError(f"amazon-bedrock http error: status={status}")

        try:
            decoded = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BedrockProviderError("amazon-bedrock invalid json response") from exc

        content = self._extract_content(decoded)
        if not content:
            raise BedrockProviderError("amazon-bedrock malformed response: empty content")

        return content

    @staticmethod
    def _extract_content(decoded: dict[str, Any]) -> str | None:
        output = decoded.get("output")
        if not isinstance(output, dict):
            return None

        message = output.get("message")
        if not isinstance(message, dict):
            return None

        content_items = message.get("content")
        if not isinstance(content_items, list) or not content_items:
            return None

        first_item = content_items[0]
        if not isinstance(first_item, dict):
            return None

        text = first_item.get("text")
        if not isinstance(text, str):
            return None

        stripped = text.strip()
        return stripped or None
