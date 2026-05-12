from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from amo_bot.db.models import User
from amo_bot.telegram.update_parser import TelegramMessage

logger = logging.getLogger(__name__)

SendOwnerTextFn = Callable[[int, str], Awaitable[object]]


class OwnerNotifier:
    def __init__(self, *, owner_telegram_user_id: int | None, send_private_text: SendOwnerTextFn | None = None) -> None:
        self._owner_telegram_user_id = owner_telegram_user_id
        self._send_private_text = send_private_text

    async def notify_new_user_discovered(self, *, user: User, message: TelegramMessage) -> None:
        if not self._is_enabled() or user.telegram_user_id <= 0:
            return

        chat = message.chat
        topic_suffix = ""
        if message.message_thread_id is not None:
            topic_suffix = f" | topic_id={message.message_thread_id}"
            if message.telegram_topic_name:
                topic_suffix += f" topic={message.telegram_topic_name}"

        username = f"@{user.username}" if user.username else "-"
        full_name = f"{(user.first_name or '-')} {(user.last_name or '-')}".strip()
        text = (
            "🆕 Neuer User erfasst\n"
            f"id: {user.telegram_user_id}\n"
            f"username: {username}\n"
            f"name: {full_name}\n"
            f"consent: {user.consent_status or 'unknown'}\n"
            f"kontext: chat_type={chat.type} chat_title={(chat.title or '-')}{topic_suffix}"
        )
        await self._safe_send(text)

    async def notify_consent_decision(self, *, user: User, accepted: bool, source: str) -> None:
        if not self._is_enabled() or user.telegram_user_id <= 0:
            return
        action = "akzeptiert" if accepted else "abgelehnt"
        username = f"@{user.username}" if user.username else "-"
        full_name = f"{(user.first_name or '-')} {(user.last_name or '-')}".strip()
        text = (
            f"📋 Consent {action}\n"
            f"id: {user.telegram_user_id}\n"
            f"username: {username}\n"
            f"name: {full_name}\n"
            f"status: {user.consent_status or 'unknown'}\n"
            f"quelle: {source}"
        )
        await self._safe_send(text)

    async def notify_consent_prompt_sent(self, *, user: User, message: TelegramMessage) -> None:
        if not self._is_enabled() or user.telegram_user_id <= 0:
            return

        chat = message.chat
        topic_suffix = ""
        if message.message_thread_id is not None:
            topic_suffix = f" | topic_id={message.message_thread_id}"
            if message.telegram_topic_name:
                topic_suffix += f" topic={message.telegram_topic_name}"

        username = f"@{user.username}" if user.username else "-"
        full_name = f"{(user.first_name or '-')} {(user.last_name or '-')}".strip()
        text = (
            "📨 Policy-DM erfolgreich gesendet\n"
            f"id: {user.telegram_user_id}\n"
            f"username: {username}\n"
            f"name: {full_name}\n"
            f"status: {user.consent_status or 'unknown'}\n"
            f"kontext: chat_type={chat.type} chat_title={(chat.title or '-')}{topic_suffix}"
        )
        await self._safe_send(text)

    async def notify_consent_unreachable(self, *, user: User, reason: str | None = None) -> None:
        if not self._is_enabled() or user.telegram_user_id <= 0:
            return
        username = f"@{user.username}" if user.username else "-"
        full_name = f"{(user.first_name or '-')} {(user.last_name or '-')}".strip()
        text = (
            "⚠️ Policy-DM nicht zustellbar\n"
            f"id: {user.telegram_user_id}\n"
            f"username: {username}\n"
            f"name: {full_name}\n"
            f"status: {user.consent_status or 'unknown'}\n"
            "aktion: User muss den Bot privat mit /start öffnen"
        )
        if reason:
            text += f"\nreason: {reason}"
        await self._safe_send(text)

    async def notify_consent_group_fallback_sent(self, *, user: User, message: TelegramMessage) -> None:
        if not self._is_enabled() or user.telegram_user_id <= 0:
            return

        chat = message.chat
        topic_suffix = ""
        if message.message_thread_id is not None:
            topic_suffix = f" | topic_id={message.message_thread_id}"
            if message.telegram_topic_name:
                topic_suffix += f" topic={message.telegram_topic_name}"

        username = f"@{user.username}" if user.username else "-"
        full_name = f"{(user.first_name or '-')} {(user.last_name or '-')}".strip()
        text = (
            "📣 Gruppenfallback für Consent gesendet\n"
            f"id: {user.telegram_user_id}\n"
            f"username: {username}\n"
            f"name: {full_name}\n"
            f"status: {user.consent_status or 'unknown'}\n"
            f"kontext: chat_type={chat.type} chat_title={(chat.title or '-')} chat_id={chat.id}{topic_suffix}"
        )
        await self._safe_send(text)

    def _is_enabled(self) -> bool:
        return self._owner_telegram_user_id is not None and self._send_private_text is not None

    async def _safe_send(self, text: str) -> None:
        if not self._is_enabled() or self._owner_telegram_user_id is None or self._send_private_text is None:
            return
        try:
            await self._send_private_text(self._owner_telegram_user_id, text)
        except Exception:
            logger.exception("Owner notification failed")
