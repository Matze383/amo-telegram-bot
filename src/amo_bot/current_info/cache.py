from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.orm import Session, sessionmaker

from amo_bot.current_info.candidates import (
    SOURCE_TYPE_DOCS,
    SOURCE_TYPE_NEWS,
    SOURCE_TYPE_OFFICIAL,
    SOURCE_TYPE_UNKNOWN,
    canonicalize_url,
)
from amo_bot.current_info.models import CurrentInfoRequest, EvidenceChunk, FetchedDocument, SearchResult
from amo_bot.current_info.ports import CurrentInfoFetchProvider
from amo_bot.db.models import (
    CurrentInfoDocument,
    CurrentInfoDocumentChunk,
    CurrentInfoFetchRun,
    CurrentInfoQueryRun,
)


CACHE_STATUS_FRESH_HIT = "fresh_hit"
CACHE_STATUS_EXPIRED_HIT = "expired_hit"
CACHE_STATUS_MISS = "miss"
CACHE_STATUS_STORED = "stored"
CACHE_STATUS_FETCH_EMPTY = "fetch_empty"
CACHE_STATUS_FETCH_ERROR = "fetch_error"


@dataclass(frozen=True, slots=True)
class CurrentInfoCacheConfig:
    realtime_ttl_seconds: int = 900
    docs_ttl_seconds: int = 604800
    general_ttl_seconds: int = 86400
    unknown_ttl_seconds: int = 3600
    max_documents: int = 5000
    retention_days: int = 30
    max_chunk_chars: int = 1200
    max_chunks_per_document: int = 12
    max_document_excerpt_chars: int = 4000


@dataclass(frozen=True, slots=True)
class CurrentInfoCacheLookup:
    document: FetchedDocument | None
    status: str
    document_id: int | None = None

    @property
    def fresh_hit(self) -> bool:
        return self.status == CACHE_STATUS_FRESH_HIT and self.document is not None


class CachedCurrentInfoFetchProvider:
    """Fetch-provider wrapper that stores public current-info documents in SQLAlchemy."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        fetch_provider: CurrentInfoFetchProvider,
        config: CurrentInfoCacheConfig | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._fetch_provider = fetch_provider
        self._config = config or CurrentInfoCacheConfig()

    def fetch(self, *, url: str, locale: str) -> FetchedDocument | None:
        with self._session_factory() as session:
            repo = CurrentInfoDocumentCacheRepository(session, config=self._config)
            lookup = repo.get_by_url(url, now=_utcnow())
            if lookup.fresh_hit:
                repo.record_fetch_run(
                    requested_url=url,
                    canonical_url=lookup.document.url if lookup.document else canonicalize_url(url),
                    cache_status=lookup.status,
                    document_id=lookup.document_id,
                )
                session.commit()
                return lookup.document

            repo.record_fetch_run(
                requested_url=url,
                canonical_url=lookup.document.url if lookup.document else canonicalize_url(url),
                cache_status=lookup.status,
                document_id=lookup.document_id,
            )
            try:
                document = self._fetch_provider.fetch(url=url, locale=locale)
            except Exception as exc:
                repo.record_fetch_run(
                    requested_url=url,
                    canonical_url=lookup.document.url if lookup.document else canonicalize_url(url),
                    cache_status=CACHE_STATUS_FETCH_ERROR,
                    document_id=lookup.document_id,
                    status="error",
                    error_class=exc.__class__.__name__,
                )
                session.commit()
                raise
            if document is None or not document.text.strip():
                repo.record_fetch_run(
                    requested_url=url,
                    canonical_url=lookup.document.url if lookup.document else canonicalize_url(url),
                    cache_status=CACHE_STATUS_FETCH_EMPTY if lookup.status == CACHE_STATUS_MISS else lookup.status,
                    document_id=lookup.document_id,
                    status="empty",
                )
                session.commit()
                return lookup.document if lookup.status == CACHE_STATUS_EXPIRED_HIT else None

            stored = repo.store_document(document, language=locale)
            repo.record_fetch_run(
                requested_url=url,
                canonical_url=document.url,
                cache_status=CACHE_STATUS_STORED,
                document_id=stored.id,
            )
            session.commit()
            return document


class CurrentInfoDocumentCacheRepository:
    def __init__(self, session: Session, *, config: CurrentInfoCacheConfig | None = None) -> None:
        self._session = session
        self._config = config or CurrentInfoCacheConfig()

    def get_by_url(self, url: str, *, now: datetime | None = None) -> CurrentInfoCacheLookup:
        current = now or _utcnow()
        canonical_url = canonicalize_url(url)
        url_hash = _sha256(canonical_url.casefold())
        row = self._session.scalar(
            select(CurrentInfoDocument)
            .where(CurrentInfoDocument.canonical_url_hash == url_hash)
            .order_by(CurrentInfoDocument.fetched_at.desc(), CurrentInfoDocument.id.desc())
            .limit(1)
        )
        if row is None:
            return CurrentInfoCacheLookup(document=None, status=CACHE_STATUS_MISS)

        row.last_seen_at = current
        status = CACHE_STATUS_FRESH_HIT if _as_aware_utc(row.expires_at) > current else CACHE_STATUS_EXPIRED_HIT
        return CurrentInfoCacheLookup(
            document=self._to_fetched_document(row),
            status=status,
            document_id=row.id,
        )

    def store_document(self, document: FetchedDocument, *, language: str = "", now: datetime | None = None) -> CurrentInfoDocument:
        current = now or _utcnow()
        canonical_url = canonicalize_url(document.url)
        url_hash = _sha256(canonical_url.casefold())
        text_body = " ".join(document.text.split())
        content_hash = _sha256(text_body)
        metadata = dict(document.metadata)
        source_type = str(metadata.get("source_type") or _source_type_for_url(canonical_url))
        quality_score = _quality_score(metadata)
        expires_at = current + self.ttl_for_source_type(source_type)
        published_at = _parse_datetime(metadata.get("published_at"))
        modified_at = _parse_datetime(metadata.get("modified_at"))

        existing = self._session.scalar(
            select(CurrentInfoDocument).where(
                CurrentInfoDocument.canonical_url_hash == url_hash,
                CurrentInfoDocument.content_hash == content_hash,
            )
        )
        if existing is None:
            row = CurrentInfoDocument(
                canonical_url=canonical_url,
                canonical_url_hash=url_hash,
                source_url=str(metadata.get("final_url") or canonical_url),
                host=_host(canonical_url),
                title=(document.title or "")[:512],
                language=(language or "")[:16],
                source_type=source_type[:32],
                content_hash=content_hash,
                text_excerpt=text_body[: self._config.max_document_excerpt_chars],
                quality_score=quality_score,
                status_code=document.status_code,
                provider=(document.provider or "")[:64],
                metadata_json=_json_dumps(metadata),
                published_at=published_at,
                modified_at=modified_at,
                fetched_at=current,
                expires_at=expires_at,
                last_seen_at=current,
            )
            self._session.add(row)
            self._session.flush()
        else:
            row = existing
            row.source_url = str(metadata.get("final_url") or row.source_url or canonical_url)
            row.title = (document.title or row.title or "")[:512]
            row.language = (language or row.language or "")[:16]
            row.source_type = source_type[:32]
            row.text_excerpt = text_body[: self._config.max_document_excerpt_chars]
            row.quality_score = quality_score
            row.status_code = document.status_code
            row.provider = (document.provider or row.provider or "")[:64]
            row.metadata_json = _json_dumps(metadata)
            row.published_at = published_at
            row.modified_at = modified_at
            row.fetched_at = current
            row.expires_at = expires_at
            row.last_seen_at = current
            row.chunks.clear()
            self._session.flush()

        for index, chunk_text in enumerate(self._chunk_text(text_body)):
            row.chunks.append(
                CurrentInfoDocumentChunk(
                    document_id=row.id,
                    chunk_index=index,
                    canonical_url=canonical_url,
                    canonical_url_hash=url_hash,
                    host=row.host,
                    title=row.title,
                    language=row.language,
                    source_type=row.source_type,
                    text_excerpt=chunk_text,
                    chunk_hash=_sha256(chunk_text),
                    quality_score=row.quality_score,
                    metadata_json=_json_dumps({"document_id": row.id, "chunk_index": index}),
                    source_timestamp=modified_at or published_at,
                    fetched_at=current,
                    expires_at=expires_at,
                )
            )
        self._session.flush()
        return row

    def retrieve_chunks(
        self,
        *,
        query_text: str,
        limit: int = 5,
        now: datetime | None = None,
        include_expired: bool = False,
    ) -> list[EvidenceChunk]:
        current = now or _utcnow()
        safe_limit = max(1, min(int(limit or 5), 20))
        tokens = _tokens(query_text)
        query = select(CurrentInfoDocumentChunk)
        if not include_expired:
            query = query.where(CurrentInfoDocumentChunk.expires_at > current)

        if tokens and self._is_mysql_backend():
            try:
                terms = " ".join(sorted(tokens))
                ids = [
                    int(row_id)
                    for row_id in self._session.execute(
                        text(
                            "SELECT id FROM current_info_document_chunks "
                            f"WHERE {'expires_at > :now AND ' if not include_expired else ''}"
                            "MATCH(title, text_excerpt) AGAINST (:terms IN NATURAL LANGUAGE MODE) "
                            "ORDER BY MATCH(title, text_excerpt) AGAINST (:terms IN NATURAL LANGUAGE MODE) DESC "
                            "LIMIT :limit"
                        ),
                        {"now": current, "terms": terms, "limit": safe_limit * 5},
                    ).scalars()
                ]
                query = query.where(CurrentInfoDocumentChunk.id.in_(ids or [-1]))
            except Exception:
                pass

        rows = list(self._session.scalars(query.order_by(CurrentInfoDocumentChunk.fetched_at.desc()).limit(200)).all())
        scored = [(self._score_chunk(row, tokens=tokens, now=current), row) for row in rows]
        if tokens:
            scored = [item for item in scored if item[0] > 0]
        scored.sort(key=lambda item: (item[0], item[1].quality_score, item[1].id), reverse=True)
        chunks: list[EvidenceChunk] = []
        for score, row in scored[:safe_limit]:
            metadata = _json_loads(row.metadata_json)
            metadata.update(
                {
                    "source_type": row.source_type,
                    "source_timestamp": _iso(row.source_timestamp or row.fetched_at),
                    "fetched_at": _iso(row.fetched_at),
                    "expires_at": _iso(row.expires_at),
                    "cache": "current_info_documents",
                }
            )
            chunks.append(
                EvidenceChunk(
                    text=row.text_excerpt,
                    source_url=row.canonical_url,
                    source_title=row.title,
                    relevance=round(score, 6),
                    metadata=metadata,
                )
            )
        return chunks

    def record_fetch_run(
        self,
        *,
        requested_url: str,
        canonical_url: str,
        cache_status: str,
        document_id: int | None = None,
        status: str = "ok",
        error_class: str = "",
    ) -> None:
        canonical = canonicalize_url(canonical_url or requested_url)
        self._session.add(
            CurrentInfoFetchRun(
                requested_url=requested_url,
                canonical_url=canonical,
                canonical_url_hash=_sha256(canonical.casefold()) if canonical else "",
                source_type=_source_type_for_url(canonical),
                cache_status=cache_status,
                status=status,
                document_id=document_id,
                error_class=error_class[:128],
            )
        )

    def record_query_run(
        self,
        *,
        request: CurrentInfoRequest,
        result_count: int,
        cache_hit_count: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._session.add(
            CurrentInfoQueryRun(
                query_hash=_sha256(" ".join(request.query.split()).casefold()),
                locale=(request.locale or "")[:16],
                domain_hint=(request.domain_hint or "")[:64],
                result_count=max(0, int(result_count)),
                cache_hit_count=max(0, int(cache_hit_count)),
                metadata_json=_json_dumps(metadata or {}),
            )
        )

    def prune(self, *, now: datetime | None = None) -> int:
        current = now or _utcnow()
        cutoff = current - timedelta(days=max(1, int(self._config.retention_days)))
        expired_ids = [
            int(row_id)
            for row_id in self._session.scalars(
                select(CurrentInfoDocument.id).where(
                    or_(CurrentInfoDocument.expires_at < cutoff, CurrentInfoDocument.last_seen_at < cutoff)
                )
            )
        ]
        document_count = int(self._session.scalar(select(func.count()).select_from(CurrentInfoDocument)) or 0)
        overflow = max(0, document_count - max(1, int(self._config.max_documents)))
        if overflow:
            old_ids = [
                int(row_id)
                for row_id in self._session.scalars(
                    select(CurrentInfoDocument.id)
                    .order_by(CurrentInfoDocument.last_seen_at.asc(), CurrentInfoDocument.id.asc())
                    .limit(overflow)
                )
            ]
            expired_ids.extend(old_ids)

        unique_ids = tuple(dict.fromkeys(expired_ids))
        if not unique_ids:
            return 0
        self._session.execute(delete(CurrentInfoDocumentChunk).where(CurrentInfoDocumentChunk.document_id.in_(unique_ids)))
        self._session.execute(delete(CurrentInfoDocument).where(CurrentInfoDocument.id.in_(unique_ids)))
        return len(unique_ids)

    def ttl_for_source_type(self, source_type: str) -> timedelta:
        normalized = (source_type or SOURCE_TYPE_UNKNOWN).strip()
        if normalized == SOURCE_TYPE_NEWS:
            return timedelta(seconds=max(60, int(self._config.realtime_ttl_seconds)))
        if normalized in {SOURCE_TYPE_DOCS, SOURCE_TYPE_OFFICIAL}:
            return timedelta(seconds=max(3600, int(self._config.docs_ttl_seconds)))
        if normalized == SOURCE_TYPE_UNKNOWN:
            return timedelta(seconds=max(300, int(self._config.unknown_ttl_seconds)))
        return timedelta(seconds=max(300, int(self._config.general_ttl_seconds)))

    def _to_fetched_document(self, row: CurrentInfoDocument) -> FetchedDocument:
        metadata = _json_loads(row.metadata_json)
        metadata.update(
            {
                "canonical_url": row.canonical_url,
                "source_type": row.source_type,
                "cache_status": "hit",
                "expires_at": _iso(row.expires_at),
            }
        )
        return FetchedDocument(
            url=row.canonical_url,
            text=row.text_excerpt,
            title=row.title,
            fetched_at=_iso(row.fetched_at),
            status_code=row.status_code,
            provider=row.provider,
            metadata=metadata,
        )

    def _chunk_text(self, text_body: str) -> list[str]:
        max_chars = max(200, int(self._config.max_chunk_chars))
        max_chunks = max(1, int(self._config.max_chunks_per_document))
        paragraphs = [item.strip() for item in re.split(r"\n{2,}|(?<=[.!?])\s+", text_body) if item.strip()]
        chunks: list[str] = []
        current = ""
        for paragraph in paragraphs or [text_body]:
            if current and len(current) + len(paragraph) + 1 > max_chars:
                chunks.append(current)
                current = ""
                if len(chunks) >= max_chunks:
                    break
            if len(paragraph) > max_chars:
                chunks.append(paragraph[:max_chars])
                if len(chunks) >= max_chunks:
                    break
                continue
            current = f"{current} {paragraph}".strip()
        if current and len(chunks) < max_chunks:
            chunks.append(current)
        return chunks or [text_body[:max_chars]]

    def _score_chunk(self, row: CurrentInfoDocumentChunk, *, tokens: set[str], now: datetime) -> float:
        text_tokens = _tokens(f"{row.title} {row.text_excerpt}")
        textual = 0.2
        if tokens:
            overlap = len(tokens & text_tokens)
            if overlap == 0:
                return 0.0
            textual = overlap / max(len(tokens), 1)
        age_days = max(0.0, (now - _as_aware_utc(row.fetched_at)).total_seconds() / 86400)
        recency = 1.0 / (1.0 + min(age_days, 365.0) / 14.0)
        return (textual * 0.70) + (float(row.quality_score or 0.0) * 0.20) + (recency * 0.10)

    def _is_mysql_backend(self) -> bool:
        bind = self._session.get_bind()
        return bind.dialect.name in {"mysql", "mariadb"}


class DbCurrentInfoRetrievalProvider:
    def __init__(self, *, session_factory: sessionmaker[Session], config: CurrentInfoCacheConfig | None = None) -> None:
        self._session_factory = session_factory
        self._config = config or CurrentInfoCacheConfig()

    def retrieve(
        self,
        *,
        request: CurrentInfoRequest,
        documents: tuple[FetchedDocument, ...],
        search_results: tuple[SearchResult, ...],
    ) -> tuple[EvidenceChunk, ...]:
        with self._session_factory() as session:
            repo = CurrentInfoDocumentCacheRepository(session, config=self._config)
            chunks = repo.retrieve_chunks(query_text=request.query, limit=max(request.max_results, 1))
            repo.record_query_run(
                request=request,
                result_count=len(chunks),
                cache_hit_count=len(chunks),
                metadata={"search_result_count": len(search_results), "document_count": len(documents)},
            )
            session.commit()
            return tuple(chunks)


def build_current_info_cache_config_from_settings(settings: Any) -> CurrentInfoCacheConfig:
    return CurrentInfoCacheConfig(
        realtime_ttl_seconds=int(getattr(settings, "amo_current_info_cache_realtime_ttl_seconds", 900)),
        docs_ttl_seconds=int(getattr(settings, "amo_current_info_cache_docs_ttl_seconds", 604800)),
        general_ttl_seconds=int(getattr(settings, "amo_current_info_cache_general_ttl_seconds", 86400)),
        unknown_ttl_seconds=int(getattr(settings, "amo_current_info_cache_unknown_ttl_seconds", 3600)),
        max_documents=int(getattr(settings, "amo_current_info_cache_max_documents", 5000)),
        retention_days=int(getattr(settings, "amo_current_info_cache_retention_days", 30)),
        max_chunk_chars=int(getattr(settings, "amo_current_info_cache_max_chunk_chars", 1200)),
        max_chunks_per_document=int(getattr(settings, "amo_current_info_cache_max_chunks_per_document", 12)),
    )


def build_cached_fetch_provider_from_settings(
    settings: Any,
    *,
    session_factory: sessionmaker[Session],
    fetch_provider: CurrentInfoFetchProvider,
) -> CachedCurrentInfoFetchProvider:
    return CachedCurrentInfoFetchProvider(
        session_factory=session_factory,
        fetch_provider=fetch_provider,
        config=build_current_info_cache_config_from_settings(settings),
    )


def _quality_score(metadata: dict[str, Any]) -> float:
    raw_quality = metadata.get("extraction_quality")
    if isinstance(raw_quality, dict):
        label = str(raw_quality.get("quality") or raw_quality.get("label") or "").casefold()
        if "high" in label:
            return 1.0
        if "medium" in label or "ok" in label:
            return 0.7
        if "low" in label:
            return 0.35
        text_length = raw_quality.get("text_length")
        try:
            return min(max(float(text_length or 0) / 4000.0, 0.1), 1.0)
        except (TypeError, ValueError):
            return 0.5
    return 0.5


def _source_type_for_url(url: str) -> str:
    host = _host(url)
    path = urlparse(url).path.lower()
    if host.endswith(".gov") or host.endswith(".mil") or host.endswith(".int") or "europa.eu" in host:
        return SOURCE_TYPE_OFFICIAL
    if host.startswith("docs.") or any(marker in path for marker in ("/docs", "/documentation", "/api/", "/reference")):
        return SOURCE_TYPE_DOCS
    if "/news" in path or "/article" in path:
        return SOURCE_TYPE_NEWS
    return SOURCE_TYPE_UNKNOWN


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")[:255]


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _tokens(value: str | None) -> set[str]:
    return {token.casefold() for token in re.findall(r"[A-Za-z0-9ÄÖÜäöüß_+-]+", value or "") if len(token) >= 3}


def _json_dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _json_loads(value: str | None) -> dict[str, Any]:
    try:
        payload = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _as_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str:
    return _as_aware_utc(value).isoformat() if value is not None else ""


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    return _as_aware_utc(parsed)
