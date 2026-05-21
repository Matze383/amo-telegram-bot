from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
import logging

import httpx


class TelegramApiError(RuntimeError):
    pass


logger = logging.getLogger(__name__)


class TelegramRateLimitError(TelegramApiError):
    def __init__(self, retry_after: int) -> None:
        super().__init__(f"Telegram rate limit hit, retry after {retry_after}s")
        self.retry_after = retry_after


@dataclass(slots=True)
class TelegramClient:
    token: str
    base_url: str = "https://api.telegram.org"
    timeout_seconds: float = 30.0
    poll_read_timeout_margin_seconds: float = 10.0

    @property
    def api_root(self) -> str:
        return f"{self.base_url}/bot{self.token}"

    async def _call(self, method: str, payload: dict[str, Any]) -> Any:
        url = f"{self.api_root}/{method}"
        timeout = self._timeout_for_method(method=method, payload=payload)
        async with httpx.AsyncClient(timeout=timeout) as client:
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

    def _timeout_for_method(self, *, method: str, payload: dict[str, Any]) -> httpx.Timeout | float:
        if method != "getUpdates":
            return self.timeout_seconds

        poll_timeout_raw = payload.get("timeout")
        try:
            poll_timeout = float(poll_timeout_raw)
        except (TypeError, ValueError):
            poll_timeout = 0.0

        read_timeout = max(self.timeout_seconds, poll_timeout + self.poll_read_timeout_margin_seconds)
        connect_write_pool = max(5.0, self.timeout_seconds)
        logger.debug(
            "telegram getUpdates timeout config: shared_timeout=%s poll_timeout=%s read_timeout=%s",
            self.timeout_seconds,
            poll_timeout,
            read_timeout,
        )
        return httpx.Timeout(connect=connect_write_pool, write=connect_write_pool, pool=connect_write_pool, read=read_timeout)

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
