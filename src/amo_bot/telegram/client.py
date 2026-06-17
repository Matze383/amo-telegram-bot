from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import logging
import mimetypes

import httpx

from amo_bot.telegram.outbound_text import split_telegram_message_text

_COMPONENT = "telegram.api"


class TelegramApiError(RuntimeError):
    def __init__(self, message: str, *, code: int | None = None, error_data: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.error_data = error_data or {}


class TelegramRateLimitError(TelegramApiError):
    def __init__(self, retry_after: int) -> None:
        super().__init__(f"Telegram rate limit hit, retry after {retry_after}s", code=429)
        self.retry_after = retry_after


logger = logging.getLogger(__name__)


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

        extra: dict[str, Any] = {"method": method}
        req_id = getattr(self, "_request_id", None)
        if req_id:
            extra["request_id"] = req_id

        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, json=payload)

        if response.status_code == 429:
            retry_after = 1
            try:
                retry_after = int(response.json().get("parameters", {}).get("retry_after", 1))
            except Exception:
                pass
            logger.warning(
                "telegram rate_limit component=%s method=%s retry_after=%s",
                _COMPONENT, method, retry_after,
            )
            raise TelegramRateLimitError(retry_after=retry_after)

        if response.status_code >= 400:
            error_msg = response.text[:300]
            logger.warning(
                "telegram http_error component=%s method=%s status=%s message=%s",
                _COMPONENT, method, response.status_code, error_msg,
            )
            raise TelegramApiError(
                f"HTTP {response.status_code}: {error_msg}",
                code=response.status_code,
            )

        data = response.json()
        if not data.get("ok", False):
            desc = str(data.get("description", "Unknown Telegram error"))
            logger.warning(
                "telegram api_error component=%s method=%s description=%s",
                _COMPONENT, method, desc,
            )
            raise TelegramApiError(desc, error_data=data)

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
        parse_mode: str | None = None,
    ) -> dict[str, Any]:
        chunks = split_telegram_message_text(text, parse_mode=parse_mode)
        first_result: dict[str, Any] | None = None
        for index, chunk in enumerate(chunks):
            payload: dict[str, Any] = {"chat_id": chat_id, "text": chunk}
            if parse_mode is not None:
                payload["parse_mode"] = parse_mode
            if message_thread_id is not None:
                payload["message_thread_id"] = message_thread_id
            if index == 0:
                if reply_to_message_id is not None:
                    payload["reply_to_message_id"] = reply_to_message_id
                if reply_markup is not None:
                    payload["reply_markup"] = reply_markup
            result = await self._call("sendMessage", payload)
            if first_result is None:
                first_result = result if isinstance(result, dict) else {}
        return first_result or {}

    async def send_photo(
        self,
        *,
        chat_id: int,
        photo_path: str,
        caption: str = "",
        message_thread_id: int | None = None,
    ) -> dict[str, Any]:
        file_path = Path(photo_path)
        with file_path.open("rb") as fh:
            files = {"photo": (file_path.name, fh, "application/octet-stream")}
            payload: dict[str, Any] = {"chat_id": chat_id, "caption": caption[:1024]}
            if message_thread_id is not None:
                payload["message_thread_id"] = message_thread_id
            return await self._call_multipart("sendPhoto", payload=payload, files=files)

    async def send_document(
        self,
        *,
        chat_id: int,
        document_path: str,
        caption: str = "",
        message_thread_id: int | None = None,
        mime_type: str | None = None,
    ) -> dict[str, Any]:
        file_path = Path(document_path)
        inferred = mime_type or mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        with file_path.open("rb") as fh:
            files = {"document": (file_path.name, fh, inferred)}
            payload: dict[str, Any] = {"chat_id": chat_id, "caption": caption[:1024]}
            if message_thread_id is not None:
                payload["message_thread_id"] = message_thread_id
            return await self._call_multipart("sendDocument", payload=payload, files=files)

    async def _call_multipart(self, method: str, *, payload: dict[str, Any], files: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.api_root}/{method}"
        timeout = self._timeout_for_method(method=method, payload=payload)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, data=payload, files=files)

        if response.status_code == 429:
            retry_after = 1
            try:
                retry_after = int(response.json().get("parameters", {}).get("retry_after", 1))
            except Exception:
                pass
            logger.warning(
                "telegram rate_limit component=%s method=%s retry_after=%s",
                _COMPONENT, method, retry_after,
            )
            raise TelegramRateLimitError(retry_after=retry_after)

        if response.status_code >= 400:
            error_msg = response.text[:300]
            logger.warning(
                "telegram http_error component=%s method=%s status=%s message=%s",
                _COMPONENT, method, response.status_code, error_msg,
            )
            raise TelegramApiError(
                f"HTTP {response.status_code}: {error_msg}",
                code=response.status_code,
            )

        data = response.json()
        if not data.get("ok", False):
            desc = str(data.get("description", "Unknown Telegram error"))
            logger.warning(
                "telegram api_error component=%s method=%s description=%s",
                _COMPONENT, method, desc,
            )
            raise TelegramApiError(desc, error_data=data)
        result = data.get("result")
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
