from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx


class TelegramApiError(RuntimeError):
    pass


class TelegramRateLimitError(TelegramApiError):
    def __init__(self, retry_after: int) -> None:
        super().__init__(f"Telegram rate limit hit, retry after {retry_after}s")
        self.retry_after = retry_after


@dataclass(slots=True)
class TelegramClient:
    token: str
    base_url: str = "https://api.telegram.org"
    timeout_seconds: float = 30.0

    @property
    def api_root(self) -> str:
        return f"{self.base_url}/bot{self.token}"

    async def _call(self, method: str, payload: dict[str, Any]) -> Any:
        url = f"{self.api_root}/{method}"
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(url, json=payload)

        if response.status_code == 429:
            retry_after = 1
            try:
                retry_after = int(response.json().get("parameters", {}).get("retry_after", 1))
            except Exception:
                pass
            raise TelegramRateLimitError(retry_after=retry_after)

        if response.status_code >= 400:
            raise TelegramApiError(f"HTTP {response.status_code}: {response.text[:300]}")

        data = response.json()
        if not data.get("ok", False):
            raise TelegramApiError(str(data.get("description", "Unknown Telegram error")))
        return data.get("result")

    async def get_updates(
        self,
        *,
        offset: int,
        timeout: int,
        limit: int = 100,
        allowed_updates: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"offset": offset, "timeout": timeout, "limit": limit}
        if allowed_updates:
            payload["allowed_updates"] = allowed_updates
        result = await self._call("getUpdates", payload)
        return result if isinstance(result, list) else []

    async def send_message(
        self,
        chat_id: int,
        text: str,
        reply_to_message_id: int | None = None,
        message_thread_id: int | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text[:4000]}
        if reply_to_message_id is not None:
            payload["reply_to_message_id"] = reply_to_message_id
        if message_thread_id is not None:
            payload["message_thread_id"] = message_thread_id
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        result = await self._call("sendMessage", payload)
        return result if isinstance(result, dict) else {}

    async def answer_callback_query(
        self,
        callback_query_id: str,
        text: str | None = None,
        show_alert: bool = False,
    ) -> bool:
        payload: dict[str, Any] = {"callback_query_id": callback_query_id, "show_alert": show_alert}
        if text is not None:
            payload["text"] = text[:200]
        result = await self._call("answerCallbackQuery", payload)
        return bool(result)

    async def get_me(self) -> dict[str, Any]:
        result = await self._call("getMe", {})
        return result if isinstance(result, dict) else {}

    async def backoff_sleep(self, seconds: int) -> None:
        await asyncio.sleep(max(1, seconds))
