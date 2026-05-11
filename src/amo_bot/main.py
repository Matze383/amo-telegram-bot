from __future__ import annotations

import asyncio
import logging
import threading
from argparse import ArgumentParser

from amo_bot.ai.ollama import OllamaClient
from amo_bot.ai.service import AIService
from amo_bot.config.settings import get_settings
from amo_bot.core.logging import setup_logging
from amo_bot.db.base import create_session_factory
from amo_bot.db.init_db import init_db
from amo_bot.plugins.command_runtime import PluginCommandExecutor
from amo_bot.db.repositories import UserRoleRepository
from amo_bot.plugins.loader import PluginLoader
from amo_bot.plugins.scheduled_runtime import ScheduledPluginExecutor
from amo_bot.telegram.client import TelegramClient
from amo_bot.telegram.commands import create_builtin_registry
from amo_bot.telegram.chat_topic_persistence import ChatTopicPersistenceService
from amo_bot.telegram.dispatcher import Dispatcher
from amo_bot.telegram.owner_notify import OwnerNotifier
from amo_bot.telegram.polling import OffsetStore, run_polling
from amo_bot.telegram.role_resolver import DBRoleResolver
from amo_bot.webui.flask_app import create_flask_app


logger = logging.getLogger(__name__)


def _build_parser() -> ArgumentParser:
    parser = ArgumentParser(prog="python -m amo_bot.main")
    parser.add_argument("--webui", action="store_true", help="Start Flask WebUI only")
    parser.add_argument("--serve", action="store_true", help="Start WebUI and Telegram polling together")
    return parser


def run(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args([] if argv is None else argv)

    settings = get_settings()
    setup_logging()
    init_db(settings.database_url)

    session_factory = create_session_factory(settings.database_url)
    with session_factory() as session:
        UserRoleRepository(session).bootstrap_owner_from_settings(
            owner_telegram_user_id=settings.webui_owner_telegram_id
        )

    tg = TelegramClient(token=settings.bot_token, base_url=settings.telegram_api_base)
    logger.info(
        "bot startup config: bot_username=%s ollama_url=%s ollama_model=%s",
        settings.bot_username,
        settings.ollama_base_url,
        settings.ollama_model,
    )
    offset_store = OffsetStore(settings.offset_state_file)

    role_resolver = DBRoleResolver(session_factory)
    ai_service = AIService(
        OllamaClient(
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
            timeout_seconds=settings.ollama_timeout_seconds,
            max_response_chars=settings.ollama_max_response_chars,
        )
    )
    async def send_owner_private_text(chat_id: int, text: str) -> object:
        return await tg.send_message(chat_id=chat_id, text=text)

    owner_notifier = OwnerNotifier(
        owner_telegram_user_id=settings.webui_owner_telegram_id,
        send_private_text=send_owner_private_text,
    )

    command_registry = create_builtin_registry(
        database_url=settings.database_url,
        ai_service=ai_service,
        owner_notifier=owner_notifier,
    )

    async def send_text(chat_id: int, text: str, message_thread_id: int | None = None) -> object:
        return await tg.send_message(chat_id=chat_id, text=text, message_thread_id=message_thread_id)

    async def send_markup(
        chat_id: int,
        text: str,
        reply_markup: dict[str, object],
        message_thread_id: int | None = None,
    ) -> object:
        return await tg.send_message(
            chat_id=chat_id,
            text=text,
            message_thread_id=message_thread_id,
            reply_markup=reply_markup,
        )

    async def send_private_text_with_markup(
        chat_id: int,
        text: str,
        reply_markup: dict[str, object],
    ) -> object:
        return await tg.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)

    async def answer_callback(callback_query_id: str, text: str | None = None) -> object:
        return await tg.answer_callback_query(callback_query_id=callback_query_id, text=text)

    async def reply_text(
        chat_id: int,
        message_id: int,
        text: str,
        message_thread_id: int | None = None,
    ) -> object:
        return await tg.send_message(
            chat_id=chat_id,
            text=text,
            reply_to_message_id=message_id,
            message_thread_id=message_thread_id,
        )

    plugin_loader = PluginLoader(settings.amo_plugin_dir)

    plugin_command_executor = PluginCommandExecutor(
        loader=plugin_loader,
        session_factory=session_factory,
        send_message=send_text,
        reply=reply_text,
    )

    scheduled_plugin_executor = ScheduledPluginExecutor(
        loader=plugin_loader,
        session_factory=session_factory,
        send_message=send_text,
        reply=reply_text,
    )

    dispatcher = Dispatcher(
        command_registry=command_registry,
        role_resolver=role_resolver,
        send_text=send_text,
        send_markup=send_markup,
        send_private_markup=send_markup,
        answer_callback=answer_callback,
        bot_username=settings.bot_username,
        message_persistence=ChatTopicPersistenceService(
            session_factory,
            send_private_message=send_private_text_with_markup,
            owner_notifier=owner_notifier,
        ),
        plugin_command_executor=plugin_command_executor,
        database_url=settings.database_url,
        owner_notifier=owner_notifier,
    )

    if args.webui:
        app = create_flask_app(settings=settings)
        app.run(host=settings.webui_host, port=settings.webui_port)
        return

    if args.serve:
        app = create_flask_app(settings=settings)

        webui_thread = threading.Thread(
            target=app.run,
            kwargs={
                "host": settings.webui_host,
                "port": settings.webui_port,
                "use_reloader": False,
            },
            daemon=True,
            name="flask-webui",
        )
        webui_thread.start()

        asyncio.run(
            run_polling(
                tg,
                offset_store,
                timeout_seconds=settings.poll_timeout_seconds,
                limit=settings.poll_limit,
                retry_max_seconds=settings.poll_retry_max_seconds,
                dispatcher=dispatcher,
                scheduled_tick=scheduled_plugin_executor.run_due_once,
            )
        )
        return

    asyncio.run(
        run_polling(
            tg,
            offset_store,
            timeout_seconds=settings.poll_timeout_seconds,
            limit=settings.poll_limit,
            retry_max_seconds=settings.poll_retry_max_seconds,
            dispatcher=dispatcher,
            scheduled_tick=scheduled_plugin_executor.run_due_once,
        )
    )


if __name__ == "__main__":
    run()
