from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import os
from typing import Any, Callable, Protocol

from amo_bot.db.base import create_session_factory
from amo_bot.db.telegram_queue import (
    DEFAULT_LEASE_SECONDS,
    DEFAULT_MAX_ATTEMPTS,
    TelegramIncomingQueueRepository,
    TelegramOutgoingQueueRepository,
    TelegramProcessHealthRepository,
)
from amo_bot.telegram.dispatcher import Dispatcher
from amo_bot.telegram.outbound_text import split_telegram_message_text


logger = logging.getLogger(__name__)


class DispatcherFactory(Protocol):
    def __call__(self, send_text: Any, send_markup: Any | None = None) -> Dispatcher: ...


@dataclass(slots=True)
class QueueBackedTelegramSender:
    database_url: str
    topic_id: int | None
    trigger_message_id: int | None
    job_id: str

    async def send_text(self, chat_id: int, text: str, message_thread_id: int | None = None) -> dict[str, object]:
        with create_session_factory(self.database_url)() as session:
            item = TelegramOutgoingQueueRepository(session).enqueue_text(
                chat_id=chat_id,
                topic_id=message_thread_id if message_thread_id is not None else self.topic_id,
                trigger_message_id=self.trigger_message_id,
                text=text,
                job_id=self.job_id,
            )
        return {"queued": True, "outbox_id": item.id, "job_id": item.job_id}

    async def send_markup(
        self,
        chat_id: int,
        text: str,
        reply_markup: dict[str, Any],
        message_thread_id: int | None = None,
    ) -> dict[str, object]:
        with create_session_factory(self.database_url)() as session:
            item = TelegramOutgoingQueueRepository(session).enqueue_text(
                chat_id=chat_id,
                topic_id=message_thread_id if message_thread_id is not None else self.topic_id,
                trigger_message_id=self.trigger_message_id,
                text=text,
                job_id=self.job_id,
                reply_markup=reply_markup,
            )
        return {"queued": True, "outbox_id": item.id, "job_id": item.job_id}


@dataclass(slots=True)
class TopicWorker:
    database_url: str
    chat_id: int
    topic_id: int | None
    dispatcher_factory: Callable[[QueueBackedTelegramSender], Dispatcher]
    worker_id: str | None = None
    lease_seconds: int = DEFAULT_LEASE_SECONDS
    max_attempts: int = DEFAULT_MAX_ATTEMPTS

    def __post_init__(self) -> None:
        if self.worker_id is None:
            topic_label = "none" if self.topic_id is None else str(self.topic_id)
            self.worker_id = f"topic:{self.chat_id}:{topic_label}:{os.getpid()}"

    async def process_one(self) -> bool:
        assert self.worker_id is not None
        with create_session_factory(self.database_url)() as session:
            item = TelegramIncomingQueueRepository(session).claim_next_for_topic(
                chat_id=self.chat_id,
                topic_id=self.topic_id,
                worker_id=self.worker_id,
                lease_seconds=self.lease_seconds,
                max_attempts=self.max_attempts,
            )
        if item is None:
            return False

        sender = QueueBackedTelegramSender(
            database_url=self.database_url,
            topic_id=item.topic_id,
            trigger_message_id=item.message_id,
            job_id=f"in-{item.id}-{item.telegram_update_id}",
        )
        dispatcher = self.dispatcher_factory(sender)
        raw_update = item.payload.get("raw_update")
        try:
            await dispatcher.handle_raw_update(raw_update)
        except Exception as exc:
            logger.exception("topic worker failed item_id=%s", item.id)
            with create_session_factory(self.database_url)() as session:
                TelegramIncomingQueueRepository(session).fail(
                    item.id,
                    worker_id=self.worker_id,
                    error=exc,
                    max_attempts=self.max_attempts,
                )
            return False

        with create_session_factory(self.database_url)() as session:
            TelegramIncomingQueueRepository(session).complete(item.id, worker_id=self.worker_id)
        return True

    async def run_forever(self, *, idle_sleep_seconds: float = 0.2, stop_event: asyncio.Event | None = None) -> None:
        assert self.worker_id is not None
        while stop_event is None or not stop_event.is_set():
            with create_session_factory(self.database_url)() as session:
                TelegramProcessHealthRepository(session).heartbeat(
                    process_name=self.worker_id,
                    process_kind="topic",
                    chat_id=self.chat_id,
                    topic_id=self.topic_id,
                    status="running",
                )
            processed = await self.process_one()
            if not processed:
                await asyncio.sleep(max(0.05, idle_sleep_seconds))


def make_outbox_send_text(
    *,
    database_url: str,
    trigger_message_id: int | None,
    topic_id: int | None,
    job_id: str,
):
    sender = QueueBackedTelegramSender(
        database_url=database_url,
        topic_id=topic_id,
        trigger_message_id=trigger_message_id,
        job_id=job_id,
    )
    return sender.send_text
