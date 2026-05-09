from __future__ import annotations

import asyncio

from amo_bot.telegram.polling import OffsetStore, run_polling


class _DummyTelegramClient:
    def __init__(self) -> None:
        self.calls = 0

    async def get_updates(self, *, offset: int, timeout: int, limit: int):
        self.calls += 1
        if self.calls >= 3:
            raise RuntimeError("stop-loop")
        return []


def test_run_polling_calls_scheduled_tick() -> None:
    tg = _DummyTelegramClient()
    offset_store = OffsetStore(":memory:")
    ticks: list[str] = []

    async def _tick() -> None:
        ticks.append("ok")

    async def _run() -> None:
        await asyncio.wait_for(
            run_polling(
                tg=tg,
                offset_store=offset_store,
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


def test_run_polling_swallows_scheduled_tick_errors() -> None:
    tg = _DummyTelegramClient()
    offset_store = OffsetStore(":memory:")
    tick_calls = 0

    async def _tick() -> None:
        nonlocal tick_calls
        tick_calls += 1
        raise RuntimeError("tick-failed")

    async def _run() -> None:
        await asyncio.wait_for(
            run_polling(
                tg=tg,
                offset_store=offset_store,
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
