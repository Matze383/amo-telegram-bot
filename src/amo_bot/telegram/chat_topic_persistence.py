from __future__ import annotations

import logging

from sqlalchemy.orm import sessionmaker

from amo_bot.consent.prompt_service import ConsentPromptService
from amo_bot.db.models import GROUP_CHAT_TYPES
from amo_bot.db.repositories import ChatSeenUserRepository, ChatTopicRepository, TopicAgentMemoryRepository, UserRoleRepository
from amo_bot.telegram.owner_notify import OwnerNotifier
from amo_bot.telegram.update_parser import TelegramMessage, TelegramUser


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

            text = (message.text or "").strip()
            if text and not text.startswith("/"):
                self._persist_recent_message(
                    session=session,
                    chat_type=message.chat.type,
                    chat_id=message.chat.id,
                    message_thread_id=message.message_thread_id,
                    private_user_id=message.from_user.id,
                    message_id=message.message_id,
                    author=message.from_user,
                    text=text,
                    source="bot" if message.from_user.is_bot else "user",
                )

            reply_to = message.reply_to_message
            reply_text = (message.reply_to_message_text or "").strip()
            if reply_to is not None and reply_text and not reply_text.startswith("/"):
                self._persist_recent_message(
                    session=session,
                    chat_type=message.chat.type,
                    chat_id=message.chat.id,
                    message_thread_id=message.message_thread_id,
                    private_user_id=message.from_user.id if message.chat.type == "private" else None,
                    message_id=reply_to.message_id,
                    author=reply_to.from_user,
                    text=reply_text,
                    source="bot" if (reply_to.from_user and reply_to.from_user.is_bot) else "user",
                    skip_existing=True,
                )

            if existing_user is None and self._owner_notifier is not None:
                await self._owner_notifier.notify_new_user_discovered(user=user, message=message)

            if self._send_private_message is not None:
                prompt_result = await self._consent_prompt_service.maybe_prompt_user(
                    user=user,
                    send_private_message=self._send_private_message,
                )
                if prompt_result == "prompted" and existing_user is None and self._owner_notifier is not None:
                    await self._owner_notifier.notify_consent_prompt_sent(user=user, message=message)
                if prompt_result == "unreachable":
                    if self._owner_notifier is not None:
                        await self._owner_notifier.notify_consent_unreachable(user=user, reason="private_dm_unreachable")
                    if (
                        existing_user is None
                        and (self._send_group_markup is not None or self._send_group_text is not None)
                        and message.chat.type in GROUP_CHAT_TYPES
                    ):
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
                            if self._owner_notifier is not None:
                                await self._owner_notifier.notify_consent_group_fallback_sent(user=user, message=message)
                        except Exception:
                            # Fallback message send failure must not break persistence/consent state updates.
                            logger.exception("group consent fallback send failed: chat_id=%s user_id=%s", message.chat.id, message.from_user.id)

            session.commit()

    def _persist_recent_message(
        self,
        *,
        session,
        chat_type: str,
        chat_id: int,
        message_thread_id: int | None,
        private_user_id: int | None,
        message_id: int,
        author: TelegramUser | None,
        text: str,
        source: str,
        skip_existing: bool = False,
    ) -> None:
        scope: tuple[str, int | None, int | None, int | None] | None = None
        if chat_type in GROUP_CHAT_TYPES:
            if message_thread_id is not None:
                scope = ("topic", chat_id, message_thread_id, None)
            else:
                scope = ("group_chat", chat_id, None, None)
        elif chat_type == "private" and private_user_id is not None:
            scope = ("private_user", None, None, private_user_id)

        if scope is None:
            return

        scope_type, scope_chat_id, topic_id, user_id = scope
        repo = TopicAgentMemoryRepository(session)
        if skip_existing and repo.get_recent_by_telegram_message_id(
            scope_type=scope_type,
            chat_id=scope_chat_id,
            topic_id=topic_id,
            user_id=user_id,
            telegram_message_id=message_id,
        ) is not None:
            return

        repo.add_message(
            scope_type=scope_type,
            chat_id=scope_chat_id,
            topic_id=topic_id,
            user_id=user_id,
            message_text=text,
            telegram_message_id=message_id,
            telegram_author_user_id=author.id if author is not None else None,
            telegram_author_username=author.username if author is not None else None,
            telegram_author_is_bot=bool(author and author.is_bot),
            source=source,
        )

    async def persist_bot_sent_message(
        self,
        *,
        chat_id: int,
        message_thread_id: int | None,
        message_id: int,
        text: str,
        bot_username: str | None = None,
    ) -> None:
        content = (text or "").strip()
        if not content or content.startswith("/"):
            return
        with self._session_factory() as session:
            self._persist_recent_message(
                session=session,
                chat_type="supergroup" if chat_id < 0 else "private",
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                private_user_id=chat_id if chat_id > 0 else None,
                message_id=message_id,
                author=TelegramUser(
                    id=0,
                    is_bot=True,
                    first_name="Bot",
                    username=(bot_username or self._bot_username),
                ),
                text=content,
                source="bot",
                skip_existing=True,
            )
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
