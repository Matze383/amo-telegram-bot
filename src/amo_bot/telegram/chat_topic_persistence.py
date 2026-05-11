from __future__ import annotations

import logging

from sqlalchemy.orm import sessionmaker

from amo_bot.consent.prompt_service import ConsentPromptService
from amo_bot.db.models import GROUP_CHAT_TYPES
from amo_bot.db.repositories import ChatSeenUserRepository, ChatTopicRepository, UserRoleRepository
from amo_bot.telegram.owner_notify import OwnerNotifier
from amo_bot.telegram.update_parser import TelegramMessage


logger = logging.getLogger(__name__)


class ChatTopicPersistenceService:
    def __init__(
        self,
        session_factory: sessionmaker,
        send_private_message=None,
        owner_notifier: OwnerNotifier | None = None,
        send_group_markup=None,
        send_group_text=None,
        bot_username: str | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._send_private_message = send_private_message
        self._consent_prompt_service = ConsentPromptService()
        self._owner_notifier = owner_notifier
        self._send_group_markup = send_group_markup
        self._send_group_text = send_group_text
        self._bot_username = bot_username

    async def persist_message(self, message: TelegramMessage) -> None:
        with self._session_factory() as session:
            user_repo = UserRoleRepository(session)
            existing_user = user_repo.get_user_by_telegram_id(message.from_user.id)
            user = user_repo.upsert_discovered_user(
                telegram_user_id=message.from_user.id,
                username=message.from_user.username,
                first_name=message.from_user.first_name or None,
                last_name=message.from_user.last_name,
            )

            repo = ChatTopicRepository(session)
            repo.upsert_chat(
                chat_id=message.chat.id,
                chat_type=message.chat.type,
                title=message.chat.title,
                username=message.chat.username,
            )
            if message.chat.type in GROUP_CHAT_TYPES:
                ChatSeenUserRepository(session).mark_seen(
                    chat_id=message.chat.id,
                    telegram_user_id=message.from_user.id,
                )
            if message.message_thread_id is not None:
                repo.upsert_topic(
                    chat_id=message.chat.id,
                    message_thread_id=message.message_thread_id,
                    telegram_topic_name=message.telegram_topic_name,
                )

            if existing_user is None and self._owner_notifier is not None:
                await self._owner_notifier.notify_new_user_discovered(user=user, message=message)

            if self._send_private_message is not None:
                prompt_result = await self._consent_prompt_service.maybe_prompt_user(
                    user=user,
                    send_private_message=self._send_private_message,
                )
                if prompt_result == "unreachable":
                    if self._owner_notifier is not None:
                        await self._owner_notifier.notify_consent_unreachable(user=user, reason="private_dm_unreachable")
                    if (self._send_group_markup is not None or self._send_group_text is not None) and message.chat.type in GROUP_CHAT_TYPES:
                        try:
                            text = self._build_group_unreachable_text(message=message)
                            markup = self._build_group_unreachable_markup()
                            if markup is not None and self._send_group_markup is not None:
                                await self._send_group_markup(
                                    message.chat.id,
                                    text,
                                    markup,
                                    message.message_thread_id,
                                )
                            elif self._send_group_text is not None:
                                await self._send_group_text(
                                    message.chat.id,
                                    text,
                                    message.message_thread_id,
                                )
                        except Exception:
                            # Fallback message send failure must not break persistence/consent state updates.
                            logger.exception("group consent fallback send failed: chat_id=%s user_id=%s", message.chat.id, message.from_user.id)

            session.commit()

    def _build_group_unreachable_text(self, *, message: TelegramMessage) -> str:
        mention = self._render_user_mention(message=message)
        group_label = self._render_group_label(message=message)
        if mention:
            return (
                f"Willkommen {mention} in {group_label}. "
                "Ich bin der KI-Bot der Gruppe. "
                "Damit du mich nutzen kannst und ich mit dir interagieren kann, "
                "musst du den Nutzungsbedingungen zustimmen."
            )
        return (
            f"Willkommen in {group_label}. "
            "Ich bin der KI-Bot der Gruppe. "
            "Damit du mich nutzen kannst und ich mit dir interagieren kann, "
            "musst du den Nutzungsbedingungen zustimmen."
        )

    def _render_user_mention(self, *, message: TelegramMessage) -> str:
        if message.from_user.username:
            return f"@{message.from_user.username}"
        return ""

    def _render_group_label(self, *, message: TelegramMessage) -> str:
        if message.chat.title:
            return message.chat.title
        return "der Gruppe"

    def _build_group_unreachable_markup(self) -> dict[str, object] | None:
        bot_username = (self._bot_username or "").strip().lstrip("@")
        if not bot_username:
            return None
        return {
            "inline_keyboard": [
                [
                    {
                        "text": "Policy privat öffnen",
                        "url": f"https://t.me/{bot_username}?start=consent",
                    }
                ]
            ]
        }
