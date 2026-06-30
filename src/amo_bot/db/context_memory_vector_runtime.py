from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from amo_bot.db.context_memory_vector import ContextMemoryVectorRepository, EmbeddingProvider


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ContextMemoryVectorBackfillRuntime:
    repository: ContextMemoryVectorRepository
    embedding_provider: EmbeddingProvider
    enabled: bool = True
    interval_seconds: float = 120.0
    empty_interval_seconds: float = 300.0
    batch_size: int = 100
    warmup_on_startup: bool = False
    _task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    _stop_event: asyncio.Event | None = field(default=None, init=False, repr=False)
    _lock: asyncio.Lock = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.interval_seconds = max(30.0, float(self.interval_seconds))
        self.empty_interval_seconds = max(self.interval_seconds, float(self.empty_interval_seconds))
        self.batch_size = max(1, min(int(self.batch_size), 1000))
        self._lock = asyncio.Lock()

    def start(self) -> None:
        if not self.enabled or self._task is not None:
            return
        loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()
        self._task = loop.create_task(self._loop(), name="context-memory-vector-backfill")

    async def stop(self) -> None:
        if self._task is None:
            return
        if self._stop_event is not None:
            self._stop_event.set()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        self._stop_event = None

    async def run_once(self) -> int:
        async with self._lock:
            await self._warmup_embedding_provider()
            try:
                return int(
                    await asyncio.to_thread(
                        self.repository.index_pending,
                        embedding_provider=self.embedding_provider,
                        limit=self.batch_size,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("context_memory_vector_backfill_runtime_failed: %s", exc.__class__.__name__)
                return 0

    async def _warmup_embedding_provider(self) -> None:
        if not self.warmup_on_startup:
            return
        self.warmup_on_startup = False
        warmup = getattr(self.embedding_provider, "warmup", None)
        if not callable(warmup):
            return
        try:
            await asyncio.to_thread(warmup)
        except Exception as exc:  # noqa: BLE001
            logger.warning("context_memory_vector_warmup_failed: %s", exc.__class__.__name__)

    async def _loop(self) -> None:
        while self._stop_event is not None and not self._stop_event.is_set():
            indexed = await self.run_once()
            sleep_seconds = self.interval_seconds if indexed else self.empty_interval_seconds
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=sleep_seconds)
            except asyncio.TimeoutError:
                pass
