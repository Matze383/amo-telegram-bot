from __future__ import annotations

import asyncio

from sqlalchemy import select

from amo_bot.db.base import create_session_factory
from amo_bot.db.init_db import init_db
from amo_bot.db.models import TelegramIncomingQueue
from amo_bot.telegram.queue_poller import OffsetStore, run_queue_poller


class _DummyTelegramClient:
    def __init__(self) -> None:
        self.calls = 0

    async def get_updates(self, *, offset: int, timeout: int, limit: int):
        self.calls += 1
        if self.calls >= 3:
            raise RuntimeError("stop-loop")
        return []


class _SingleRestartUpdateTelegramClient:
    def __init__(self) -> None:
        self.calls = 0

    async def get_updates(self, *, offset: int, timeout: int, limit: int):
        self.calls += 1
        if self.calls > 1:
            await asyncio.sleep(1)
        return [{"update_id": 268714202, "message": {"text": "/restart"}}]


def test_run_queue_poller_calls_scheduled_tick(tmp_path) -> None:
    tg = _DummyTelegramClient()
    offset_store = OffsetStore(":memory:")
    database_url = f"sqlite:///{tmp_path / 'queue.db'}"
    init_db(database_url)
    ticks: list[str] = []

    async def _tick() -> None:
        ticks.append("ok")

    async def _run() -> None:
        await asyncio.wait_for(
            run_queue_poller(
                tg=tg,
                offset_store=offset_store,
                database_url=database_url,
                timeout_seconds=1,
                limit=1,
                retry_max_seconds=1,
                scheduled_tick=_tick,
                scheduled_tick_interval_seconds=0.01,
            ),
            timeout=0.3,
        )

    try:
        asyncio.run(_run())
    except TimeoutError:
        pass

    assert len(ticks) >= 1


def test_run_queue_poller_swallows_scheduled_tick_errors(tmp_path) -> None:
    tg = _DummyTelegramClient()
    offset_store = OffsetStore(":memory:")
    database_url = f"sqlite:///{tmp_path / 'queue.db'}"
    init_db(database_url)
    tick_calls = 0

    async def _tick() -> None:
        nonlocal tick_calls
        tick_calls += 1
        raise RuntimeError("tick-failed")

    async def _run() -> None:
        await asyncio.wait_for(
            run_queue_poller(
                tg=tg,
                offset_store=offset_store,
                database_url=database_url,
                timeout_seconds=1,
                limit=1,
                retry_max_seconds=1,
                scheduled_tick=_tick,
                scheduled_tick_interval_seconds=0.01,
            ),
            timeout=0.3,
        )

    try:
        asyncio.run(_run())
    except TimeoutError:
        pass

    assert tick_calls >= 1


def test_run_queue_poller_saves_offset_after_enqueue(tmp_path) -> None:
    offset_store = OffsetStore(str(tmp_path / "offset.json"))
    database_url = f"sqlite:///{tmp_path / 'queue.db'}"
    init_db(database_url)

    async def _run() -> None:
        await asyncio.wait_for(
            run_queue_poller(
                tg=_SingleRestartUpdateTelegramClient(),
                offset_store=offset_store,
                database_url=database_url,
                timeout_seconds=1,
                limit=1,
                retry_max_seconds=1,
            ),
            timeout=0.2,
        )

    try:
        asyncio.run(_run())
    except TimeoutError:
        pass

    assert offset_store.load() == 268714202
    with create_session_factory(database_url)() as session:
        row = session.scalar(select(TelegramIncomingQueue))
        assert row is not None
        assert row.telegram_update_id == 268714202
