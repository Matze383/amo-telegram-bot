from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from amo_bot.db.context_memory_vector import (
    ALLOWED_VECTOR_SOURCES,
    ContextMemoryVectorRepository,
    ContextVectorSearchResult,
    VECTOR_SOURCE_RECENT,
    VECTOR_SOURCE_RETRIEVABLE,
)
from amo_bot.db.init_db import init_db
from amo_bot.db.repositories import RetrievableMemoryRepository, TopicAgentMemoryRepository


class _FakeEmbeddingProvider:
    def embed_texts(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple((float(index + 1), 0.25, 0.5) for index, _text in enumerate(texts))


class _FakeVectorRecall:
    def __init__(self, results: tuple[ContextVectorSearchResult, ...]) -> None:
        self.results = results
        self.calls: list[dict[str, object]] = []

    def search(self, **kwargs: object) -> tuple[ContextVectorSearchResult, ...]:
        self.calls.append(dict(kwargs))
        return self.results


def _factory(tmp_path: Path) -> sessionmaker[Session]:
    db_path = tmp_path / "context_vectors.sqlite"
    init_db(f"sqlite:///{db_path}")
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


def _row(session: Session, sql: str, **params: object):
    return session.execute(text(sql), params).mappings().first()


def test_context_vector_pending_rows_point_back_to_source_rows(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    vector_repo = ContextMemoryVectorRepository(session_factory=factory, embedding_model="test-embed")

    with factory() as session:
        repo = TopicAgentMemoryRepository(session, vector_repository=vector_repo)
        recent = repo.append_message(
            scope_type="topic",
            chat_id=-100,
            topic_id=11,
            message_text="espresso topic memory",
            telegram_message_id=501,
        )

        vector_row = _row(
            session,
            "SELECT source_table, source_id, scope_type, chat_id, topic_id, status "
            "FROM context_memory_vectors WHERE source_table = :source_table",
            source_table=VECTOR_SOURCE_RECENT,
        )

    assert vector_row is not None
    assert vector_row["source_table"] == "topic_recent_messages"
    assert vector_row["source_id"] == recent.id
    assert vector_row["scope_type"] == "topic"
    assert vector_row["chat_id"] == -100
    assert vector_row["topic_id"] == 11
    assert vector_row["status"] == "pending"


def test_same_group_different_topic_vector_hits_are_revalidated(tmp_path: Path) -> None:
    factory = _factory(tmp_path)

    with factory() as session:
        repo = TopicAgentMemoryRepository(session)
        topic_a = repo.append_message(scope_type="topic", chat_id=-100, topic_id=1, message_text="alpha espresso")
        topic_b = repo.append_message(scope_type="topic", chat_id=-100, topic_id=2, message_text="beta espresso")

    fake_recall = _FakeVectorRecall(
        (
            ContextVectorSearchResult(source_table=VECTOR_SOURCE_RECENT, source_id=topic_b.id, score=0.99, metadata={}),
            ContextVectorSearchResult(source_table=VECTOR_SOURCE_RECENT, source_id=topic_a.id, score=0.80, metadata={}),
        )
    )
    with factory() as session:
        repo = TopicAgentMemoryRepository(session, vector_recall=fake_recall)  # type: ignore[arg-type]
        rows = repo.recall_recent_messages(
            query_text="espresso",
            scope_type="topic",
            chat_id=-100,
            topic_id=1,
            limit=10,
        )

    assert [row.id for row in rows] == [topic_a.id]
    assert fake_recall.calls[0]["chat_id"] == -100
    assert fake_recall.calls[0]["topic_id"] == 1


def test_retrievable_memory_vector_recall_keeps_visibility_gate(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    now_results: list[int] = []
    with factory() as session:
        repo = RetrievableMemoryRepository(session)
        global_row = repo.create_memory(visibility="global", memory_type="fact", summary="global espresso")
        chat_row = repo.create_memory(visibility="chat", chat_id=-100, memory_type="fact", summary="chat espresso")
        topic_row = repo.create_memory(
            visibility="topic",
            chat_id=-100,
            message_thread_id=1,
            memory_type="fact",
            summary="topic espresso",
        )
        user_row = repo.create_memory(
            visibility="user",
            chat_id=-100,
            user_id=10,
            memory_type="fact",
            summary="user espresso",
        )
        wrong_topic = repo.create_memory(
            visibility="topic",
            chat_id=-100,
            message_thread_id=2,
            memory_type="fact",
            summary="wrong topic espresso",
        )
        wrong_user = repo.create_memory(
            visibility="user",
            chat_id=-100,
            user_id=20,
            memory_type="fact",
            summary="wrong user espresso",
        )
        now_results = [global_row.id, chat_row.id, topic_row.id, user_row.id, wrong_topic.id, wrong_user.id]

    fake_recall = _FakeVectorRecall(
        tuple(
            ContextVectorSearchResult(source_table=VECTOR_SOURCE_RETRIEVABLE, source_id=row_id, score=1.0, metadata={})
            for row_id in now_results
        )
    )
    with factory() as session:
        repo = RetrievableMemoryRepository(session, vector_recall=fake_recall)  # type: ignore[arg-type]
        records = repo.recall_memories(
            query_text="espresso",
            chat_id=-100,
            message_thread_id=1,
            user_id=10,
            limit=10,
        )

    assert [record.id for record in records] == now_results[:4]
    assert fake_recall.calls[0]["include_retrievable_visibility"] is True
    assert fake_recall.calls[0]["chat_id"] == -100
    assert fake_recall.calls[0]["topic_id"] == 1


def test_edited_recent_message_marks_existing_vector_pending_refresh(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    vector_repo = ContextMemoryVectorRepository(session_factory=factory, embedding_model="test-embed")

    with factory() as session:
        repo = TopicAgentMemoryRepository(session, vector_repository=vector_repo)
        recent = repo.append_message(
            scope_type="topic",
            chat_id=-100,
            topic_id=3,
            message_text="original text",
            telegram_message_id=700,
        )
        vector_repo.index_pending(embedding_provider=_FakeEmbeddingProvider())
        indexed = _row(
            session,
            "SELECT status, embedding, text_hash FROM context_memory_vectors WHERE source_id = :source_id",
            source_id=recent.id,
        )
        assert indexed is not None
        assert indexed["status"] == "indexed"
        assert indexed["embedding"] is not None

        updated = repo.update_recent_by_telegram_message_id(
            scope_type="topic",
            chat_id=-100,
            topic_id=3,
            telegram_message_id=700,
            message_text="edited text",
        )
        session.commit()
        refreshed = _row(
            session,
            "SELECT status, embedding, text_hash FROM context_memory_vectors WHERE source_id = :source_id",
            source_id=recent.id,
        )

    assert updated is not None
    assert refreshed is not None
    assert refreshed["status"] == "pending"
    assert refreshed["embedding"] is None
    assert refreshed["text_hash"] != indexed["text_hash"]


def test_operational_queue_tables_are_not_vector_sources() -> None:
    assert "telegram_incoming_queue" not in ALLOWED_VECTOR_SOURCES
    assert "telegram_outgoing_queue" not in ALLOWED_VECTOR_SOURCES
