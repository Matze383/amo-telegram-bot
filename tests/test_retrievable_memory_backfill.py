from __future__ import annotations

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from amo_bot.db.base import Base
from amo_bot.db.models import RetrievableMemory, TopicDailyMemory, TopicLongMemory, TopicRecentMessage
from amo_bot.db.repositories import RetrievableMemoryRepository, TopicAgentMemoryRepository
from amo_bot.db.retrievable_memory_backfill import check_schema_ready, format_result, main, run_backfill


def _factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


def test_backfill_creates_retrievable_rows_from_daily_summaries_with_scope() -> None:
    factory = _factory()
    with factory() as session:
        memory_repo = TopicAgentMemoryRepository(session)
        memory_repo.upsert_daily_memory(
            scope_type="topic",
            chat_id=-100,
            topic_id=7,
            memory_date="2026-06-01",
            summary_text="topic summarized memory",
            tokens_estimate=3,
        )
        memory_repo.upsert_daily_memory(
            scope_type="group_chat",
            chat_id=-200,
            memory_date="2026-06-01",
            summary_text="chat summarized memory",
            tokens_estimate=3,
        )
        memory_repo.upsert_daily_memory(
            scope_type="private_user",
            user_id=123,
            memory_date="2026-06-01",
            summary_text="user summarized memory",
            tokens_estimate=3,
        )

        result = RetrievableMemoryRepository(session).backfill_from_summarized_memories(dry_run=False, include_long=False)

        assert result.daily_memory.source_rows == 3
        assert result.daily_memory.created == 3
        rows = session.scalars(select(RetrievableMemory).order_by(RetrievableMemory.id.asc())).all()
        assert [(row.visibility, row.chat_id, row.message_thread_id, row.user_id) for row in rows] == [
            ("topic", -100, 7, None),
            ("chat", -200, None, None),
            ("user", None, None, 123),
        ]
        assert {row.source for row in rows} == {"daily_memory"}
        assert {row.memory_type for row in rows} == {"summary"}
        assert {bool(row.active) for row in rows} == {True}
        assert {round(float(row.confidence), 1) for row in rows} == {0.7}


def test_backfill_rerun_updates_existing_not_duplicate() -> None:
    factory = _factory()
    with factory() as session:
        memory_repo = TopicAgentMemoryRepository(session)
        memory_repo.upsert_daily_memory(
            scope_type="topic",
            chat_id=-100,
            topic_id=7,
            memory_date="2026-06-01",
            summary_text="before summary",
            tokens_estimate=2,
        )
        recall_repo = RetrievableMemoryRepository(session)
        first = recall_repo.backfill_from_summarized_memories(dry_run=False, include_long=False)
        daily = memory_repo.get_daily_memory(
            scope_type="topic", chat_id=-100, topic_id=7, memory_date="2026-06-01"
        )
        assert daily is not None
        memory_repo.upsert_daily_memory(
            scope_type="topic",
            chat_id=-100,
            topic_id=7,
            memory_date="2026-06-01",
            summary_text="after summary",
            tokens_estimate=2,
        )

        second = recall_repo.backfill_from_summarized_memories(dry_run=False, include_long=False)

        assert first.daily_memory.created == 1
        assert second.daily_memory.created == 0
        assert second.daily_memory.updated == 1
        rows = session.scalars(select(RetrievableMemory)).all()
        assert len(rows) == 1
        assert rows[0].summary == "after summary"


def test_dry_run_does_not_write(tmp_path) -> None:
    db_path = tmp_path / "backfill.sqlite"
    database_url = f"sqlite:///{db_path}"
    factory = sessionmaker(bind=create_engine(database_url, future=True), autoflush=False, expire_on_commit=False, class_=Session)
    Base.metadata.create_all(bind=factory.kw["bind"])
    with factory() as session:
        TopicAgentMemoryRepository(session).upsert_daily_memory(
            scope_type="topic",
            chat_id=-100,
            topic_id=7,
            memory_date="2026-06-01",
            summary_text="dry run summary",
            tokens_estimate=3,
        )

    result = run_backfill(database_url=database_url, dry_run=True)

    assert result.dry_run is True
    assert result.daily_memory.created == 1
    with factory() as session:
        assert session.scalar(select(func.count(RetrievableMemory.id))) == 0


def test_backfill_does_not_import_raw_recent_messages() -> None:
    factory = _factory()
    with factory() as session:
        memory_repo = TopicAgentMemoryRepository(session)
        memory_repo.upsert_daily_memory(
            scope_type="topic",
            chat_id=-100,
            topic_id=7,
            memory_date="2026-06-01",
            summary_text="safe summarized memory",
            tokens_estimate=3,
        )
        session.add(
            TopicRecentMessage(
                scope_type="topic",
                chat_id=-100,
                topic_id=7,
                user_id=123,
                message_text="raw recent message must not be imported",
            )
        )
        session.commit()

        RetrievableMemoryRepository(session).backfill_from_summarized_memories(dry_run=False)

        rows = session.scalars(select(RetrievableMemory)).all()
        assert len(rows) == 1
        assert rows[0].summary == "safe summarized memory"
        assert "raw recent" not in (rows[0].summary or "")


def test_long_memory_backfill_and_scope_isolation() -> None:
    factory = _factory()
    with factory() as session:
        memory_repo = TopicAgentMemoryRepository(session)
        memory_repo.create_long_memory(scope_type="topic", chat_id=-100, topic_id=7, fact_text="topic long memory")
        memory_repo.create_long_memory(scope_type="group_chat", chat_id=-100, fact_text="chat long memory")
        memory_repo.create_long_memory(scope_type="private_user", user_id=123, fact_text="user long memory")

        result = RetrievableMemoryRepository(session).backfill_from_summarized_memories(dry_run=False, include_daily=False)

        assert result.long_memory.created == 3
        topic_text = "\n".join(
            row.searchable_text
            for row in RetrievableMemoryRepository(session).recall_memories(
                query_text="memory", chat_id=-100, message_thread_id=7, user_id=123, limit=10
            )
        )
        assert "topic long memory" in topic_text
        assert "chat long memory" in topic_text
        assert "user long memory" not in topic_text

        user_text = "\n".join(
            row.searchable_text
            for row in RetrievableMemoryRepository(session).recall_memories(query_text="memory", user_id=123, limit=10)
        )
        assert "user long memory" in user_text
        assert "topic long memory" not in user_text


def test_format_result_is_metadata_only() -> None:
    factory = _factory()
    with factory() as session:
        TopicAgentMemoryRepository(session).upsert_daily_memory(
            scope_type="topic",
            chat_id=-100,
            topic_id=7,
            memory_date="2026-06-01",
            summary_text="secret summary text",
            tokens_estimate=3,
        )
        result = RetrievableMemoryRepository(session).backfill_from_summarized_memories(dry_run=True)

    output = format_result(result)
    assert "secret summary text" not in output
    assert "source=daily_memory" in output
    assert "created=1" in output


def test_schema_check_reports_missing_retrievable_memories_table(tmp_path) -> None:
    db_path = tmp_path / "missing_retrievable.sqlite"
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url, future=True)
    TopicDailyMemory.__table__.create(bind=engine)
    TopicLongMemory.__table__.create(bind=engine)

    status = check_schema_ready(database_url=database_url)

    assert status.schema_ready is False
    assert status.missing_tables == ("retrievable_memories",)


def test_dry_run_cli_missing_retrievable_memories_exits_zero_with_metadata_only(tmp_path, capsys) -> None:
    db_path = tmp_path / "dry_missing_retrievable.sqlite"
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url, future=True)
    TopicDailyMemory.__table__.create(bind=engine)
    TopicLongMemory.__table__.create(bind=engine)

    exit_code = main(["--database-url", database_url, "--dry-run"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "mode=dry_run" in output
    assert "schema_ready=false missing_tables=retrievable_memories" in output
    assert "Traceback" not in output


def test_apply_cli_missing_retrievable_memories_fails_safely(tmp_path, capsys) -> None:
    db_path = tmp_path / "apply_missing_retrievable.sqlite"
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url, future=True)
    TopicDailyMemory.__table__.create(bind=engine)
    TopicLongMemory.__table__.create(bind=engine)

    exit_code = main(["--database-url", database_url, "--apply"])

    output = capsys.readouterr().out
    assert exit_code == 2
    assert "mode=apply" in output
    assert "schema_ready=false missing_tables=retrievable_memories" in output
    assert "Traceback" not in output


def test_cli_missing_source_table_reports_safely(tmp_path, capsys) -> None:
    db_path = tmp_path / "missing_source.sqlite"
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url, future=True)
    RetrievableMemory.__table__.create(bind=engine)
    TopicDailyMemory.__table__.create(bind=engine)

    exit_code = main(["--database-url", database_url, "--dry-run"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "schema_ready=false missing_tables=topic_long_memories" in output
    assert "Traceback" not in output
