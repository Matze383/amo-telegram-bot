from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from amo_bot.telegram.client import TelegramApiError, TelegramClient, TelegramRateLimitError
from amo_bot.telegram.dispatcher import Dispatcher

logger = logging.getLogger(__name__)


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


async def run_polling(
    tg: TelegramClient,
    offset_store: OffsetStore,
    timeout_seconds: int,
    limit: int,
    retry_max_seconds: int,
    dispatcher: Dispatcher | None = None,
) -> None:
    offset = offset_store.load() + 1
    backoff = 1

    while True:
        try:
            updates = await tg.get_updates(offset=offset, timeout=timeout_seconds, limit=limit)
            backoff = 1

            for update in updates:
                update_id = int(update.get("update_id", 0))
                if update_id < offset:
                    continue

                logger.info("received update_id=%s", update_id)
                if dispatcher is not None:
                    await dispatcher.handle_raw_update(update)

                offset = update_id + 1
                offset_store.save(update_id)

        except TelegramRateLimitError as exc:
            await tg.backoff_sleep(exc.retry_after)
        except TelegramApiError as exc:
            logger.warning("telegram api error: %s", exc)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, retry_max_seconds)
        except Exception as exc:
            logger.exception("unexpected polling error: %s", exc)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, retry_max_seconds)
