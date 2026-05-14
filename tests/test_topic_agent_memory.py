from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from amo_bot.db.init_db import init_db
from amo_bot.db.models import Base
from amo_bot.db.repositories import TopicAgentMemoryRepository


def test_init_db_is_idempotent_for_topic_agent_tables(tmp_path) -> None:
    db_path = tmp_path / "topic_agent.sqlite"
    db_url = f"sqlite:///{db_path}"

    init_db(db_url)
    init_db(db_url)

    engine = create_engine(db_url, future=True)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    expected = {
        "topic_agent_configs",
        "topic_daily_memories",
        "topic_long_memories",
        "topic_ai_sessions",
    }
    assert expected.issubset(tables)


def test_topic_memory_repository_daily_memory_retention_prune_boundary_and_idempotency() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    with Session(engine, future=True) as session:
        repo = TopicAgentMemoryRepository(session)

        repo.upsert_daily_memory(
            scope_type="topic",
            chat_id=-100123,
            topic_id=777,
            memory_date="2026-04-13",
            summary_text="older-than-30d",
            tokens_estimate=10,
        )
        repo.upsert_daily_memory(
            scope_type="topic",
            chat_id=-100123,
            topic_id=777,
            memory_date="2026-04-14",
            summary_text="exactly-30d-boundary",
            tokens_estimate=10,
        )
        repo.upsert_daily_memory(
            scope_type="topic",
            chat_id=-100123,
            topic_id=777,
            memory_date="2026-04-15",
            summary_text="inside-retention",
            tokens_estimate=10,
        )
        repo.upsert_daily_memory(
            scope_type="private_user",
            user_id=42,
            memory_date="2026-04-01",
            summary_text="private-must-stay",
            tokens_estimate=10,
        )

        deleted_first = repo.prune_daily_memories(
            scope_type="topic",
            chat_id=-100123,
            topic_id=777,
            retention_days=30,
            today=date(2026, 5, 14),
        )
        assert deleted_first == 1

        remaining_topic = repo.list_daily_memories(scope_type="topic", chat_id=-100123, topic_id=777, limit=10)
        assert [row.memory_date for row in remaining_topic] == ["2026-04-15", "2026-04-14"]

        private_row = repo.get_daily_memory(scope_type="private_user", user_id=42, memory_date="2026-04-01")
        assert private_row is not None

        deleted_second = repo.prune_daily_memories(
            scope_type="topic",
            chat_id=-100123,
            topic_id=777,
            retention_days=30,
            today=date(2026, 5, 14),
        )
        assert deleted_second == 0


def test_topic_memory_repository_config_daily_long_and_session_scopes() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    with Session(engine, future=True) as session:
        repo = TopicAgentMemoryRepository(session)

        topic_cfg = repo.upsert_config(scope_type="topic", chat_id=-100123, topic_id=777)
        assert topic_cfg.ai_enabled is False
        assert topic_cfg.response_mode == "command"
        assert topic_cfg.memory_retention_days == 30
        assert topic_cfg.tools_enabled is False
        assert topic_cfg.topic_soul_owner_only_edit is True

        private_cfg = repo.upsert_config(scope_type="private_user", user_id=42)
        assert private_cfg.ai_enabled is False
        assert private_cfg.response_mode == "command"

        updated_topic_cfg = repo.upsert_config(
            scope_type="topic",
            chat_id=-100123,
            topic_id=777,
            ai_enabled=True,
            response_mode="command",
            memory_retention_days=14,
            tools_enabled=False,
            topic_soul_text="topic soul",
            topic_soul_owner_only_edit=True,
        )
        assert updated_topic_cfg.ai_enabled is True
        assert updated_topic_cfg.memory_retention_days == 14
        assert updated_topic_cfg.topic_soul_text == "topic soul"

        fetched_topic_cfg = repo.get_config(scope_type="topic", chat_id=-100123, topic_id=777)
        assert fetched_topic_cfg is not None
        assert fetched_topic_cfg.ai_enabled is True

        d1 = repo.upsert_daily_memory(
            scope_type="topic",
            chat_id=-100123,
            topic_id=777,
            memory_date="2026-05-12",
            summary_text="s1",
            tokens_estimate=10,
        )
        d1b = repo.upsert_daily_memory(
            scope_type="topic",
            chat_id=-100123,
            topic_id=777,
            memory_date="2026-05-12",
            summary_text="s1-updated",
            tokens_estimate=12,
        )
        d2 = repo.upsert_daily_memory(
            scope_type="topic",
            chat_id=-100123,
            topic_id=777,
            memory_date="2026-05-13",
            summary_text="s2",
            tokens_estimate=15,
        )
        assert d1.memory_date == d1b.memory_date
        assert d1b.summary_text == "s1-updated"

        fetched_d1 = repo.get_daily_memory(
            scope_type="topic",
            chat_id=-100123,
            topic_id=777,
            memory_date="2026-05-12",
        )
        assert fetched_d1 is not None
        assert fetched_d1.summary_text == "s1-updated"

        listed = repo.list_daily_memories(scope_type="topic", chat_id=-100123, topic_id=777)
        assert [row.memory_date for row in listed] == [d2.memory_date, d1.memory_date]

        long_row = repo.create_long_memory(
            scope_type="topic",
            chat_id=-100123,
            topic_id=777,
            fact_text="important fact",
            source_daily_memory_id=1,
        )
        active = repo.list_long_memories(scope_type="topic", chat_id=-100123, topic_id=777, active_only=True)
        assert len(active) == 1
        assert active[0].id == long_row.id
        assert active[0].is_active is True
        assert active[0].promotion_status == "none"

        assert repo.deactivate_long_memory(memory_id=long_row.id) is True
        active_after = repo.list_long_memories(scope_type="topic", chat_id=-100123, topic_id=777, active_only=True)
        assert active_after == []
        all_rows = repo.list_long_memories(scope_type="topic", chat_id=-100123, topic_id=777, active_only=False)
        assert len(all_rows) == 1
        assert all_rows[0].is_active is False

        session_row = repo.upsert_ai_session(
            scope_type="private_user",
            user_id=42,
            session_payload={"context": ["hello"]},
            last_message_at=datetime(2026, 5, 13, tzinfo=timezone.utc),
        )
        assert session_row.session_payload == {"context": ["hello"]}

        fetched_session = repo.get_ai_session(scope_type="private_user", user_id=42)
        assert fetched_session is not None
        assert fetched_session.session_payload == {"context": ["hello"]}


def test_topic_long_memory_promotion_candidate_lifecycle_and_scope_isolation() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    with Session(engine, future=True) as session:
        repo = TopicAgentMemoryRepository(session)

        topic_a = repo.create_long_memory(
            scope_type="topic",
            chat_id=-1001,
            topic_id=11,
            fact_text="topic-a",
        )
        topic_b = repo.create_long_memory(
            scope_type="topic",
            chat_id=-1002,
            topic_id=22,
            fact_text="topic-b",
        )

        assert repo.mark_long_memory_candidate(memory_id=topic_a.id) is True

        listed_a = repo.list_long_memories(scope_type="topic", chat_id=-1001, topic_id=11, active_only=False)
        listed_b = repo.list_long_memories(scope_type="topic", chat_id=-1002, topic_id=22, active_only=False)
        assert len(listed_a) == 1
        assert listed_a[0].promotion_status == "candidate"
        assert len(listed_b) == 1
        assert listed_b[0].promotion_status == "none"

        assert repo.clear_long_memory_candidate(memory_id=topic_a.id) is True
        listed_a_after_clear = repo.list_long_memories(scope_type="topic", chat_id=-1001, topic_id=11, active_only=False)
        assert listed_a_after_clear[0].promotion_status == "none"

        assert repo.mark_long_memory_candidate(memory_id=topic_a.id) is True
        assert repo.deactivate_long_memory(memory_id=topic_a.id) is True
        listed_a_after_deactivate = repo.list_long_memories(scope_type="topic", chat_id=-1001, topic_id=11, active_only=False)
        assert listed_a_after_deactivate[0].is_active is False
        assert listed_a_after_deactivate[0].promotion_status == "none"

        assert repo.mark_long_memory_candidate(memory_id=topic_a.id) is False

        assert repo.mark_long_memory_candidate(memory_id=999999) is False
        assert repo.clear_long_memory_candidate(memory_id=999999) is False
