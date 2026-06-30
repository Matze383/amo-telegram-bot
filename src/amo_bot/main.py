from __future__ import annotations

import asyncio
import logging
import os
import signal
import threading
from argparse import ArgumentParser
from typing import Any

from dotenv import load_dotenv

from amo_bot.ai.providers import build_ai_provider
from amo_bot.config.settings import get_settings
from amo_bot.core.logging import setup_logging, log_event
from amo_bot.db.base import create_session_factory
from amo_bot.db.context_memory_vector import ContextMemoryVectorRecall, ContextMemoryVectorRepository
from amo_bot.db.context_memory_vector_runtime import ContextMemoryVectorBackfillRuntime
from amo_bot.db.init_db import init_db
from amo_bot.db.repositories import (
    ResearchSourceObservationRepository,
    ResearchSourcePreferenceRepository,
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
from amo_bot.telegram.outbox_sender import OutboxSender
from amo_bot.telegram.owner_notify import OwnerNotifier
from amo_bot.telegram.queue_poller import OffsetStore, run_queue_poller
from amo_bot.telegram.role_resolver import DBRoleResolver
from amo_bot.telegram.supervisor import ManagedProcess, TelegramProcessSupervisor
from amo_bot.telegram.topic_worker import QueueBackedTelegramSender, QueueWorker
from amo_bot.webui.flask_app import create_flask_app
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
    build_embedding_provider_from_settings,
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


_LEGACY_RUNTIME_ERROR = (
    "AMO_TELEGRAM_RUNTIME legacy polling runtime removed; only queue supported; "
    "remove variable or set queue."
)


def _validate_telegram_runtime_env() -> None:
    raw_runtime = os.getenv("AMO_TELEGRAM_RUNTIME")
    if raw_runtime is None or raw_runtime.strip() in {"", "queue"}:
        return
    raise RuntimeError(_LEGACY_RUNTIME_ERROR)


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


def _build_current_info_service(settings, *, session_factory):
    if not settings.amo_current_info_enabled:
        return None
    current_info_vector_components = build_current_info_vector_components_from_settings(
        settings,
        session_factory=session_factory,
    )
    current_info_vector_indexer = current_info_vector_components[0] if current_info_vector_components is not None else None
    current_info_embedding_provider = current_info_vector_components[2] if current_info_vector_components is not None else None
    current_info_research_provider = build_gpt_researcher_provider_from_settings(
        settings,
        embedding_provider=current_info_embedding_provider,
    )
    current_info_search_provider = build_search_broker_from_settings(settings)
    if current_info_search_provider is None and current_info_research_provider is None:
        return None
    current_info_fetch_provider = build_cached_fetch_provider_from_settings(
        settings,
        session_factory=session_factory,
        fetch_provider=build_document_fetcher_from_settings(settings),
        vector_indexer=current_info_vector_indexer,
    )
    return CurrentInfoService(
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


def _build_context_memory_vector_repository(settings, *, session_factory):
    if not bool(getattr(settings, "amo_vector_enabled", False)):
        return None
    embedding_model = str(getattr(settings, "amo_vector_embedding_model", "") or "").strip()
    if not embedding_model:
        return None
    return ContextMemoryVectorRepository(session_factory=session_factory, embedding_model=embedding_model)


def _build_context_memory_vector_recall(
    settings,
    *,
    context_vector_repository: ContextMemoryVectorRepository | None,
):
    if context_vector_repository is None:
        return None
    if not bool(getattr(settings, "amo_vector_enabled", False)):
        return None
    embedding_model = str(getattr(settings, "amo_vector_embedding_model", "") or "").strip()
    if not embedding_model:
        return None
    return ContextMemoryVectorRecall(
        vector_search=context_vector_repository,
        embedding_provider=build_embedding_provider_from_settings(settings),
    )


def _build_context_memory_vector_backfill_runtime(
    settings,
    *,
    context_vector_repository: ContextMemoryVectorRepository | None,
) -> ContextMemoryVectorBackfillRuntime | None:
    if context_vector_repository is None:
        return None
    if not bool(getattr(settings, "amo_vector_enabled", False)):
        return None
    return ContextMemoryVectorBackfillRuntime(
        repository=context_vector_repository,
        embedding_provider=build_embedding_provider_from_settings(settings),
    )


def _build_queue_worker_dispatcher(
    *,
    settings,
    session_factory,
    tg: TelegramClient,
    sender: QueueBackedTelegramSender,
) -> Dispatcher:
    role_resolver = DBRoleResolver(session_factory)
    if hasattr(role_resolver, "set_telegram_client"):
        role_resolver.set_telegram_client(tg)
    ai_service = build_ai_provider(settings)

    async def send_text(chat_id: int, text: str, message_thread_id: int | None = None) -> object:
        return await sender.send_text(chat_id, text, message_thread_id)

    async def send_markup(
        chat_id: int,
        text: str,
        reply_markup: dict[str, object],
        message_thread_id: int | None = None,
    ) -> object:
        return await sender.send_markup(chat_id, text, reply_markup, message_thread_id)

    async def reply_text(chat_id: int, message_id: int, text: str, message_thread_id: int | None = None) -> object:
        scoped_sender = QueueBackedTelegramSender(
            database_url=settings.database_url,
            topic_id=message_thread_id,
            trigger_message_id=message_id,
            job_id=sender.job_id,
        )
        return await scoped_sender.send_text(chat_id, text, message_thread_id)

    async def reply_markup(
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup: dict[str, Any],
        message_thread_id: int | None = None,
    ) -> object:
        scoped_sender = QueueBackedTelegramSender(
            database_url=settings.database_url,
            topic_id=message_thread_id,
            trigger_message_id=message_id,
            job_id=sender.job_id,
        )
        return await scoped_sender.send_markup(chat_id, text, reply_markup, message_thread_id)

    async def answer_callback(callback_query_id: str, text: str | None = None) -> object:
        return await tg.answer_callback_query(callback_query_id=callback_query_id, text=text)

    async def send_photo(*args, **kwargs) -> object:  # noqa: ANN002,ANN003
        raise RuntimeError("send_photo transport is not available in telegram queue worker runtime")

    async def send_document(*args, **kwargs) -> object:  # noqa: ANN002,ANN003
        raise RuntimeError("send_document transport is not available in telegram queue worker runtime")

    context_vector_repository = _build_context_memory_vector_repository(settings, session_factory=session_factory)
    context_vector_recall = _build_context_memory_vector_recall(
        settings,
        context_vector_repository=context_vector_repository,
    )
    owner_notifier = OwnerNotifier(
        owner_telegram_user_id=settings.webui_owner_telegram_id,
        send_private_text=send_text,
        send_private_markup=lambda chat_id, text, reply_markup: send_markup(chat_id, text, reply_markup, None),
    )
    command_registry = create_builtin_registry(
        database_url=settings.database_url,
        ai_service=ai_service,
        owner_notifier=owner_notifier,
        prompt_timezone=settings.dreaming_timezone,
    )
    plugin_loader = PluginLoader(settings.amo_plugin_dir)
    plugin_command_executor = PluginCommandExecutor(
        loader=plugin_loader,
        session_factory=session_factory,
        send_message=send_text,
        reply=reply_text,
        reply_markup=reply_markup,
        send_photo=send_photo,
        send_document=send_document,
        image_media_store=TelegramImageMediaStore(bot_token=settings.bot_token),
        enable_image_attachments=True,
        image_analyze_provider=ai_service,
    )
    message_persistence = ChatTopicPersistenceService(
        session_factory,
        owner_notifier=owner_notifier,
        send_group_markup=send_markup,
        send_group_text=send_text,
        bot_username=settings.bot_username,
        context_vector_repository=context_vector_repository,
    )
    return Dispatcher(
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
        webtool_dispatcher=SessionBoundWebtoolCapabilityDispatcher(session_factory=session_factory),
        web_evidence_pipeline=WebEvidencePipeline(session_factory=session_factory),
        current_info_service=_build_current_info_service(settings, session_factory=session_factory),
        current_info_enabled=settings.amo_current_info_enabled,
        current_info_timeout_seconds=settings.amo_current_info_timeout_seconds,
        current_info_research_timeout_seconds=float(getattr(settings, "amo_research_timeout_seconds", 300.0)),
        current_info_late_synthesis_timeout_seconds=settings.amo_current_info_late_synthesis_timeout_seconds,
        current_info_max_results=settings.amo_current_info_max_results,
        current_info_max_documents=settings.amo_current_info_max_documents,
        context_memory_vector_recall=context_vector_recall,
        prompt_timezone=settings.dreaming_timezone,
    )


def run_queue_sender_process(*, idle_sleep_seconds: float | None = None) -> None:
    settings = get_settings()
    setup_logging()
    init_db(settings.database_url)
    session_factory = create_session_factory(settings.database_url)
    tg = TelegramClient(token=settings.bot_token, base_url=settings.telegram_api_base)
    persistence = ChatTopicPersistenceService(
        session_factory,
        bot_username=settings.bot_username,
        context_vector_repository=_build_context_memory_vector_repository(settings, session_factory=session_factory),
    )
    sender = OutboxSender(
        database_url=settings.database_url,
        telegram_client=tg,
        sender_id=f"sender:{os.getpid()}",
        message_persistence=persistence,
        bot_username=settings.bot_username,
    )
    asyncio.run(
        sender.run_forever(
            idle_sleep_seconds=idle_sleep_seconds or settings.amo_telegram_queue_idle_sleep_seconds,
        )
    )


def run_queue_poller_process(*, idle_sleep_seconds: float | None = None) -> None:
    del idle_sleep_seconds
    settings = get_settings()
    setup_logging()
    init_db(settings.database_url)
    session_factory = create_session_factory(settings.database_url)
    tg = TelegramClient(token=settings.bot_token, base_url=settings.telegram_api_base)
    scheduled_sender = QueueBackedTelegramSender(
        database_url=settings.database_url,
        topic_id=None,
        trigger_message_id=None,
        job_id="scheduled-plugin",
    )
    plugin_loader = PluginLoader(settings.amo_plugin_dir)
    scheduled_plugin_executor = ScheduledPluginExecutor(
        loader=plugin_loader,
        session_factory=session_factory,
        send_message=scheduled_sender.send_text,
        reply=lambda chat_id, message_id, text, message_thread_id=None: QueueBackedTelegramSender(
            database_url=settings.database_url,
            topic_id=message_thread_id,
            trigger_message_id=message_id,
            job_id="scheduled-plugin",
        ).send_text(chat_id, text, message_thread_id),
        timeout_seconds=30.0,
    )
    asyncio.run(
        run_queue_poller(
            tg,
            OffsetStore(settings.offset_state_file),
            database_url=settings.database_url,
            timeout_seconds=settings.poll_timeout_seconds,
            limit=settings.poll_limit,
            retry_max_seconds=settings.poll_retry_max_seconds,
            scheduled_tick=scheduled_plugin_executor.run_due_once,
        )
    )


def run_queue_worker_process(worker_index: int, *, idle_sleep_seconds: float | None = None) -> None:
    settings = get_settings()
    setup_logging()
    init_db(settings.database_url)
    session_factory = create_session_factory(settings.database_url)
    tg = TelegramClient(token=settings.bot_token, base_url=settings.telegram_api_base)
    restart_requested = False

    def request_runtime_restart() -> None:
        nonlocal restart_requested
        restart_requested = True

    def dispatcher_factory(sender: QueueBackedTelegramSender) -> Dispatcher:
        dispatcher = _build_queue_worker_dispatcher(
            settings=settings,
            session_factory=session_factory,
            tg=tg,
            sender=sender,
        )
        dispatcher.restart_terminator = request_runtime_restart
        return dispatcher

    worker = QueueWorker(
        database_url=settings.database_url,
        dispatcher_factory=dispatcher_factory,
        worker_id=f"queue-worker:{worker_index}:{os.getpid()}",
    )

    async def run_worker() -> None:
        context_vector_runtime = None
        if worker_index == 1:
            context_vector_runtime = _build_context_memory_vector_backfill_runtime(
                settings,
                context_vector_repository=_build_context_memory_vector_repository(
                    settings,
                    session_factory=session_factory,
                ),
            )
            if context_vector_runtime is not None:
                context_vector_runtime.start()
        try:
            await worker.run_forever(
                idle_sleep_seconds=idle_sleep_seconds or settings.amo_telegram_queue_idle_sleep_seconds,
                should_stop=lambda: restart_requested,
            )
        finally:
            if context_vector_runtime is not None:
                await context_vector_runtime.stop()

    asyncio.run(run_worker())
    if restart_requested:
        os.kill(os.getppid(), signal.SIGTERM)


def _queue_worker_processes(settings) -> list[ManagedProcess]:
    return [
        ManagedProcess(
            name=f"telegram-queue-worker-{index}",
            kind="queue_worker",
            target=run_queue_worker_process,
            args=(index,),
        )
        for index in range(1, settings.amo_telegram_queue_worker_count + 1)
    ]


def _run_queue_runtime(settings) -> None:
    supervisor = TelegramProcessSupervisor(database_url=settings.database_url)
    sender = ManagedProcess(
        name="telegram-outbox-sender",
        kind="sender",
        target=run_queue_sender_process,
    )
    poller = ManagedProcess(
        name="telegram-update-poller",
        kind="poller",
        target=run_queue_poller_process,
    )

    supervisor.run_runtime(
        sender=sender,
        workers=_queue_worker_processes(settings),
        poller=poller,
    )


def _build_parser() -> ArgumentParser:
    parser = ArgumentParser(prog="python -m amo_bot.main")
    parser.add_argument("--webui", action="store_true", help="Start Flask WebUI only")
    parser.add_argument("--serve", action="store_true", help="Start WebUI and Telegram queue runtime together")
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
    _validate_telegram_runtime_env()
    configured_pid_file = args.pid_file or getattr(settings, "bot_pid_file", _configured_pid_file_for_stop(None))

    setup_logging()
    init_db(settings.database_url)

    session_factory = create_session_factory(settings.database_url)
    with session_factory() as session:
        UserRoleRepository(session).bootstrap_owner_from_settings(
            owner_telegram_user_id=settings.webui_owner_telegram_id
        )

    logger.info(
        "bot startup config: bot_username=%s runtime=%s ai_provider=%s ollama_url=%s ollama_model=%s",
        settings.bot_username,
        "queue",
        settings.ai_provider,
        settings.ollama_base_url,
        settings.ollama_model,
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
                extra={
                    "mode": "webui_plus_queue",
                    "webui_host": settings.webui_host,
                    "webui_port": settings.webui_port,
                },
            )

        else:
            log_event(
                logger,
                logging.INFO,
                event="bot.start",
                component="main",
                extra={"mode": "queue_only"},
            )
        _run_queue_runtime(settings)


if __name__ == "__main__":
    run()
