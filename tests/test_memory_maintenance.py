from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from amo_bot.ai.memory_maintenance import MemoryMaintenanceService
from amo_bot.db.init_db import init_db
from amo_bot.db.models import Base
from amo_bot.db.repositories import TopicAgentMemoryRepository


def _make_repo() -> TopicAgentMemoryRepository:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    init_db(engine.url.render_as_string(hide_password=False))
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = maker()
    assert isinstance(session, Session)
    return TopicAgentMemoryRepository(session)


def test_memory_maintenance_run_once_applies_retention_and_is_idempotent() -> None:
    repo = _make_repo()
    service = MemoryMaintenanceService(repository=repo)

    cfg = repo.upsert_config(
        scope_type="topic",
        chat_id=-100111,
        topic_id=9,
        memory_retention_days=30,
    )

    today = date(2026, 5, 14)
    old_day = today - timedelta(days=31)
    keep_day = today - timedelta(days=30)

    repo.upsert_daily_memory(
        scope_type=cfg.scope_type,
        chat_id=cfg.chat_id,
        topic_id=cfg.topic_id,
        user_id=cfg.user_id,
        memory_date=old_day.isoformat(),
        summary_text="drop",
        tokens_estimate=12,
    )
    repo.upsert_daily_memory(
        scope_type=cfg.scope_type,
        chat_id=cfg.chat_id,
        topic_id=cfg.topic_id,
        user_id=cfg.user_id,
        memory_date=keep_day.isoformat(),
        summary_text="keep",
        tokens_estimate=12,
    )

    run_at = datetime(2026, 5, 14, 4, 0, tzinfo=UTC)
    first = service.run_once(now=run_at)
    assert first.run_at == run_at
    assert first.scopes_scanned == 1
    assert first.scopes_pruned == 1
    assert first.deleted_daily_memories == 1

    remaining = repo.list_daily_memories(
        scope_type=cfg.scope_type,
        chat_id=cfg.chat_id,
        topic_id=cfg.topic_id,
        user_id=cfg.user_id,
        limit=10,
    )
    assert [r.memory_date for r in remaining] == [keep_day.isoformat()]

    second = service.run_once(now=run_at)
    assert second.scopes_scanned == 1
    assert second.scopes_pruned == 0
    assert second.deleted_daily_memories == 0


def test_memory_maintenance_run_once_reports_safe_zero_status_without_configs() -> None:
    repo = _make_repo()
    service = MemoryMaintenanceService(repository=repo)

    run_at = datetime(2026, 5, 14, 5, 0, tzinfo=UTC)
    result = service.run_once(now=run_at)

    assert result.run_at == run_at
    assert result.scopes_scanned == 0
    assert result.scopes_pruned == 0
    assert result.deleted_daily_memories == 0
