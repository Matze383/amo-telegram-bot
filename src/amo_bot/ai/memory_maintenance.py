from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from amo_bot.db.models import TopicAgentConfig
from amo_bot.db.repositories import TopicAgentConfigRecord, TopicAgentMemoryRepository, TopicDailyMemoryRecord


@dataclass(frozen=True, slots=True)
class MemoryMaintenanceResult:
    """Deterministic maintenance status for cron-safe audit logging."""

    run_at: datetime
    scopes_scanned: int
    scopes_pruned: int
    deleted_daily_memories: int
    aggregation_scopes_attempted: int
    recent_rows_seen: int
    daily_rows_upserted: int
    scopes_skipped_no_new_data: int
    aggregation_scopes_failed: int
    curation_scopes_attempted: int
    curation_candidates_considered: int
    curation_promoted: int
    curation_scopes_failed: int


logger = logging.getLogger(__name__)


class MemoryMaintenanceService:
    """Retention + bounded automatic long-memory curation for topic/private scopes."""

    _MAX_FACT_LEN = 280

    def __init__(
        self,
        *,
        repository: TopicAgentMemoryRepository,
        auto_curate_long_memory: bool = False,
        max_daily_candidates_per_scope: int = 3,
        max_promotions_per_scope: int = 2,
        curator: object | None = None,
    ) -> None:
        self._repository = repository
        self._auto_curate_long_memory = auto_curate_long_memory
        self._max_daily_candidates_per_scope = max(1, min(max_daily_candidates_per_scope, 30))
        self._max_promotions_per_scope = max(1, min(max_promotions_per_scope, 20))
        self._curator = curator or _DefaultCurator()

    def run_once(
        self,
        *,
        now: datetime | None = None,
        scopes: list[TopicAgentConfig] | None = None,
    ) -> MemoryMaintenanceResult:
        """Run one maintenance cycle.

        If ``scopes`` is provided, only those configs are processed (batch mode).
        If None, all configs from the DB are processed (legacy full-scan mode).
        """
        run_at = now or datetime.now(UTC)
        scopes_scanned = 0
        scopes_pruned = 0
        deleted_daily_memories = 0
        aggregation_scopes_attempted = 0
        recent_rows_seen = 0
        daily_rows_upserted = 0
        scopes_skipped_no_new_data = 0
        aggregation_scopes_failed = 0
        curation_scopes_attempted = 0
        curation_candidates_considered = 0
        curation_promoted = 0
        curation_scopes_failed = 0

        if scopes is None:
            scopes = self._repository._session.scalars(select(TopicAgentConfig)).all()  # noqa: SLF001

        for scope in scopes:
            scopes_scanned += 1

            aggregation_scopes_attempted += 1
            try:
                aggregation = self._repository.aggregate_recent_messages_to_daily_memory(
                    scope_type=scope.scope_type,
                    chat_id=scope.chat_id,
                    topic_id=scope.topic_id,
                    user_id=scope.user_id,
                    now=run_at,
                )
                recent_rows_seen += aggregation.recent_rows_seen
                daily_rows_upserted += aggregation.daily_rows_upserted
                if aggregation.skipped_no_new_data:
                    scopes_skipped_no_new_data += 1
            except Exception as exc:
                aggregation_scopes_failed += 1
                logger.warning(
                    "daily_memory_aggregation_failed",
                    extra={
                        "scope_type": scope.scope_type,
                        "chat_id": scope.chat_id,
                        "topic_id": scope.topic_id,
                        "user_id": scope.user_id,
                        "error_class": exc.__class__.__name__,
                    },
                )

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

            if self._auto_curate_long_memory:
                curation_scopes_attempted += 1
                considered, promoted, failed = self._curate_scope(scope=scope, run_at=run_at)
                curation_candidates_considered += considered
                curation_promoted += promoted
                curation_scopes_failed += 1 if failed else 0

        return MemoryMaintenanceResult(
            run_at=run_at,
            scopes_scanned=scopes_scanned,
            scopes_pruned=scopes_pruned,
            deleted_daily_memories=deleted_daily_memories,
            aggregation_scopes_attempted=aggregation_scopes_attempted,
            recent_rows_seen=recent_rows_seen,
            daily_rows_upserted=daily_rows_upserted,
            scopes_skipped_no_new_data=scopes_skipped_no_new_data,
            aggregation_scopes_failed=aggregation_scopes_failed,
            curation_scopes_attempted=curation_scopes_attempted,
            curation_candidates_considered=curation_candidates_considered,
            curation_promoted=curation_promoted,
            curation_scopes_failed=curation_scopes_failed,
        )

    def _curate_scope(self, *, scope: TopicAgentConfig, run_at: datetime) -> tuple[int, int, bool]:
        cfg = TopicAgentConfigRecord(
            scope_type=scope.scope_type,
            chat_id=scope.chat_id,
            topic_id=scope.topic_id,
            user_id=scope.user_id,
            ai_enabled=scope.ai_enabled,
            response_mode=scope.response_mode,
            memory_retention_days=scope.memory_retention_days,
            tools_enabled=scope.tools_enabled,
            main_soul_text=scope.main_soul_text,
            topic_soul_text=scope.topic_soul_text,
            topic_soul_owner_only_edit=scope.topic_soul_owner_only_edit,
            recent_context_window_size=max(0, min(int(getattr(scope, "recent_context_window_size", 0) or 0), 50)),
            image_analysis_mode=(getattr(scope, "image_analysis_mode", "inherit") or "inherit").strip().lower(),
        )
        rows = self._repository.list_daily_memories(
            scope_type=cfg.scope_type,
            chat_id=cfg.chat_id,
            topic_id=cfg.topic_id,
            user_id=cfg.user_id,
            limit=self._max_daily_candidates_per_scope,
        )
        if not rows:
            return 0, 0, False

        try:
            decisions = self._curator.curate(scope=cfg, daily_memories=rows, now=run_at)
        except Exception:
            return len(rows), 0, True

        promotions: list[tuple[int, str]] = []
        seen_daily_ids: set[int] = set()
        for decision in decisions:
            if len(promotions) >= self._max_promotions_per_scope:
                break
            if not isinstance(decision, dict):
                continue
            source_daily_memory_id = decision.get("source_daily_memory_id")
            fact_text_raw = decision.get("fact_text")
            if not isinstance(source_daily_memory_id, int) or source_daily_memory_id in seen_daily_ids:
                continue
            if not isinstance(fact_text_raw, str):
                continue
            fact_text = _sanitize_fact_text(fact_text_raw)
            if not fact_text:
                continue
            if not any(row.id == source_daily_memory_id for row in rows):
                continue
            seen_daily_ids.add(source_daily_memory_id)
            promotions.append((source_daily_memory_id, fact_text))

        if not promotions:
            return len(rows), 0, False

        session = self._repository._session  # noqa: SLF001
        savepoint = session.begin_nested()
        try:
            for source_daily_memory_id, fact_text in promotions:
                self._repository.create_long_memory(
                    scope_type=cfg.scope_type,
                    chat_id=cfg.chat_id,
                    topic_id=cfg.topic_id,
                    user_id=cfg.user_id,
                    fact_text=fact_text,
                    source_daily_memory_id=source_daily_memory_id,
                    promotion_status="candidate",
                    auto_commit=False,
                )
        except Exception:
            savepoint.rollback()
            return len(rows), 0, True
        savepoint.commit()

        return len(rows), len(promotions), False


class _DefaultCurator:
    def curate(
        self,
        *,
        scope: TopicAgentConfigRecord,
        daily_memories: list[TopicDailyMemoryRecord],
        now: datetime,
    ) -> list[dict[str, object]]:
        return []


_SENSITIVE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"system\W*prompt", re.IGNORECASE),
    re.compile(r"developer\W*prompt", re.IGNORECASE),
    re.compile(r"chain\W*of\W*thought", re.IGNORECASE),
    re.compile(r"internal", re.IGNORECASE),
    re.compile(r"secret", re.IGNORECASE),
    re.compile(r"api\W*key", re.IGNORECASE),
    re.compile(r"token", re.IGNORECASE),
)


def _normalize_guard_text(text: str) -> str:
    lowered = unicodedata.normalize("NFKC", text).casefold()
    return "".join(ch if ch.isalnum() else " " for ch in lowered)


def _sanitize_fact_text(text: str) -> str:
    value = " ".join(text.strip().split())
    if not value:
        return ""

    normalized = _normalize_guard_text(value)
    for pattern in _SENSITIVE_PATTERNS:
        if pattern.search(normalized):
            return ""

    return value[: MemoryMaintenanceService._MAX_FACT_LEN]
