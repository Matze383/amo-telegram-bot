from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from amo_bot.telegram.client import TelegramRateLimitError


@dataclass(slots=True)
class FakeTelegramClient:
    incoming_updates: list[dict[str, Any]] = field(default_factory=list)
    sent_messages: list[dict[str, Any]] = field(default_factory=list)
    fail_send_count: int = 0
    flood_wait_seconds: int | None = None
    _next_message_id: int = 1000

    def add_text(
        self,
        *,
        update_id: int,
        chat_id: int,
        message_id: int,
        text: str,
        user_id: int = 42,
        username: str | None = "tester",
        topic_id: int | None = None,
        reply_to_message_id: int | None = None,
        reply_to_is_bot: bool = False,
        bot_username: str = "AMO_bot",
    ) -> None:
        message: dict[str, Any] = {
            "message_id": message_id,
            "from": {
                "id": user_id,
                "is_bot": False,
                "first_name": username or "Tester",
                "username": username,
            },
            "chat": {"id": chat_id, "type": "supergroup" if chat_id < 0 else "private", "title": "Fake Group"},
            "text": text,
        }
        if topic_id is not None:
            message["message_thread_id"] = topic_id
        if reply_to_message_id is not None:
            message["reply_to_message"] = {
                "message_id": reply_to_message_id,
                "from": {
                    "id": 7,
                    "is_bot": reply_to_is_bot,
                    "first_name": "AMO",
                    "username": bot_username if reply_to_is_bot else "other",
                },
                "chat": message["chat"],
                "text": "previous",
            }
        self.incoming_updates.append({"update_id": update_id, "message": message})

    def add_attachment_reference(
        self,
        *,
        update_id: int,
        chat_id: int,
        message_id: int,
        file_id: str,
        topic_id: int | None = None,
        caption: str = "",
    ) -> None:
        message: dict[str, Any] = {
            "message_id": message_id,
            "from": {"id": 42, "is_bot": False, "first_name": "Tester"},
            "chat": {"id": chat_id, "type": "supergroup" if chat_id < 0 else "private", "title": "Fake Group"},
            "caption": caption,
            "photo": [{"file_id": file_id, "file_unique_id": f"unique-{file_id}", "width": 800, "height": 600}],
        }
        if topic_id is not None:
            message["message_thread_id"] = topic_id
        self.incoming_updates.append({"update_id": update_id, "message": message})

    async def get_updates(self, *, offset: int, timeout: int, limit: int = 100, allowed_updates=None) -> list[dict[str, Any]]:  # noqa: ANN001
        del timeout, allowed_updates
        return [item for item in self.incoming_updates if int(item.get("update_id", 0)) >= offset][:limit]

    async def send_message(
        self,
        chat_id: int,
        text: str,
        reply_to_message_id: int | None = None,
        message_thread_id: int | None = None,
        reply_markup: dict[str, Any] | None = None,
        parse_mode: str | None = None,
    ) -> dict[str, Any]:
        if self.flood_wait_seconds is not None:
            seconds = self.flood_wait_seconds
            self.flood_wait_seconds = None
            raise TelegramRateLimitError(seconds)
        if self.fail_send_count > 0:
            self.fail_send_count -= 1
            raise RuntimeError("fake send failure")
        self._next_message_id += 1
        payload = {
            "message_id": self._next_message_id,
            "chat_id": chat_id,
            "text": text,
            "reply_to_message_id": reply_to_message_id,
            "message_thread_id": message_thread_id,
            "reply_markup": reply_markup,
            "parse_mode": parse_mode,
        }
        self.sent_messages.append(payload)
        return payload

    async def backoff_sleep(self, seconds: int) -> None:
        del seconds
