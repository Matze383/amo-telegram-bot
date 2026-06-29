from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Awaitable, Callable

from amo_bot.core.logging import (
    log_event,
    set_request_id,
)
from amo_bot.telegram.client import TelegramApiError, TelegramClient, TelegramRateLimitError
from amo_bot.db.base import create_session_factory
from amo_bot.db.telegram_queue import TelegramIncomingQueueRepository

logger = logging.getLogger(__name__)

_COMPONENT = "telegram.queue_poller"


class OffsetStore:
    """Defensiver MVP-Store via State-Datei statt früher DB-Kopplung."""

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> int:
        if not self.path.exists():
            return 0
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            value = int(data.get("last_update_id", 0))
            return max(0, value)
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            logger.warning("offset-state unreadable, fallback to 0")
            return 0

    def save(self, update_id: int) -> None:
        payload = json.dumps({"last_update_id": max(0, int(update_id))})

        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=self.path.parent,
            prefix=f"{self.path.name}.",
            suffix=".tmp",
            delete=False,
        ) as tmp_file:
            tmp_file.write(payload)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
            tmp_path = Path(tmp_file.name)

        os.replace(tmp_path, self.path)


async def run_queue_poller(
    tg: TelegramClient,
    offset_store: OffsetStore,
    *,
    database_url: str,
    timeout_seconds: int,
    limit: int,
    retry_max_seconds: int,
    scheduled_tick: Callable[[], Awaitable[None]] | None = None,
    scheduled_tick_interval_seconds: float = 5.0,
) -> None:
    """Fetch Telegram updates and persist raw updates before advancing offset."""

    session_factory = create_session_factory(database_url)
    offset = offset_store.load() + 1
    backoff = 1
    loop = asyncio.get_running_loop()
    next_tick_at = loop.time()

    while True:
        try:
            updates = await tg.get_updates(offset=offset, timeout=timeout_seconds, limit=limit)
            backoff = 1

            if scheduled_tick is not None and loop.time() >= next_tick_at:
                try:
                    await scheduled_tick()
                except Exception as exc:
                    logger.exception("scheduled tick failed: %s", exc)
                next_tick_at = loop.time() + max(0.1, scheduled_tick_interval_seconds)

            for update in updates:
                update_id = int(update.get("update_id", 0))
                if update_id < offset:
                    continue
                set_request_id(f"upd-{update_id}")
                try:
                    with session_factory() as session:
                        TelegramIncomingQueueRepository(session).enqueue_update(update)
                    offset = update_id + 1
                    offset_store.save(update_id)
                    log_event(
                        logger,
                        logging.INFO,
                        event="telegram.update.queued",
                        component=_COMPONENT,
                        update_id=update_id,
                    )
                except Exception as exc:
                    log_event(
                        logger,
                        logging.ERROR,
                        event="telegram.update.queue_error",
                        component=_COMPONENT,
                        update_id=update_id,
                        outcome="retry",
                        extra={"error": str(exc)[:256]},
                    )
                    raise
                finally:
                    try:
                        set_request_id(None)  # type: ignore[arg-type]
                    except Exception:
                        pass

        except TelegramRateLimitError as exc:
            log_event(
                logger,
                logging.WARNING,
                event="telegram.rate_limit",
                component=_COMPONENT,
                outcome="backoff",
                reason_code="rate_limit",
                extra={"retry_after": exc.retry_after, "backoff_s": backoff},
            )
            await tg.backoff_sleep(exc.retry_after)
        except TelegramApiError as exc:
            log_event(
                logger,
                logging.WARNING,
                event="telegram.api_error",
                component=_COMPONENT,
                outcome="retry",
                extra={"error": str(exc), "backoff_s": backoff},
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, retry_max_seconds)
        except Exception as exc:
            log_event(
                logger,
                logging.ERROR,
                event="telegram.queue_poller.error",
                component=_COMPONENT,
                outcome="retry",
                extra={"error": str(exc), "backoff_s": backoff},
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, retry_max_seconds)
