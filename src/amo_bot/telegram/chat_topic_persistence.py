from __future__ import annotations

from sqlalchemy.orm import sessionmaker

from amo_bot.consent.prompt_service import ConsentPromptService
from amo_bot.db.models import GROUP_CHAT_TYPES
from amo_bot.db.repositories import ChatSeenUserRepository, ChatTopicRepository, UserRoleRepository
from amo_bot.telegram.owner_notify import OwnerNotifier
from amo_bot.telegram.update_parser import TelegramMessage


class ChatTopicPersistenceService:
    def __init__(self, session_factory: sessionmaker, send_private_message=None, owner_notifier: OwnerNotifier | None = None) -> None:
        self._session_factory = session_factory
        self._send_private_message = send_private_message
        self._consent_prompt_service = ConsentPromptService()
        self._owner_notifier = owner_notifier

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
                if prompt_result == "unreachable" and self._owner_notifier is not None:
                    await self._owner_notifier.notify_consent_unreachable(user=user, reason="private_dm_unreachable")

            session.commit()
