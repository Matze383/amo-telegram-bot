from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import os
from typing import Any, Protocol

from amo_bot.db.base import create_session_factory
from amo_bot.db.telegram_queue import (
    DEFAULT_LEASE_SECONDS,
    DEFAULT_MAX_ATTEMPTS,
    TelegramOutgoingQueueRepository,
    TelegramProcessHealthRepository,
)
from amo_bot.telegram.client import TelegramRateLimitError


logger = logging.getLogger(__name__)


class TelegramSendClient(Protocol):
    async def send_message(
        self,
        chat_id: int,
        text: str,
        reply_to_message_id: int | None = None,
        message_thread_id: int | None = None,
        reply_markup: dict | None = None,
        parse_mode: str | None = None,
    ) -> dict: ...


class BotMessagePersistence(Protocol):
    async def persist_bot_sent_message(
        self,
        *,
        chat_id: int,
        message_thread_id: int | None,
        message_id: int,
        text: str,
        bot_username: str | None = None,
    ) -> None: ...


@dataclass(slots=True)
class OutboxSender:
    database_url: str
    telegram_client: TelegramSendClient
    sender_id: str | None = None
    lease_seconds: int = DEFAULT_LEASE_SECONDS
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    message_persistence: BotMessagePersistence | None = None
    bot_username: str | None = None

    def __post_init__(self) -> None:
        if self.sender_id is None:
            self.sender_id = f"sender:{os.getpid()}"

    async def send_one(self) -> bool:
        assert self.sender_id is not None
        if await self._finalize_one_sent_unconfirmed():
            return True
        with create_session_factory(self.database_url)() as session:
            item = TelegramOutgoingQueueRepository(session).claim_next(
                sender_id=self.sender_id,
                lease_seconds=self.lease_seconds,
                max_attempts=self.max_attempts,
            )
        if item is None:
            return False
        try:
            result = await self.telegram_client.send_message(
                chat_id=item.chat_id,
                text=item.text,
                reply_to_message_id=item.trigger_message_id,
                message_thread_id=item.topic_id,
                reply_markup=item.reply_markup,
                parse_mode=item.parse_mode,
            )
        except TelegramRateLimitError as exc:
            with create_session_factory(self.database_url)() as session:
                TelegramOutgoingQueueRepository(session).fail(
                    item.id,
                    sender_id=self.sender_id,
                    error=exc,
                    retry_after_seconds=exc.retry_after,
                    max_attempts=self.max_attempts,
                )
            return False
        except Exception as exc:
            logger.exception("outbox send failed item_id=%s", item.id)
            with create_session_factory(self.database_url)() as session:
                TelegramOutgoingQueueRepository(session).fail(
                    item.id,
                    sender_id=self.sender_id,
                    error=exc,
                    max_attempts=self.max_attempts,
                )
            return False

        sent_message_id = self._extract_message_id(result)
        try:
            await self._persist_sent_message(item=item, message_id=sent_message_id)
            with create_session_factory(self.database_url)() as session:
                completed = TelegramOutgoingQueueRepository(session).complete_sent(item.id, sender_id=self.sender_id)
            if not completed:
                raise RuntimeError("sent outbox item could not be completed")
        except Exception as exc:
            logger.exception("outbox post-send finalization failed item_id=%s", item.id)
            with create_session_factory(self.database_url)() as session:
                TelegramOutgoingQueueRepository(session).mark_sent_unconfirmed(
                    item.id,
                    sender_id=self.sender_id,
                    error=exc,
                    telegram_message_id=sent_message_id,
                    max_attempts=self.max_attempts,
                )
            return False
        return True

    async def _finalize_one_sent_unconfirmed(self) -> bool:
        assert self.sender_id is not None
        with create_session_factory(self.database_url)() as session:
            item = TelegramOutgoingQueueRepository(session).claim_sent_unconfirmed_for_finalization(
                sender_id=self.sender_id,
                lease_seconds=self.lease_seconds,
                max_attempts=self.max_attempts,
            )
        if item is None:
            return False
        try:
            await self._persist_sent_message(item=item, message_id=item.sent_message_id)
            with create_session_factory(self.database_url)() as session:
                completed = TelegramOutgoingQueueRepository(session).complete_sent_unconfirmed(
                    item.id,
                    sender_id=self.sender_id,
                )
            if not completed:
                raise RuntimeError("sent_unconfirmed outbox item could not be completed")
        except Exception as exc:
            logger.exception("outbox sent_unconfirmed finalization failed item_id=%s", item.id)
            with create_session_factory(self.database_url)() as session:
                TelegramOutgoingQueueRepository(session).fail_sent_unconfirmed_finalization(
                    item.id,
                    sender_id=self.sender_id,
                    error=exc,
                    max_attempts=self.max_attempts,
                )
            return False
        return True

    @staticmethod
    def _extract_message_id(result: object) -> int | None:
        if not isinstance(result, dict):
            return
        try:
            return int(result.get("message_id"))
        except (TypeError, ValueError):
            return

    async def _persist_sent_message(self, *, item: Any, message_id: int | None) -> None:
        if self.message_persistence is None:
            return
        if message_id is None:
            raise RuntimeError("sent Telegram message_id missing from finalization payload")
        await self.message_persistence.persist_bot_sent_message(
            chat_id=item.chat_id,
            message_thread_id=item.topic_id,
            message_id=message_id,
            text=item.text,
            bot_username=self.bot_username,
        )

    async def run_forever(self, *, idle_sleep_seconds: float = 0.2, stop_event: asyncio.Event | None = None) -> None:
        assert self.sender_id is not None
        while stop_event is None or not stop_event.is_set():
            with create_session_factory(self.database_url)() as session:
                TelegramProcessHealthRepository(session).heartbeat(
                    process_name=self.sender_id,
                    process_kind="sender",
                    status="running",
                )
            sent = await self.send_one()
            if not sent:
                await asyncio.sleep(max(0.05, idle_sleep_seconds))
