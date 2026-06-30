from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from amo_bot.current_info import vector as vector_module
from amo_bot.current_info import (
    CurrentInfoDocumentCacheRepository,
    CurrentInfoRequest,
    CurrentInfoVectorIndexer,
    DbCurrentInfoRetrievalProvider,
    EvidenceChunk,
    FetchedDocument,
    PostgresVectorStore,
    OllamaEmbeddingProvider,
    OpenAIEmbeddingProvider,
    VectorChunk,
    VectorCurrentInfoRetrievalProvider,
    VectorSearchResult,
    build_embedding_provider_from_settings,
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
        self.upsert_sessions: list[Session | None] = []
        self.searches: list[tuple[float, ...]] = []
        self.deleted_document_ids: list[tuple[int, ...]] = []

    def upsert_chunks(self, chunks: tuple[VectorChunk, ...], *, session: Session | None = None) -> None:
        self.upserts.append(chunks)
        self.upsert_sessions.append(session)

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


class _FailingUpsertVectorStore(_FakeVectorStore):
    def upsert_chunks(self, chunks: tuple[VectorChunk, ...], *, session: Session | None = None) -> None:
        del chunks, session
        raise RuntimeError("vector store unavailable")


class _FakeResponse:
    def __init__(self, *, status_code: int, payload: dict[str, object]) -> None:
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict[str, object]:
        return self._payload


class _FakeHttpxClient:
    calls: list[dict[str, object]] = []
    statuses: list[int] = [200]

    def __init__(self, *, timeout: float) -> None:
        self.timeout = timeout

    def __enter__(self) -> "_FakeHttpxClient":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def post(self, url: str, *, json: dict[str, object], headers: dict[str, str] | None = None) -> _FakeResponse:
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": self.timeout})
        status = self.statuses.pop(0) if self.statuses else 200
        if url.endswith("/api/embeddings"):
            return _FakeResponse(status_code=status, payload={"embedding": [0.3, 0.4]})
        return _FakeResponse(status_code=status, payload={"embeddings": [[0.1, 0.2]]})


def test_ollama_embedding_provider_sends_keep_alive_to_embed_endpoint(monkeypatch) -> None:
    _FakeHttpxClient.calls = []
    _FakeHttpxClient.statuses = [200]
    monkeypatch.setattr(vector_module.httpx, "Client", _FakeHttpxClient)

    provider = OllamaEmbeddingProvider(
        base_url="http://ollama.local:11434",
        model="nomic-embed",
        timeout_seconds=30.0,
        keep_alive="30m",
    )

    assert provider.embed_texts(("context text",)) == ((0.1, 0.2),)

    assert _FakeHttpxClient.calls == [
        {
            "url": "http://ollama.local:11434/api/embed",
            "json": {"model": "nomic-embed", "input": ["context text"], "keep_alive": "30m"},
            "headers": None,
            "timeout": 30.0,
        }
    ]


def test_ollama_embedding_provider_sends_keep_alive_to_legacy_endpoint(monkeypatch) -> None:
    _FakeHttpxClient.calls = []
    _FakeHttpxClient.statuses = [404, 200]
    monkeypatch.setattr(vector_module.httpx, "Client", _FakeHttpxClient)

    provider = OllamaEmbeddingProvider(
        base_url="http://ollama.local:11434/",
        model="nomic-embed",
        timeout_seconds=30.0,
        keep_alive="30m",
    )

    assert provider.embed_texts(("legacy text",)) == ((0.3, 0.4),)

    assert _FakeHttpxClient.calls[1] == {
        "url": "http://ollama.local:11434/api/embeddings",
        "json": {"model": "nomic-embed", "prompt": "legacy text", "keep_alive": "30m"},
        "headers": None,
        "timeout": 30.0,
    }


def test_embedding_provider_settings_apply_keep_alive_only_to_ollama() -> None:
    class _OllamaSettings:
        amo_vector_embedding_provider = "ollama"
        amo_vector_embedding_model = "nomic-embed"
        amo_vector_timeout_seconds = 45.0
        amo_vector_keep_alive = "1h"
        ollama_base_url = "http://ollama.local:11434"

    class _OpenAISettings:
        amo_vector_embedding_provider = "openai"
        amo_vector_embedding_model = "text-embedding-3-small"
        amo_vector_timeout_seconds = 45.0
        amo_vector_keep_alive = "1h"
        openai_api_key = "secret"

    ollama_provider = build_embedding_provider_from_settings(_OllamaSettings())
    openai_provider = build_embedding_provider_from_settings(_OpenAISettings())

    assert isinstance(ollama_provider, OllamaEmbeddingProvider)
    assert ollama_provider.timeout_seconds == 45.0
    assert ollama_provider.keep_alive == "1h"
    assert isinstance(openai_provider, OpenAIEmbeddingProvider)
    assert openai_provider.timeout_seconds == 45.0
    assert not hasattr(openai_provider, "keep_alive")


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
    assert store.upsert_sessions == [session]


def test_vector_indexer_forwards_active_session_to_vector_store() -> None:
    factory = _factory()
    store = _FakeVectorStore()
    indexer = CurrentInfoVectorIndexer(vector_store=store, embedding_provider=_FakeEmbeddingProvider())
    now = datetime(2026, 6, 16, 10, 0, tzinfo=UTC)

    with factory() as session:
        repo = CurrentInfoDocumentCacheRepository(session, vector_indexer=indexer)
        repo.store_document(
            FetchedDocument(
                url="https://example.com/session",
                title="Session",
                text="The vector store should receive the repository session.",
                metadata={"source_type": "Official"},
            ),
            language="en",
            now=now,
        )

        assert store.upsert_sessions == [session]


def test_vector_upsert_failure_rolls_back_document_cache_write() -> None:
    factory = _factory()
    indexer = CurrentInfoVectorIndexer(
        vector_store=_FailingUpsertVectorStore(),
        embedding_provider=_FakeEmbeddingProvider(),
    )

    with factory() as session:
        repo = CurrentInfoDocumentCacheRepository(session, vector_indexer=indexer)
        try:
            repo.store_document(
                FetchedDocument(
                    url="https://example.com/fail",
                    title="Fail",
                    text="This document must not commit without its vector rows.",
                    metadata={"source_type": "Official"},
                ),
                language="en",
            )
        except RuntimeError:
            session.rollback()
        else:  # pragma: no cover - defensive assertion shape
            raise AssertionError("expected vector upsert failure")

    with factory() as session:
        assert session.scalar(select(CurrentInfoDocument)) is None
        assert session.scalar(select(CurrentInfoDocumentChunk)) is None


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
