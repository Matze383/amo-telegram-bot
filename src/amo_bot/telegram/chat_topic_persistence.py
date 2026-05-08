from __future__ import annotations

from sqlalchemy.orm import sessionmaker

from amo_bot.db.repositories import ChatTopicRepository
from amo_bot.telegram.update_parser import TelegramMessage


class ChatTopicPersistenceService:
    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

    async def persist_message(self, message: TelegramMessage) -> None:
        with self._session_factory() as session:
            repo = ChatTopicRepository(session)
            repo.upsert_chat(
                chat_id=message.chat.id,
                chat_type=message.chat.type,
                title=message.chat.title,
                username=message.chat.username,
            )
            if message.message_thread_id is not None:
                repo.upsert_topic(
                    chat_id=message.chat.id,
                    message_thread_id=message.message_thread_id,
                    telegram_topic_name=None,
                )
