from __future__ import annotations

import asyncio
import logging
import os
import threading
from argparse import ArgumentParser

from dotenv import load_dotenv

from amo_bot.ai.providers import build_ai_provider
from amo_bot.config.settings import get_settings
from amo_bot.core.logging import setup_logging, log_event
from amo_bot.db.base import create_session_factory
from amo_bot.db.init_db import init_db
from amo_bot.db.repositories import (
    ResearchSourceObservationRepository,
    ResearchSourcePreferenceRepository,
    TopicAgentMemoryRepository,
    UserRoleRepository,
)
from amo_bot.plugins.command_runtime import PluginCommandExecutor
from amo_bot.plugins.loader import PluginLoader
from amo_bot.plugins.scheduled_runtime import ScheduledPluginExecutor
from amo_bot.process_control import pid_file, stop_running_bot
from amo_bot.telegram.client import TelegramClient
from amo_bot.telegram.commands import create_builtin_registry
from amo_bot.telegram.chat_topic_persistence import ChatTopicPersistenceService
from amo_bot.telegram.dispatcher import Dispatcher
from amo_bot.telegram.image_media_store import TelegramImageMediaStore
from amo_bot.telegram.owner_notify import OwnerNotifier
from amo_bot.telegram.polling import OffsetStore, run_polling
from amo_bot.telegram.role_resolver import DBRoleResolver
from amo_bot.webui.flask_app import create_flask_app
from amo_bot.ai.dreaming_runtime import DreamingRuntime
from amo_bot.ai.daily_memory_runtime import DailyMemoryRuntime
from amo_bot.ai.webtool_dispatcher import WebtoolCapabilityDispatcher
from amo_bot.ai.webtool_provider_adapter import RealBrowserProviderAdapter, RealWebscrapeProviderAdapter, RealWebsearchProviderAdapter
from amo_bot.ai.webtool_subagent import create_webtool_subagent_service
from amo_bot.current_info import (
    CurrentInfoService,
    build_cached_fetch_provider_from_settings,
    build_current_info_retrieval_provider_from_settings,
    build_current_info_safety_config_from_settings,
    build_current_info_vector_components_from_settings,
    build_document_fetcher_from_settings,
    build_gpt_researcher_provider_from_settings,
    build_search_broker_from_settings,
)
from amo_bot.db.repositories import WebToolRoleQuotaRepository
from amo_bot.telegram.webtool_evidence import (
    BinanceTickerEvidenceProvider,
    CoinGeckoEvidenceProvider,
    DbBackedProviderHealthRegistry,
    OpenMeteoEvidenceProvider,
    ResilientCryptoEvidenceProvider,
    ResilientWeatherEvidenceProvider,
    WebEvidencePipeline,
    WttrInEvidenceProvider,
    build_evidence_candidates_from_db,
)


logger = logging.getLogger(__name__)


def _configured_pid_file_for_stop(override_pid_file: str | None) -> str:
    if override_pid_file:
        return override_pid_file

    override_from_env_file = os.getenv("AMO_ENV_OVERRIDE", "1").lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
    dotenv_path = os.getenv("DOTENV_PATH", ".env")
    load_dotenv(dotenv_path=dotenv_path, override=override_from_env_file)
    return os.getenv("BOT_PID_FILE", ".state/amo_bot.pid")


async def _run_polling_with_runtimes(
    *,
    dreaming_runtime: DreamingRuntime,
    daily_runtime: DailyMemoryRuntime,
    tg: TelegramClient,
    offset_store: OffsetStore,
    timeout_seconds: int,
    limit: int,
    retry_max_seconds: int,
    dispatcher: Dispatcher,
    scheduled_tick,
) -> None:
    """Start background runtimes inside active polling loop and shut down cleanly."""
    dreaming_runtime.start()
    daily_runtime.start()
    try:
        await run_polling(
            tg,
            offset_store,
            timeout_seconds=timeout_seconds,
            limit=limit,
            retry_max_seconds=retry_max_seconds,
            dispatcher=dispatcher,
            scheduled_tick=scheduled_tick,
        )
    finally:
        await daily_runtime.stop()
        await dreaming_runtime.stop()


class SessionBoundWebtoolCapabilityDispatcher:
    """Create quota repo per execute call while reusing provider health in-process."""

    def __init__(self, *, session_factory) -> None:
        self._session_factory = session_factory
        self._provider_health = DbBackedProviderHealthRegistry(session_factory=session_factory)
        self._weather_evidence_provider = None
        self._crypto_evidence_provider = None

    def _ensure_evidence_providers(self) -> None:
        if self._weather_evidence_provider is not None and self._crypto_evidence_provider is not None:
            return
        weather_candidates = build_evidence_candidates_from_db(
            session_factory=self._session_factory,
            domain="weather",
            providers={
                "open_meteo_weather": OpenMeteoEvidenceProvider(),
                "wttr_in_weather": WttrInEvidenceProvider(),
            },
        )
        crypto_candidates = build_evidence_candidates_from_db(
            session_factory=self._session_factory,
            domain="crypto",
            providers={
                "coingecko_crypto": CoinGeckoEvidenceProvider(),
                "binance_crypto": BinanceTickerEvidenceProvider(),
            },
        )
        self._weather_evidence_provider = ResilientWeatherEvidenceProvider(
            weather_candidates,
            health=self._provider_health,
        )
        self._crypto_evidence_provider = ResilientCryptoEvidenceProvider(
            crypto_candidates,
            health=self._provider_health,
        )

    def execute(self, request):
        self._ensure_evidence_providers()
        with self._session_factory() as session:
            quota_repo = WebToolRoleQuotaRepository(session)
            browser_provider = None
            candidate = RealBrowserProviderAdapter()
            if candidate.available:
                browser_provider = candidate
            search_provider = RealWebsearchProviderAdapter(quota_limiter=quota_repo)
            scrape_provider = RealWebscrapeProviderAdapter()
            service = create_webtool_subagent_service(
                quota_repo=quota_repo,
                search_provider=search_provider,
                scrape_provider=scrape_provider,
                browser_provider=browser_provider,
                weather_evidence_provider=self._weather_evidence_provider,
                crypto_evidence_provider=self._crypto_evidence_provider,
                observation_writer=ResearchSourceObservationRepository(session),
            )
            dispatcher = WebtoolCapabilityDispatcher(quota_repo=quota_repo, service=service)
            return dispatcher.execute(request)


class SessionBoundSourcePreferenceRepository:
    """Open a short-lived DB session for each source preference lookup."""

    def __init__(self, *, session_factory) -> None:
        self._session_factory = session_factory

    def list_for_hosts(self, **kwargs):
        with self._session_factory() as session:
            return ResearchSourcePreferenceRepository(session).list_for_hosts(**kwargs)


def _build_parser() -> ArgumentParser:
    parser = ArgumentParser(prog="python -m amo_bot.main")
    parser.add_argument("--webui", action="store_true", help="Start Flask WebUI only")
    parser.add_argument("--serve", action="store_true", help="Start WebUI and Telegram polling together")
    parser.add_argument("--pid-file", help="Override BOT_PID_FILE for this invocation")
    parser.add_argument(
        "--stop",
        "--stop-running",
        dest="stop_running",
        action="store_true",
        help="Send SIGTERM to the running AMO bot process recorded in the PID file",
    )
    return parser


def run(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args([] if argv is None else argv)

    if args.stop_running:
        configured_pid_file = _configured_pid_file_for_stop(args.pid_file)
        result = stop_running_bot(configured_pid_file)
        print(result.message)
        raise SystemExit(0 if result.ok else 1)

    settings = get_settings()
    configured_pid_file = args.pid_file or getattr(settings, "bot_pid_file", _configured_pid_file_for_stop(None))

    setup_logging()
    init_db(settings.database_url)

    session_factory = create_session_factory(settings.database_url)
    with session_factory() as session:
        UserRoleRepository(session).bootstrap_owner_from_settings(
            owner_telegram_user_id=settings.webui_owner_telegram_id
        )

    tg = TelegramClient(token=settings.bot_token, base_url=settings.telegram_api_base)
    logger.info(
        "bot startup config: bot_username=%s ai_provider=%s ollama_url=%s ollama_model=%s",
        settings.bot_username,
        settings.ai_provider,
        settings.ollama_base_url,
        settings.ollama_model,
    )
    offset_store = OffsetStore(settings.offset_state_file)

    role_resolver = DBRoleResolver(session_factory)
    if hasattr(role_resolver, "set_telegram_client"):
        role_resolver.set_telegram_client(tg)
    ai_service = build_ai_provider(settings)
    message_persistence: ChatTopicPersistenceService | None = None

    async def persist_sent_result(
        *,
        chat_id: int,
        text: str,
        message_thread_id: int | None,
        result: object,
    ) -> None:
        if message_persistence is None or not isinstance(result, dict):
            return
        try:
            message_id = int(result.get("message_id"))
        except (TypeError, ValueError):
            return
        await message_persistence.persist_bot_sent_message(
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            message_id=message_id,
            text=text,
            bot_username=settings.bot_username,
        )

    async def send_owner_private_text(chat_id: int, text: str, _persist_sent_result=persist_sent_result) -> object:
        result = await tg.send_message(chat_id=chat_id, text=text)
        await _persist_sent_result(chat_id=chat_id, text=text, message_thread_id=None, result=result)
        return result

    async def send_text(chat_id: int, text: str, message_thread_id: int | None = None, _persist_sent_result=persist_sent_result) -> object:
        result = await tg.send_message(chat_id=chat_id, text=text, message_thread_id=message_thread_id)
        await _persist_sent_result(chat_id=chat_id, text=text, message_thread_id=message_thread_id, result=result)
        return result

    async def send_markup(
        chat_id: int,
        text: str,
        reply_markup: dict[str, object],
        message_thread_id: int | None = None,
        _persist_sent_result=persist_sent_result,
    ) -> object:
        result = await tg.send_message(
            chat_id=chat_id,
            text=text,
            message_thread_id=message_thread_id,
            reply_markup=reply_markup,
        )
        await _persist_sent_result(chat_id=chat_id, text=text, message_thread_id=message_thread_id, result=result)
        return result

    async def send_private_text_with_markup(
        chat_id: int,
        text: str,
        reply_markup: dict[str, object],
        _persist_sent_result=persist_sent_result,
    ) -> object:
        result = await tg.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
        await _persist_sent_result(chat_id=chat_id, text=text, message_thread_id=None, result=result)
        return result

    owner_notifier = OwnerNotifier(
        owner_telegram_user_id=settings.webui_owner_telegram_id,
        send_private_text=send_owner_private_text,
        send_private_markup=send_private_text_with_markup,
    )

    command_registry = create_builtin_registry(
        database_url=settings.database_url,
        ai_service=ai_service,
        owner_notifier=owner_notifier,
        prompt_timezone=settings.dreaming_timezone,
    )

    async def answer_callback(callback_query_id: str, text: str | None = None) -> object:
        return await tg.answer_callback_query(callback_query_id=callback_query_id, text=text)

    async def reply_text(
        chat_id: int,
        message_id: int,
        text: str,
        message_thread_id: int | None = None,
        _persist_sent_result=persist_sent_result,
    ) -> object:
        result = await tg.send_message(
            chat_id=chat_id,
            text=text,
            reply_to_message_id=message_id,
            message_thread_id=message_thread_id,
        )
        await _persist_sent_result(chat_id=chat_id, text=text, message_thread_id=message_thread_id, result=result)
        return result

    async def send_photo(
        chat_id: int,
        file_path: str,
        caption: str,
        message_thread_id: int | None = None,
        _persist_sent_result=persist_sent_result,
    ) -> object:
        result = await tg.send_photo(
            chat_id=chat_id,
            photo_path=file_path,
            caption=caption,
            message_thread_id=message_thread_id,
        )
        await _persist_sent_result(chat_id=chat_id, text=caption, message_thread_id=message_thread_id, result=result)
        return result

    async def send_document(
        chat_id: int,
        file_path: str,
        caption: str,
        message_thread_id: int | None = None,
        mime_type: str | None = None,
        _persist_sent_result=persist_sent_result,
    ) -> object:
        result = await tg.send_document(
            chat_id=chat_id,
            document_path=file_path,
            caption=caption,
            message_thread_id=message_thread_id,
            mime_type=mime_type,
        )
        await _persist_sent_result(chat_id=chat_id, text=caption, message_thread_id=message_thread_id, result=result)
        return result

    plugin_loader = PluginLoader(settings.amo_plugin_dir)

    plugin_command_executor = PluginCommandExecutor(
        loader=plugin_loader,
        session_factory=session_factory,
        send_message=send_text,
        reply=reply_text,
        reply_markup=send_markup,
        send_photo=send_photo,
        send_document=send_document,
        image_media_store=TelegramImageMediaStore(bot_token=settings.bot_token),
        enable_image_attachments=True,
        image_analyze_provider=ai_service,
    )

    scheduled_plugin_executor = ScheduledPluginExecutor(
        loader=plugin_loader,
        session_factory=session_factory,
        send_message=send_text,
        reply=reply_text,
        timeout_seconds=30.0,
    )

    message_persistence = ChatTopicPersistenceService(
        session_factory,
        send_private_message=send_private_text_with_markup,
        owner_notifier=owner_notifier,
        send_group_markup=send_markup,
        send_group_text=send_text,
        bot_username=settings.bot_username,
    )

    webtool_dispatcher = SessionBoundWebtoolCapabilityDispatcher(session_factory=session_factory)
    web_evidence_pipeline = WebEvidencePipeline(session_factory=session_factory)
    current_info_service = None
    if settings.amo_current_info_enabled:
        current_info_vector_components = build_current_info_vector_components_from_settings(
            settings,
            session_factory=session_factory,
        )
        current_info_vector_indexer = (
            current_info_vector_components[0] if current_info_vector_components is not None else None
        )
        current_info_embedding_provider = (
            current_info_vector_components[2] if current_info_vector_components is not None else None
        )
        current_info_research_provider = build_gpt_researcher_provider_from_settings(
            settings,
            embedding_provider=current_info_embedding_provider,
        )
        current_info_search_provider = build_search_broker_from_settings(settings)
        if current_info_search_provider is not None or current_info_research_provider is not None:
            current_info_fetch_provider = build_cached_fetch_provider_from_settings(
                settings,
                session_factory=session_factory,
                fetch_provider=build_document_fetcher_from_settings(settings),
                vector_indexer=current_info_vector_indexer,
            )
            current_info_service = CurrentInfoService(
                search_provider=current_info_search_provider,
                fetch_provider=current_info_fetch_provider,
                retrieval_provider=build_current_info_retrieval_provider_from_settings(
                    settings,
                    session_factory=session_factory,
                    vector_components=current_info_vector_components,
                ),
                research_provider=current_info_research_provider,
                source_preference_repository=SessionBoundSourcePreferenceRepository(session_factory=session_factory),
                safety_config=build_current_info_safety_config_from_settings(settings),
            )

    dispatcher = Dispatcher(
        command_registry=command_registry,
        role_resolver=role_resolver,
        send_text=send_text,
        send_markup=send_markup,
        send_private_markup=send_markup,
        answer_callback=answer_callback,
        bot_username=settings.bot_username,
        message_persistence=message_persistence,
        plugin_command_executor=plugin_command_executor,
        database_url=settings.database_url,
        ai_service=ai_service,
        owner_notifier=owner_notifier,
        webtool_dispatcher=webtool_dispatcher,
        web_evidence_pipeline=web_evidence_pipeline,
        current_info_service=current_info_service,
        current_info_enabled=settings.amo_current_info_enabled,
        current_info_timeout_seconds=settings.amo_current_info_timeout_seconds,
        current_info_research_timeout_seconds=float(getattr(settings, "amo_research_timeout_seconds", 300.0)),
        current_info_late_synthesis_timeout_seconds=settings.amo_current_info_late_synthesis_timeout_seconds,
        current_info_max_results=settings.amo_current_info_max_results,
        current_info_max_documents=settings.amo_current_info_max_documents,
        prompt_timezone=settings.dreaming_timezone,
    )

    # Dreaming / Memory-Curation Runtime — disabled by default.
    dreaming_repo = TopicAgentMemoryRepository(session_factory())
    dreaming_runtime = DreamingRuntime(
        repository=dreaming_repo,
        enabled=settings.dreaming_enabled,
        timeout_seconds=settings.dreaming_timeout_seconds,
        max_daily_candidates_per_scope=settings.dreaming_max_daily_candidates_per_scope,
        max_promotions_per_scope=settings.dreaming_max_promotions_per_scope,
        auto_approve=settings.dreaming_auto_approve_mode,
        window_start=settings.dreaming_window_start,
        window_end=settings.dreaming_window_end,
        timezone=settings.dreaming_timezone,
        max_scopes_per_batch=settings.dreaming_max_scopes_per_batch,
        batch_pause_seconds=settings.dreaming_batch_pause_seconds,
        jitter_seconds=settings.dreaming_jitter_seconds,
        min_daily_memories=settings.dreaming_min_daily_memories,
        lookback_days=settings.dreaming_lookback_days,
    )

    daily_runtime = DailyMemoryRuntime(
        repository=TopicAgentMemoryRepository(session_factory()),
        enabled=settings.memory_daily_enabled,
        interval_seconds=settings.memory_daily_interval_seconds,
        max_input_messages=settings.memory_daily_max_input_messages,
        max_chars_per_message=settings.memory_daily_max_chars_per_message,
        max_summary_chars=settings.memory_daily_max_summary_chars,
        min_messages=settings.memory_daily_min_messages,
        max_scopes_per_run=settings.memory_daily_max_scopes_per_run,
    )

    with pid_file(configured_pid_file):
        if args.webui:
            app = create_flask_app(settings=settings)
            log_event(
                logger, logging.INFO,
                event="bot.start",
                component="main",
                extra={"mode": "webui_only", "host": settings.webui_host, "port": settings.webui_port},
            )
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

            log_event(
                logger, logging.INFO,
                event="bot.start",
                component="main",
                extra={"mode": "webui_plus_polling", "webui_host": settings.webui_host, "webui_port": settings.webui_port},
            )
            asyncio.run(
                _run_polling_with_runtimes(
                    dreaming_runtime=dreaming_runtime,
                    daily_runtime=daily_runtime,
                    tg=tg,
                    offset_store=offset_store,
                    timeout_seconds=settings.poll_timeout_seconds,
                    limit=settings.poll_limit,
                    retry_max_seconds=settings.poll_retry_max_seconds,
                    dispatcher=dispatcher,
                    scheduled_tick=scheduled_plugin_executor.run_due_once,
                )
            )
            return

        log_event(
            logger, logging.INFO,
            event="bot.start",
            component="main",
            extra={"mode": "polling_only"},
        )
        asyncio.run(
            _run_polling_with_runtimes(
                dreaming_runtime=dreaming_runtime,
                daily_runtime=daily_runtime,
                tg=tg,
                offset_store=offset_store,
                timeout_seconds=settings.poll_timeout_seconds,
                limit=settings.poll_limit,
                retry_max_seconds=settings.poll_retry_max_seconds,
                dispatcher=dispatcher,
                scheduled_tick=scheduled_plugin_executor.run_due_once,
            )
        )


if __name__ == "__main__":
    run()
