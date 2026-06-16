from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from amo_bot.current_info import (
    CurrentInfoDocumentCacheRepository,
    CurrentInfoRequest,
    DbCurrentInfoRetrievalProvider,
    EvidenceChunk,
    FetchedDocument,
    HybridCurrentInfoRetrievalProvider,
    build_current_info_retrieval_provider_from_settings,
)
from amo_bot.current_info.ports import CurrentInfoRetrievalProvider
from amo_bot.db.base import Base


def _factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


class _Settings:
    amo_vector_enabled = False


class _FakeProvider:
    def __init__(self, chunks: tuple[EvidenceChunk, ...]) -> None:
        self.chunks = chunks

    def retrieve(
        self,
        *,
        request: CurrentInfoRequest,
        documents: tuple[FetchedDocument, ...],
        search_results: tuple[object, ...],
    ) -> tuple[EvidenceChunk, ...]:
        del request, documents, search_results
        return self.chunks


class _FailingProvider:
    def retrieve(
        self,
        *,
        request: CurrentInfoRequest,
        documents: tuple[FetchedDocument, ...],
        search_results: tuple[object, ...],
    ) -> tuple[EvidenceChunk, ...]:
        del request, documents, search_results
        raise RuntimeError("vector unavailable")


def _chunk(
    *,
    url: str,
    text: str,
    relevance: float,
    source_type: str = "Unknown",
    fetched_at: datetime | None = None,
    chunk_hash: str = "",
    host: str = "",
    language: str = "en",
    quality_score: float = 0.7,
) -> EvidenceChunk:
    fetched = fetched_at or datetime(2026, 6, 16, 10, 0, tzinfo=UTC)
    return EvidenceChunk(
        text=text,
        source_url=url,
        source_title=text[:30],
        relevance=relevance,
        metadata={
            "chunk_hash": chunk_hash,
            "host": host,
            "language": language,
            "source_type": source_type,
            "quality_score": quality_score,
            "source_timestamp": fetched.isoformat(),
            "fetched_at": fetched.isoformat(),
            "expires_at": (fetched + timedelta(days=7)).isoformat(),
        },
    )


def test_builder_returns_mariadb_keyword_provider_when_vector_disabled() -> None:
    provider = build_current_info_retrieval_provider_from_settings(_Settings(), session_factory=_factory())

    assert isinstance(provider, DbCurrentInfoRetrievalProvider)


def test_hybrid_fuses_keyword_and_vector_with_dedupe_and_official_recency_boost() -> None:
    now = datetime(2026, 6, 16, 10, 0, tzinfo=UTC)
    duplicate_keyword = _chunk(
        url="https://example.gov/status",
        text="Official status says service is green.",
        relevance=0.55,
        source_type="Official",
        fetched_at=now,
        chunk_hash="same",
        host="example.gov",
    )
    duplicate_vector = _chunk(
        url="https://example.gov/status?utm_source=ignored",
        text="Official status says service is green with semantic context.",
        relevance=0.91,
        source_type="Official",
        fetched_at=now,
        chunk_hash="same",
        host="example.gov",
    )
    weak_unknown = _chunk(
        url="https://blog.example/status",
        text="A weak mirrored status report.",
        relevance=0.9,
        source_type="Unknown",
        fetched_at=now - timedelta(days=30),
        host="blog.example",
        quality_score=0.2,
    )
    provider = HybridCurrentInfoRetrievalProvider(
        keyword_provider=_FakeProvider((duplicate_keyword, weak_unknown)),
        vector_provider=_FakeProvider((duplicate_vector,)),
    )

    chunks = provider.retrieve(
        request=CurrentInfoRequest(query="service green", metadata={"now": now.isoformat()}),
        documents=(),
        search_results=(),
    )

    assert len(chunks) == 2
    assert chunks[0].source_url == "https://example.gov/status"
    assert chunks[0].metadata["retrieval"] == "hybrid"
    assert chunks[0].metadata["hybrid_trace"]["keyword_rank"] == 1
    assert chunks[0].metadata["hybrid_trace"]["vector_rank"] == 1
    assert chunks[1].source_url == "https://blog.example/status"


def test_hybrid_semantic_results_supplement_weak_keyword_matches() -> None:
    provider = HybridCurrentInfoRetrievalProvider(
        keyword_provider=_FakeProvider(
            (
                _chunk(
                    url="https://example.com/keyword",
                    text="Keyword match is sparse.",
                    relevance=0.12,
                    host="example.com",
                ),
            )
        ),
        vector_provider=_FakeProvider(
            (
                _chunk(
                    url="https://docs.example.com/semantic",
                    text="Semantic search adds the relevant docs passage.",
                    relevance=0.88,
                    source_type="Docs",
                    host="docs.example.com",
                ),
            )
        ),
    )

    chunks = provider.retrieve(request=CurrentInfoRequest(query="semantic docs", max_results=3), documents=(), search_results=())

    assert {chunk.source_url for chunk in chunks} == {
        "https://example.com/keyword",
        "https://docs.example.com/semantic",
    }
    assert any(chunk.metadata["retrieval"] == "vector" for chunk in chunks)


def test_hybrid_falls_back_to_mariadb_keyword_when_vector_provider_fails() -> None:
    provider = HybridCurrentInfoRetrievalProvider(
        keyword_provider=_FakeProvider(
            (
                _chunk(
                    url="https://example.com/fallback",
                    text="Keyword fallback remains available.",
                    relevance=0.7,
                    host="example.com",
                ),
            )
        ),
        vector_provider=_FailingProvider(),
    )

    chunks = provider.retrieve(request=CurrentInfoRequest(query="fallback"), documents=(), search_results=())

    assert len(chunks) == 1
    assert chunks[0].source_url == "https://example.com/fallback"
    assert chunks[0].metadata["retrieval"] == "keyword"


def test_hybrid_applies_metadata_filters_to_output_candidates() -> None:
    provider = HybridCurrentInfoRetrievalProvider(
        keyword_provider=_FakeProvider(
            (
                _chunk(
                    url="https://example.com/de",
                    text="German official result.",
                    relevance=0.7,
                    source_type="Official",
                    host="example.com",
                    language="de",
                ),
                _chunk(
                    url="https://example.net/en",
                    text="English docs result.",
                    relevance=0.8,
                    source_type="Docs",
                    host="example.net",
                    language="en",
                ),
            )
        ),
        vector_provider=None,
    )

    chunks = provider.retrieve(
        request=CurrentInfoRequest(
            query="filtered",
            metadata={"filters": {"source_type": "Official", "language": "de", "host": "example.com"}},
        ),
        documents=(),
        search_results=(),
    )

    assert len(chunks) == 1
    assert chunks[0].source_url == "https://example.com/de"


def test_hybrid_with_real_mariadb_keyword_provider_returns_evidence_chunks() -> None:
    factory = _factory()
    with factory() as session:
        CurrentInfoDocumentCacheRepository(session).store_document(
            FetchedDocument(
                url="https://example.com/status",
                title="Status",
                text="Hybrid MariaDB keyword retrieval still returns cached evidence.",
                metadata={"source_type": "Official"},
            ),
            language="en",
        )
        session.commit()

    provider = HybridCurrentInfoRetrievalProvider(
        keyword_provider=DbCurrentInfoRetrievalProvider(session_factory=factory),
        vector_provider=None,
    )

    chunks = provider.retrieve(request=CurrentInfoRequest(query="Hybrid keyword evidence"), documents=(), search_results=())

    assert chunks
    assert chunks[0].source_url == "https://example.com/status"
    assert chunks[0].metadata["retrieval"] == "keyword"
