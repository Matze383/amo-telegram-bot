from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from amo_bot.current_info import (
    CACHE_STATUS_EXPIRED_HIT,
    CACHE_STATUS_FETCH_ERROR,
    CACHE_STATUS_FRESH_HIT,
    CACHE_STATUS_MISS,
    CachedCurrentInfoFetchProvider,
    CurrentInfoCacheConfig,
    CurrentInfoDocumentCacheRepository,
    CurrentInfoRequest,
    DbCurrentInfoRetrievalProvider,
    FetchedDocument,
)
from amo_bot.db.base import Base
from amo_bot.db.models import (
    CurrentInfoDocument,
    CurrentInfoDocumentChunk,
    CurrentInfoFetchRun,
    CurrentInfoQueryRun,
)


def _factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


class _FakeFetchProvider:
    def __init__(self, documents: list[FetchedDocument | None]) -> None:
        self.documents = list(documents)
        self.calls: list[str] = []

    def fetch(self, *, url: str, locale: str) -> FetchedDocument | None:
        del locale
        self.calls.append(url)
        return self.documents.pop(0) if self.documents else None


class _FailingFetchProvider:
    def fetch(self, *, url: str, locale: str) -> FetchedDocument | None:
        del url, locale
        raise RuntimeError("upstream unavailable")


def test_document_cache_stores_canonical_document_chunks_and_fresh_lookup() -> None:
    factory = _factory()
    now = datetime(2026, 6, 16, 10, 0, tzinfo=UTC)
    document = FetchedDocument(
        url="https://Example.com/news/story?utm_source=test",
        title="Current story",
        text="Alpha status is green. Beta status is still pending.",
        fetched_at=now.isoformat(),
        status_code=200,
        provider="unit",
        metadata={"source_type": "News", "published_at": now.isoformat()},
    )

    with factory() as session:
        repo = CurrentInfoDocumentCacheRepository(
            session,
            config=CurrentInfoCacheConfig(realtime_ttl_seconds=900, max_chunk_chars=240),
        )
        row = repo.store_document(document, language="en", now=now)
        session.commit()

        lookup = repo.get_by_url("https://example.com/news/story?utm_campaign=ignored", now=now + timedelta(minutes=5))

        assert lookup.status == CACHE_STATUS_FRESH_HIT
        assert lookup.document is not None
        assert lookup.document.url == "https://example.com/news/story"
        assert row.content_hash
        assert session.scalar(select(CurrentInfoDocumentChunk.text_excerpt)).startswith("Alpha status")
        assert row.expires_at == now + timedelta(seconds=900)


def test_document_cache_distinguishes_fresh_expired_and_miss() -> None:
    factory = _factory()
    now = datetime(2026, 6, 16, 10, 0, tzinfo=UTC)
    document = FetchedDocument(
        url="https://example.com/docs/reference",
        title="Reference",
        text="A stable reference document with cacheable content.",
        metadata={"source_type": "Docs"},
    )

    with factory() as session:
        repo = CurrentInfoDocumentCacheRepository(
            session,
            config=CurrentInfoCacheConfig(docs_ttl_seconds=3600),
        )
        repo.store_document(document, language="en", now=now)
        session.commit()

        assert repo.get_by_url(document.url, now=now + timedelta(minutes=30)).status == CACHE_STATUS_FRESH_HIT
        assert repo.get_by_url(document.url, now=now + timedelta(hours=2)).status == CACHE_STATUS_EXPIRED_HIT
        assert repo.get_by_url("https://example.com/missing", now=now).status == CACHE_STATUS_MISS


def test_cached_fetch_provider_records_miss_then_reuses_fresh_hit() -> None:
    factory = _factory()
    fetch_provider = _FakeFetchProvider(
        [
            FetchedDocument(
                url="https://example.com/article",
                title="Article",
                text="Current cache body",
                provider="unit",
                metadata={"source_type": "Unknown"},
            )
        ]
    )
    provider = CachedCurrentInfoFetchProvider(
        session_factory=factory,
        fetch_provider=fetch_provider,
        config=CurrentInfoCacheConfig(unknown_ttl_seconds=3600),
    )

    first = provider.fetch(url="https://example.com/article", locale="en")
    second = provider.fetch(url="https://example.com/article", locale="en")

    assert first is not None
    assert second is not None
    assert fetch_provider.calls == ["https://example.com/article"]
    with factory() as session:
        statuses = list(session.scalars(select(CurrentInfoFetchRun.cache_status).order_by(CurrentInfoFetchRun.id)))
        assert statuses == [CACHE_STATUS_MISS, "stored", CACHE_STATUS_FRESH_HIT]


def test_cached_fetch_provider_records_fetch_error_without_swallowing_failure() -> None:
    factory = _factory()
    provider = CachedCurrentInfoFetchProvider(
        session_factory=factory,
        fetch_provider=_FailingFetchProvider(),
    )

    try:
        provider.fetch(url="https://example.com/failing", locale="en")
    except RuntimeError as exc:
        assert str(exc) == "upstream unavailable"
    else:
        raise AssertionError("fetch failure must be re-raised")

    with factory() as session:
        runs = list(session.scalars(select(CurrentInfoFetchRun).order_by(CurrentInfoFetchRun.id)))
        assert [run.cache_status for run in runs] == [CACHE_STATUS_MISS, CACHE_STATUS_FETCH_ERROR]
        assert runs[-1].status == "error"
        assert runs[-1].error_class == "RuntimeError"


def test_keyword_retrieval_uses_fallback_ranker_and_returns_evidence_metadata() -> None:
    factory = _factory()
    now = datetime(2026, 6, 16, 10, 0, tzinfo=UTC)
    with factory() as session:
        repo = CurrentInfoDocumentCacheRepository(session)
        repo.store_document(
            FetchedDocument(
                url="https://example.com/status",
                title="AMO service status",
                text="AMO service status is green and all jobs are healthy.",
                metadata={"source_type": "Official"},
            ),
            language="en",
            now=now,
        )
        repo.store_document(
            FetchedDocument(
                url="https://example.com/other",
                title="Other topic",
                text="This text discusses unrelated release notes.",
                metadata={"source_type": "Docs"},
            ),
            language="en",
            now=now,
        )
        session.commit()

        chunks = repo.retrieve_chunks(query_text="AMO status healthy", limit=2, now=now)

        assert len(chunks) == 1
        assert chunks[0].source_url == "https://example.com/status"
        assert chunks[0].metadata["source_type"] == "Official"
        assert chunks[0].metadata["cache"] == "current_info_documents"


def test_db_retrieval_provider_hashes_query_without_storing_raw_text() -> None:
    factory = _factory()
    request = CurrentInfoRequest(query="private user asks about secret roadmap", locale="en")
    with factory() as session:
        CurrentInfoDocumentCacheRepository(session).store_document(
            FetchedDocument(
                url="https://example.com/roadmap",
                title="Roadmap",
                text="The public roadmap mentions release windows.",
                metadata={"source_type": "Docs"},
            ),
            language="en",
        )
        session.commit()

    provider = DbCurrentInfoRetrievalProvider(session_factory=factory)
    chunks = provider.retrieve(request=request, documents=(), search_results=())

    assert chunks
    with factory() as session:
        run = session.scalar(select(CurrentInfoQueryRun))
        assert run is not None
        assert len(run.query_hash) == 64
        assert "secret roadmap" not in run.metadata_json


def test_prune_removes_old_documents_and_chunks_by_size_limit() -> None:
    factory = _factory()
    now = datetime(2026, 6, 16, 10, 0, tzinfo=UTC)
    with factory() as session:
        repo = CurrentInfoDocumentCacheRepository(
            session,
            config=CurrentInfoCacheConfig(max_documents=1, retention_days=30),
        )
        repo.store_document(
            FetchedDocument(url="https://example.com/old", text="old cache text", metadata={"source_type": "Unknown"}),
            now=now - timedelta(days=40),
        )
        repo.store_document(
            FetchedDocument(url="https://example.com/new", text="new cache text", metadata={"source_type": "Unknown"}),
            now=now,
        )
        session.commit()

        removed = repo.prune(now=now)
        session.commit()

        assert removed == 1
        assert session.scalar(select(CurrentInfoDocument.canonical_url)) == "https://example.com/new"
        assert session.scalar(select(CurrentInfoDocumentChunk.canonical_url)) == "https://example.com/new"
