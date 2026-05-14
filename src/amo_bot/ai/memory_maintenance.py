from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select

from amo_bot.db.models import TopicAgentConfig
from amo_bot.db.repositories import TopicAgentMemoryRepository


@dataclass(frozen=True, slots=True)
class MemoryMaintenanceResult:
    """Deterministic maintenance status for cron-safe audit logging."""

    run_at: datetime
    scopes_scanned: int
    scopes_pruned: int
    deleted_daily_memories: int


class MemoryMaintenanceService:
    """Retention-only maintenance entrypoint for topic/private memory scopes."""

    def __init__(self, *, repository: TopicAgentMemoryRepository) -> None:
        self._repository = repository

    def run_once(self, *, now: datetime | None = None) -> MemoryMaintenanceResult:
        run_at = now or datetime.now(UTC)
        scopes_scanned = 0
        scopes_pruned = 0
        deleted_daily_memories = 0

        scopes = self._repository._session.scalars(select(TopicAgentConfig)).all()  # noqa: SLF001
        for scope in scopes:
            scopes_scanned += 1
            deleted = self._repository.prune_daily_memories(
                scope_type=scope.scope_type,
                chat_id=scope.chat_id,
                topic_id=scope.topic_id,
                user_id=scope.user_id,
                retention_days=scope.memory_retention_days,
                today=run_at.date(),
            )
            if deleted > 0:
                scopes_pruned += 1
                deleted_daily_memories += deleted

        return MemoryMaintenanceResult(
            run_at=run_at,
            scopes_scanned=scopes_scanned,
            scopes_pruned=scopes_pruned,
            deleted_daily_memories=deleted_daily_memories,
        )
