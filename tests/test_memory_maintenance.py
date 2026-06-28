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


def test_memory_maintenance_aggregates_recent_messages_per_scope_before_pruning() -> None:
    repo = _make_repo()
    service = MemoryMaintenanceService(repository=repo)

    cfg1 = repo.upsert_config(scope_type="topic", chat_id=-1001, topic_id=11, memory_retention_days=30)
    cfg2 = repo.upsert_config(scope_type="topic", chat_id=-1001, topic_id=12, memory_retention_days=30)

    repo.append_message(scope_type=cfg1.scope_type, chat_id=cfg1.chat_id, topic_id=cfg1.topic_id, user_id=cfg1.user_id, message_text="hello from topic 11", telegram_author_user_id=101, source="user", created_at=datetime(2026, 5, 13, 22, 40, tzinfo=UTC))
    repo.append_message(scope_type=cfg2.scope_type, chat_id=cfg2.chat_id, topic_id=cfg2.topic_id, user_id=cfg2.user_id, message_text="hello from topic 12", telegram_author_user_id=102, source="user", created_at=datetime(2026, 5, 13, 23, 10, tzinfo=UTC))

    run_at = datetime(2026, 5, 14, 2, 30, tzinfo=UTC)
    result = service.run_once(now=run_at)

    assert result.scopes_scanned == 2
    assert result.aggregation_scopes_attempted == 2
    assert result.aggregation_scopes_failed == 0
    assert result.recent_rows_seen == 2
    assert result.daily_rows_upserted == 2

    d1 = repo.get_daily_memory(scope_type="topic", chat_id=-1001, topic_id=11, user_id=None, memory_date="2026-05-13")
    d2 = repo.get_daily_memory(scope_type="topic", chat_id=-1001, topic_id=12, user_id=None, memory_date="2026-05-13")
    assert d1 is not None and d2 is not None
    assert "topic_id=11" in d1.summary_text
    assert "topic_id=12" in d2.summary_text


def test_memory_maintenance_aggregation_skip_when_no_recent_rows() -> None:
    repo = _make_repo()
    service = MemoryMaintenanceService(repository=repo)
    repo.upsert_config(scope_type="topic", chat_id=-2002, topic_id=33, memory_retention_days=30)

    run_at = datetime(2026, 5, 14, 2, 30, tzinfo=UTC)
    result = service.run_once(now=run_at)

    assert result.scopes_scanned == 1
    assert result.aggregation_scopes_attempted == 1
    assert result.recent_rows_seen == 0
    assert result.daily_rows_upserted == 0
    assert result.scopes_skipped_no_new_data == 1


def test_memory_maintenance_aggregation_runs_before_prune() -> None:
    repo = _make_repo()
    service = MemoryMaintenanceService(repository=repo)
    cfg = repo.upsert_config(scope_type="topic", chat_id=-3003, topic_id=44, memory_retention_days=1)

    repo.append_message(scope_type=cfg.scope_type, chat_id=cfg.chat_id, topic_id=cfg.topic_id, user_id=cfg.user_id, message_text="today row", telegram_author_user_id=7, source="user", created_at=datetime(2026, 5, 14, 0, 10, tzinfo=UTC))
    repo.upsert_daily_memory(scope_type=cfg.scope_type, chat_id=cfg.chat_id, topic_id=cfg.topic_id, user_id=cfg.user_id, memory_date="2026-05-12", summary_text="old", tokens_estimate=1)

    run_at = datetime(2026, 5, 14, 4, 0, tzinfo=UTC)
    result = service.run_once(now=run_at)

    assert result.daily_rows_upserted == 1
    assert result.deleted_daily_memories >= 1
    today_daily = repo.get_daily_memory(scope_type="topic", chat_id=cfg.chat_id, topic_id=cfg.topic_id, user_id=cfg.user_id, memory_date="2026-05-14")
    assert today_daily is not None


def test_aggregate_recent_messages_groups_by_created_at_day() -> None:
    repo = _make_repo()
    cfg = repo.upsert_config(scope_type="topic", chat_id=-4004, topic_id=77, memory_retention_days=30)

    repo.append_message(scope_type=cfg.scope_type, chat_id=cfg.chat_id, topic_id=cfg.topic_id, user_id=cfg.user_id, message_text="day one content", telegram_author_user_id=1, source="user", created_at=datetime(2026, 5, 13, 21, 30, tzinfo=UTC))
    repo.append_message(scope_type=cfg.scope_type, chat_id=cfg.chat_id, topic_id=cfg.topic_id, user_id=cfg.user_id, message_text="day two content", telegram_author_user_id=2, source="user", created_at=datetime(2026, 5, 14, 1, 30, tzinfo=UTC))

    result = repo.aggregate_recent_messages_to_daily_memory(
        scope_type=cfg.scope_type,
        chat_id=cfg.chat_id,
        topic_id=cfg.topic_id,
        user_id=cfg.user_id,
        now=datetime(2026, 5, 14, 2, 0, tzinfo=UTC),
    )

    assert result.recent_rows_seen == 2
    assert result.daily_rows_upserted == 2
    assert result.skipped_no_new_data is False
    assert repo.get_daily_memory(scope_type="topic", chat_id=cfg.chat_id, topic_id=cfg.topic_id, user_id=cfg.user_id, memory_date="2026-05-13") is not None
    assert repo.get_daily_memory(scope_type="topic", chat_id=cfg.chat_id, topic_id=cfg.topic_id, user_id=cfg.user_id, memory_date="2026-05-14") is not None


def test_aggregate_recent_messages_nightly_run_keeps_previous_day_memory_date() -> None:
    repo = _make_repo()
    cfg = repo.upsert_config(scope_type="topic", chat_id=-5005, topic_id=88, memory_retention_days=30)

    repo.append_message(scope_type=cfg.scope_type, chat_id=cfg.chat_id, topic_id=cfg.topic_id, user_id=cfg.user_id, message_text="late evening item", telegram_author_user_id=1, source="user", created_at=datetime(2026, 5, 13, 23, 58, tzinfo=UTC))

    result = repo.aggregate_recent_messages_to_daily_memory(
        scope_type=cfg.scope_type,
        chat_id=cfg.chat_id,
        topic_id=cfg.topic_id,
        user_id=cfg.user_id,
        now=datetime(2026, 5, 14, 2, 0, tzinfo=UTC),
    )

    assert result.recent_rows_seen == 1
    assert result.daily_rows_upserted == 1
    assert repo.get_daily_memory(scope_type="topic", chat_id=cfg.chat_id, topic_id=cfg.topic_id, user_id=cfg.user_id, memory_date="2026-05-13") is not None
    assert repo.get_daily_memory(scope_type="topic", chat_id=cfg.chat_id, topic_id=cfg.topic_id, user_id=cfg.user_id, memory_date="2026-05-14") is None


def test_aggregate_recent_messages_summary_has_sanitized_truncated_content() -> None:
    repo = _make_repo()
    cfg = repo.upsert_config(scope_type="topic", chat_id=-6006, topic_id=99, memory_retention_days=30)

    long_line = "X" * 260
    repo.append_message(scope_type=cfg.scope_type, chat_id=cfg.chat_id, topic_id=cfg.topic_id, user_id=cfg.user_id, message_text=f"  useful info one  ", telegram_author_user_id=1, source="user", created_at=datetime(2026, 5, 14, 0, 1, tzinfo=UTC))
    repo.append_message(scope_type=cfg.scope_type, chat_id=cfg.chat_id, topic_id=cfg.topic_id, user_id=cfg.user_id, message_text=long_line, telegram_author_user_id=2, source="user", created_at=datetime(2026, 5, 14, 0, 2, tzinfo=UTC))

    result = repo.aggregate_recent_messages_to_daily_memory(
        scope_type=cfg.scope_type,
        chat_id=cfg.chat_id,
        topic_id=cfg.topic_id,
        user_id=cfg.user_id,
        now=datetime(2026, 5, 14, 2, 0, tzinfo=UTC),
    )

    assert result.daily_rows_upserted == 1
    row = repo.get_daily_memory(scope_type=cfg.scope_type, chat_id=cfg.chat_id, topic_id=cfg.topic_id, user_id=cfg.user_id, memory_date="2026-05-14")
    assert row is not None
    assert "Content digest:" in row.summary_text
    assert "- useful info one" in row.summary_text
    assert ("- " + ("X" * 200) + "…") in row.summary_text
    assert long_line not in row.summary_text


def test_aggregate_recent_messages_excludes_bot_and_meta_rows_from_content_digest() -> None:
    repo = _make_repo()
    cfg = repo.upsert_config(scope_type="topic", chat_id=-6106, topic_id=100, memory_retention_days=30)
    ts = datetime(2026, 5, 14, 0, 1, tzinfo=UTC)

    repo.append_message(scope_type=cfg.scope_type, chat_id=cfg.chat_id, topic_id=cfg.topic_id, user_id=cfg.user_id, message_text="normal user asks about ChatGPT", telegram_author_user_id=1, source="user", created_at=ts)
    repo.append_message(scope_type=cfg.scope_type, chat_id=cfg.chat_id, topic_id=cfg.topic_id, user_id=cfg.user_id, message_text="ExampleTech bot answer should not be digested", telegram_author_user_id=2, source="assistant", telegram_author_is_bot=True, created_at=ts)
    repo.append_message(scope_type=cfg.scope_type, chat_id=cfg.chat_id, topic_id=cfg.topic_id, user_id=cfg.user_id, message_text="local commit 5fb83d9 fix: reduce off-topic memory recall drift", telegram_author_user_id=1, source="user", created_at=ts)

    result = repo.aggregate_recent_messages_to_daily_memory(scope_type=cfg.scope_type, chat_id=cfg.chat_id, topic_id=cfg.topic_id, user_id=cfg.user_id, now=ts)

    assert result.daily_rows_upserted == 1
    row = repo.get_daily_memory(scope_type=cfg.scope_type, chat_id=cfg.chat_id, topic_id=cfg.topic_id, user_id=cfg.user_id, memory_date="2026-05-14")
    assert row is not None
    assert "- normal user asks about ChatGPT" in row.summary_text
    assert "eligible_content_messages=1" in row.summary_text
    assert "ExampleTech" not in row.summary_text
    assert "local commit" not in row.summary_text


def test_aggregate_recent_messages_skips_daily_digest_when_no_eligible_content() -> None:
    repo = _make_repo()
    cfg = repo.upsert_config(scope_type="topic", chat_id=-6107, topic_id=101, memory_retention_days=30)
    ts = datetime(2026, 5, 14, 0, 1, tzinfo=UTC)

    repo.append_message(scope_type=cfg.scope_type, chat_id=cfg.chat_id, topic_id=cfg.topic_id, user_id=cfg.user_id, message_text="ExampleTech bot answer should not be digested", telegram_author_user_id=2, source="assistant", telegram_author_is_bot=True, created_at=ts)
    repo.append_message(scope_type=cfg.scope_type, chat_id=cfg.chat_id, topic_id=cfg.topic_id, user_id=cfg.user_id, message_text="pytest tests/test_ai_router.py -q PASS", telegram_author_user_id=1, source="user", created_at=ts)

    result = repo.aggregate_recent_messages_to_daily_memory(scope_type=cfg.scope_type, chat_id=cfg.chat_id, topic_id=cfg.topic_id, user_id=cfg.user_id, now=ts)

    assert result.recent_rows_seen == 2
    assert result.daily_rows_upserted == 0
    assert result.skipped_no_new_data is True
    assert repo.get_daily_memory(scope_type=cfg.scope_type, chat_id=cfg.chat_id, topic_id=cfg.topic_id, user_id=cfg.user_id, memory_date="2026-05-14") is None


def test_aggregate_recent_messages_multiple_topics_remain_separate() -> None:
    repo = _make_repo()
    cfg1 = repo.upsert_config(scope_type="topic", chat_id=-7007, topic_id=1, memory_retention_days=30)
    cfg2 = repo.upsert_config(scope_type="topic", chat_id=-7007, topic_id=2, memory_retention_days=30)

    ts = datetime(2026, 5, 14, 8, 0, tzinfo=UTC)
    repo.append_message(scope_type=cfg1.scope_type, chat_id=cfg1.chat_id, topic_id=cfg1.topic_id, user_id=cfg1.user_id, message_text="topic one only", telegram_author_user_id=1, source="user", created_at=ts)
    repo.append_message(scope_type=cfg2.scope_type, chat_id=cfg2.chat_id, topic_id=cfg2.topic_id, user_id=cfg2.user_id, message_text="topic two only", telegram_author_user_id=2, source="user", created_at=ts)

    r1 = repo.aggregate_recent_messages_to_daily_memory(scope_type=cfg1.scope_type, chat_id=cfg1.chat_id, topic_id=cfg1.topic_id, user_id=cfg1.user_id, now=ts)
    r2 = repo.aggregate_recent_messages_to_daily_memory(scope_type=cfg2.scope_type, chat_id=cfg2.chat_id, topic_id=cfg2.topic_id, user_id=cfg2.user_id, now=ts)

    assert r1.daily_rows_upserted == 1
    assert r2.daily_rows_upserted == 1

    d1 = repo.get_daily_memory(scope_type="topic", chat_id=-7007, topic_id=1, user_id=None, memory_date="2026-05-14")
    d2 = repo.get_daily_memory(scope_type="topic", chat_id=-7007, topic_id=2, user_id=None, memory_date="2026-05-14")
    assert d1 is not None and d2 is not None
    assert "topic one only" in d1.summary_text
    assert "topic two only" in d2.summary_text
    assert "topic two only" not in d1.summary_text
    assert "topic one only" not in d2.summary_text
