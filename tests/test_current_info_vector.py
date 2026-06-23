from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from amo_bot.current_info import (
    CurrentInfoDocumentCacheRepository,
    CurrentInfoRequest,
    CurrentInfoVectorIndexer,
    DbCurrentInfoRetrievalProvider,
    EvidenceChunk,
    FetchedDocument,
    PostgresVectorStore,
    VectorChunk,
    VectorCurrentInfoRetrievalProvider,
    VectorSearchResult,
)
from amo_bot.db.base import Base
from amo_bot.db.models import CurrentInfoDocument, CurrentInfoDocumentChunk


def _factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


class _FakeEmbeddingProvider:
    def __init__(self, vectors: tuple[tuple[float, ...], ...] = ((0.1, 0.2, 0.3),)) -> None:
        self.vectors = vectors
        self.calls: list[tuple[str, ...]] = []

    def embed_texts(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        self.calls.append(texts)
        if len(self.vectors) == len(texts):
            return self.vectors
        return tuple(self.vectors[0] for _ in texts)


class _FakeVectorStore:
    def __init__(self, search_results: tuple[VectorSearchResult, ...] = ()) -> None:
        self.search_results = search_results
        self.upserts: list[tuple[VectorChunk, ...]] = []
        self.searches: list[tuple[float, ...]] = []
        self.deleted_document_ids: list[tuple[int, ...]] = []

    def upsert_chunks(self, chunks: tuple[VectorChunk, ...]) -> None:
        self.upserts.append(chunks)

    def search(self, *, vector: tuple[float, ...], limit: int) -> tuple[VectorSearchResult, ...]:
        del limit
        self.searches.append(vector)
        return self.search_results

    def delete_document_ids(self, document_ids: tuple[int, ...]) -> None:
        self.deleted_document_ids.append(document_ids)


class _FailingVectorStore(_FakeVectorStore):
    def search(self, *, vector: tuple[float, ...], limit: int) -> tuple[VectorSearchResult, ...]:
        del vector, limit
        raise RuntimeError("vector store unavailable")


def test_vector_indexer_upserts_chunk_pointers_without_text_payload() -> None:
    factory = _factory()
    store = _FakeVectorStore()
    indexer = CurrentInfoVectorIndexer(vector_store=store, embedding_provider=_FakeEmbeddingProvider())
    now = datetime(2026, 6, 16, 10, 0, tzinfo=UTC)

    with factory() as session:
        repo = CurrentInfoDocumentCacheRepository(session, vector_indexer=indexer)
        row = repo.store_document(
            FetchedDocument(
                url="https://example.com/status",
                title="Status",
                text="Current public status text for semantic retrieval.",
                metadata={"source_type": "Official"},
            ),
            language="en",
            now=now,
        )
        session.commit()

        chunk = session.scalar(select(CurrentInfoDocumentChunk).where(CurrentInfoDocumentChunk.document_id == row.id))

    assert chunk is not None
    assert store.upserts
    vector_chunk = store.upserts[0][0]
    assert vector_chunk.point_id
    assert vector_chunk.point_id != str(chunk.id)
    assert vector_chunk.chunk_id == chunk.id
    assert vector_chunk.document_id == row.id
    assert vector_chunk.metadata["canonical_url"] == "https://example.com/status"
    assert "text" not in vector_chunk.metadata
    assert "text_excerpt" not in vector_chunk.metadata


def test_vector_retrieval_resolves_chunk_ids_through_database_rows() -> None:
    factory = _factory()
    now = datetime(2026, 6, 16, 10, 0, tzinfo=UTC)
    with factory() as session:
        row = CurrentInfoDocumentCacheRepository(session).store_document(
            FetchedDocument(
                url="https://example.com/semantic",
                title="Semantic result",
                text="Vector retrieval should return this stored database chunk.",
                metadata={"source_type": "Docs"},
            ),
            language="en",
            now=now,
        )
        row.chunks[0].expires_at = datetime(2026, 7, 16, 10, 0, tzinfo=UTC)
        chunk_id = int(row.chunks[0].id)
        session.commit()

    store = _FakeVectorStore((VectorSearchResult(chunk_id=chunk_id, score=0.87, metadata={}),))
    provider = VectorCurrentInfoRetrievalProvider(
        session_factory=factory,
        vector_store=store,
        embedding_provider=_FakeEmbeddingProvider(),
        fallback_provider=DbCurrentInfoRetrievalProvider(session_factory=factory),
    )

    chunks = provider.retrieve(request=CurrentInfoRequest(query="semantic query"), documents=(), search_results=())

    assert len(chunks) == 1
    assert chunks[0].text == "Vector retrieval should return this stored database chunk."
    assert chunks[0].source_url == "https://example.com/semantic"
    assert chunks[0].metadata["retrieval"] == "vector"
    assert chunks[0].metadata["pointer_status"] == "verified_db_pointer"
    assert store.searches == [(0.1, 0.2, 0.3)]


def test_vector_retrieval_ignores_hits_without_database_pointers(caplog) -> None:
    factory = _factory()
    store = _FakeVectorStore((VectorSearchResult(chunk_id=987654, score=0.99, metadata={"title": "orphan"}),))
    provider = VectorCurrentInfoRetrievalProvider(
        session_factory=factory,
        vector_store=store,
        embedding_provider=_FakeEmbeddingProvider(),
        fallback_provider=DbCurrentInfoRetrievalProvider(session_factory=factory),
    )

    chunks = provider.retrieve(
        request=CurrentInfoRequest(query="orphan vector memory"),
        documents=(),
        search_results=(),
    )

    assert chunks == ()
    assert "current_info_vector_unresolved_db_pointers: count=1" in caplog.text


def test_vector_retrieval_falls_back_to_keyword_when_vector_store_fails() -> None:
    factory = _factory()
    with factory() as session:
        CurrentInfoDocumentCacheRepository(session).store_document(
            FetchedDocument(
                url="https://example.com/fallback",
                title="Fallback status",
                text="Keyword fallback keeps retrieval working when vector search is down.",
                metadata={"source_type": "Official"},
            ),
            language="en",
        )
        session.commit()

    provider = VectorCurrentInfoRetrievalProvider(
        session_factory=factory,
        vector_store=_FailingVectorStore(),
        embedding_provider=_FakeEmbeddingProvider(),
        fallback_provider=DbCurrentInfoRetrievalProvider(session_factory=factory),
    )

    chunks = provider.retrieve(
        request=CurrentInfoRequest(query="fallback vector down"),
        documents=(),
        search_results=(),
    )

    assert chunks
    assert chunks[0].source_url == "https://example.com/fallback"
    assert chunks[0].metadata["cache"] == "current_info_documents"
    assert chunks[0].metadata.get("retrieval") != "vector"


def test_vector_prune_hook_deletes_points_by_document_id() -> None:
    factory = _factory()
    store = _FakeVectorStore()
    indexer = CurrentInfoVectorIndexer(vector_store=store, embedding_provider=_FakeEmbeddingProvider())
    now = datetime(2026, 6, 16, 10, 0, tzinfo=UTC)

    with factory() as session:
        repo = CurrentInfoDocumentCacheRepository(session, vector_indexer=indexer)
        row = repo.store_document(
            FetchedDocument(url="https://example.com/old", text="old text", metadata={"source_type": "Unknown"}),
            now=now,
        )
        row.last_seen_at = datetime(2026, 5, 1, 10, 0, tzinfo=UTC)
        row.expires_at = datetime(2026, 5, 1, 10, 0, tzinfo=UTC)
        document_id = int(row.id)
        session.commit()

        removed = repo.prune(now=now)
        session.commit()

        assert removed == 1
        assert session.scalar(select(CurrentInfoDocument)) is None
    assert store.deleted_document_ids == [(document_id,)]


def test_postgres_vector_store_builds_with_session_factory() -> None:
    factory = _factory()
    store = PostgresVectorStore(session_factory=factory)

    assert store is not None
