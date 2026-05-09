from __future__ import annotations

import asyncio
import logging

from amo_bot.ai.ollama import OllamaClient
from amo_bot.ai.service import AIService
from amo_bot.config.settings import get_settings
from amo_bot.core.logging import setup_logging
from amo_bot.db.base import create_session_factory
from amo_bot.db.init_db import init_db
from amo_bot.plugins.command_runtime import PluginCommandExecutor
from amo_bot.plugins.loader import PluginLoader
from amo_bot.telegram.client import TelegramClient
from amo_bot.telegram.commands import create_builtin_registry
from amo_bot.telegram.chat_topic_persistence import ChatTopicPersistenceService
from amo_bot.telegram.dispatcher import Dispatcher
from amo_bot.telegram.polling import OffsetStore, run_polling
from amo_bot.telegram.role_resolver import DBRoleResolver


logger = logging.getLogger(__name__)


def run() -> None:
    settings = get_settings()
    setup_logging()
    init_db(settings.database_url)

    tg = TelegramClient(token=settings.bot_token, base_url=settings.telegram_api_base)
    logger.info(
        "bot startup config: bot_username=%s ollama_url=%s ollama_model=%s",
        settings.bot_username,
        settings.ollama_base_url,
        settings.ollama_model,
    )
    offset_store = OffsetStore(settings.offset_state_file)

    session_factory = create_session_factory(settings.database_url)
    role_resolver = DBRoleResolver(session_factory)
    ai_service = AIService(
        OllamaClient(
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
            timeout_seconds=settings.ollama_timeout_seconds,
            max_response_chars=settings.ollama_max_response_chars,
        )
    )
    command_registry = create_builtin_registry(database_url=settings.database_url, ai_service=ai_service)

    async def send_text(chat_id: int, text: str) -> object:
        return await tg.send_message(chat_id=chat_id, text=text)

    async def reply_text(chat_id: int, message_id: int, text: str) -> object:
        return await tg.send_message(chat_id=chat_id, text=text, reply_to_message_id=message_id)

    plugin_command_executor = PluginCommandExecutor(
        loader=PluginLoader(settings.amo_plugin_dir),
        session_factory=session_factory,
        send_message=send_text,
        reply=reply_text,
    )

    dispatcher = Dispatcher(
        command_registry=command_registry,
        role_resolver=role_resolver,
        send_text=send_text,
        bot_username=settings.bot_username,
        message_persistence=ChatTopicPersistenceService(session_factory),
        plugin_command_executor=plugin_command_executor,
    )

    asyncio.run(
        run_polling(
            tg,
            offset_store,
            timeout_seconds=settings.poll_timeout_seconds,
            limit=settings.poll_limit,
            retry_max_seconds=settings.poll_retry_max_seconds,
            dispatcher=dispatcher,
        )
    )


if __name__ == "__main__":
    run()
