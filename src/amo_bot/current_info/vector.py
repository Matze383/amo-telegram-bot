from __future__ import annotations

import logging
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timezone
from typing import Any, Protocol

import httpx
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from amo_bot.current_info.models import CurrentInfoRequest, EvidenceChunk, FetchedDocument, SearchResult
from amo_bot.current_info.ports import CurrentInfoRetrievalProvider
from amo_bot.db.models import CurrentInfoDocumentChunk


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class VectorSearchResult:
    chunk_id: int
    score: float
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class VectorChunk:
    point_id: str
    chunk_id: int
    document_id: int
    chunk_index: int
    vector: tuple[float, ...]
    metadata: dict[str, Any]


class EmbeddingProvider(Protocol):
    def embed_texts(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        ...


class VectorStore(Protocol):
    def upsert_chunks(self, chunks: tuple[VectorChunk, ...]) -> None:
        ...

    def search(self, *, vector: tuple[float, ...], limit: int) -> tuple[VectorSearchResult, ...]:
        ...

    def delete_document_ids(self, document_ids: tuple[int, ...]) -> None:
        ...


@dataclass(frozen=True, slots=True)
class QdrantVectorStoreConfig:
    url: str
    collection: str = "current_info_chunks"
    api_key: str | None = None
    timeout_seconds: float = 3.0


class QdrantVectorStore:
    def __init__(self, config: QdrantVectorStoreConfig) -> None:
        self._config = config
        self._collection_ready_dimension: int | None = None

    def upsert_chunks(self, chunks: tuple[VectorChunk, ...]) -> None:
        if not chunks:
            return
        dimension = len(chunks[0].vector)
        if dimension <= 0:
            raise ValueError("vector dimension must be > 0")
        self._ensure_collection(dimension)
        points = [
            {
                "id": chunk.point_id,
                "vector": list(chunk.vector),
                "payload": {
                    "document_id": chunk.document_id,
                    "chunk_id": chunk.chunk_id,
                    "chunk_index": chunk.chunk_index,
                    **chunk.metadata,
                },
            }
            for chunk in chunks
        ]
        self._request("PUT", f"/collections/{self._config.collection}/points", json={"points": points})

    def search(self, *, vector: tuple[float, ...], limit: int) -> tuple[VectorSearchResult, ...]:
        response = self._request(
            "POST",
            f"/collections/{self._config.collection}/points/search",
            json={"vector": list(vector), "limit": max(1, int(limit)), "with_payload": True},
        )
        results = response.get("result")
        if not isinstance(results, list):
            return ()
        parsed: list[VectorSearchResult] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
            raw_chunk_id = payload.get("chunk_id", item.get("id"))
            try:
                chunk_id = int(raw_chunk_id)
                score = float(item.get("score") or 0.0)
            except (TypeError, ValueError):
                continue
            parsed.append(VectorSearchResult(chunk_id=chunk_id, score=score, metadata=dict(payload)))
        return tuple(parsed)

    def delete_document_ids(self, document_ids: tuple[int, ...]) -> None:
        ids = tuple(dict.fromkeys(int(item) for item in document_ids))
        if not ids:
            return
        self._request(
            "POST",
            f"/collections/{self._config.collection}/points/delete",
            json={
                "filter": {
                    "must": [
                        {
                            "key": "document_id",
                            "match": {"any": list(ids)},
                        }
                    ]
                }
            },
        )

    def _ensure_collection(self, dimension: int) -> None:
        if self._collection_ready_dimension == dimension:
            return
        status = self._request_status("GET", f"/collections/{self._config.collection}")
        if status == 404:
            self._request(
                "PUT",
                f"/collections/{self._config.collection}",
                json={"vectors": {"size": dimension, "distance": "Cosine"}},
            )
        elif not (200 <= status < 300):
            raise RuntimeError(f"qdrant collection lookup failed: status={status}")
        self._collection_ready_dimension = dimension

    def _request_status(self, method: str, path: str) -> int:
        with httpx.Client(timeout=self._config.timeout_seconds) as client:
            response = client.request(method, self._url(path), headers=self._headers())
        return int(response.status_code)

    def _request(self, method: str, path: str, *, json: dict[str, Any] | None = None) -> dict[str, Any]:
        with httpx.Client(timeout=self._config.timeout_seconds) as client:
            response = client.request(method, self._url(path), headers=self._headers(), json=json)
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    def _url(self, path: str) -> str:
        return f"{self._config.url.rstrip('/')}{path}"

    def _headers(self) -> dict[str, str]:
        if not self._config.api_key:
            return {}
        return {"api-key": self._config.api_key}


@dataclass(frozen=True, slots=True)
class PostgresVectorStoreConfig:
    table_name: str = "current_info_chunk_vectors"


class PostgresVectorStore:
    def __init__(self, *, session_factory: sessionmaker[Session], config: PostgresVectorStoreConfig | None = None) -> None:
        self._session_factory = session_factory
        self._config = config or PostgresVectorStoreConfig()

    def upsert_chunks(self, chunks: tuple[VectorChunk, ...]) -> None:
        if not chunks:
            return
        dimension = len(chunks[0].vector)
        if dimension <= 0:
            raise ValueError("vector dimension must be > 0")
        if any(len(chunk.vector) != dimension for chunk in chunks):
            raise ValueError("all vectors in one upsert must have the same dimension")

        with self._session_factory() as session:
            existing_dimensions = {
                int(value)
                for value in session.execute(
                    text(
                        """
                        SELECT DISTINCT embedding_dimension
                        FROM current_info_chunk_vectors
                        WHERE embedding_dimension IS NOT NULL
                        """
                    )
                ).scalars()
            }
            if existing_dimensions and existing_dimensions != {dimension}:
                raise RuntimeError(
                    "postgres vector store already contains embeddings with a different dimension"
                )
            for chunk in chunks:
                session.execute(
                    text(
                        """
                        INSERT INTO current_info_chunk_vectors (
                            point_id,
                            chunk_id,
                            document_id,
                            chunk_index,
                            embedding,
                            embedding_dimension,
                            metadata_json,
                            updated_at
                        )
                        VALUES (
                            CAST(:point_id AS uuid),
                            :chunk_id,
                            :document_id,
                            :chunk_index,
                            CAST(:embedding AS vector),
                            :embedding_dimension,
                            :metadata_json,
                            now()
                        )
                        ON CONFLICT (chunk_id) DO UPDATE SET
                            point_id = EXCLUDED.point_id,
                            document_id = EXCLUDED.document_id,
                            chunk_index = EXCLUDED.chunk_index,
                            embedding = EXCLUDED.embedding,
                            embedding_dimension = EXCLUDED.embedding_dimension,
                            metadata_json = EXCLUDED.metadata_json,
                            updated_at = now()
                        """
                    ),
                    {
                        "point_id": chunk.point_id,
                        "chunk_id": int(chunk.chunk_id),
                        "document_id": int(chunk.document_id),
                        "chunk_index": int(chunk.chunk_index),
                        "embedding": _vector_literal(chunk.vector),
                        "embedding_dimension": dimension,
                        "metadata_json": json.dumps(chunk.metadata, ensure_ascii=False, sort_keys=True),
                    },
                )
            session.commit()

    def search(self, *, vector: tuple[float, ...], limit: int) -> tuple[VectorSearchResult, ...]:
        if not vector:
            return ()
        dimension = len(vector)
        with self._session_factory() as session:
            rows = session.execute(
                text(
                    """
                    SELECT
                        chunk_id,
                        metadata_json,
                        1 - (embedding <=> CAST(:embedding AS vector)) AS score
                    FROM current_info_chunk_vectors
                    WHERE embedding_dimension = :embedding_dimension
                    ORDER BY embedding <=> CAST(:embedding AS vector)
                    LIMIT :limit
                    """
                ),
                {
                    "embedding": _vector_literal(vector),
                    "embedding_dimension": dimension,
                    "limit": max(1, int(limit)),
                },
            ).mappings()
            parsed: list[VectorSearchResult] = []
            for row in rows:
                try:
                    metadata = json.loads(str(row.get("metadata_json") or "{}"))
                except json.JSONDecodeError:
                    metadata = {}
                parsed.append(
                    VectorSearchResult(
                        chunk_id=int(row["chunk_id"]),
                        score=float(row.get("score") or 0.0),
                        metadata=metadata if isinstance(metadata, dict) else {},
                    )
                )
            return tuple(parsed)

    def delete_document_ids(self, document_ids: tuple[int, ...]) -> None:
        ids = tuple(dict.fromkeys(int(item) for item in document_ids))
        if not ids:
            return
        with self._session_factory() as session:
            session.execute(
                text("DELETE FROM current_info_chunk_vectors WHERE document_id = ANY(:document_ids)"),
                {"document_ids": list(ids)},
            )
            session.commit()


@dataclass(frozen=True, slots=True)
class OllamaEmbeddingProvider:
    base_url: str
    model: str
    timeout_seconds: float = 30.0

    def embed_texts(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        if not texts:
            return ()
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                f"{self.base_url.rstrip('/')}/api/embed",
                json={"model": self.model, "input": list(texts)},
            )
            if response.status_code == 404:
                return tuple(self._embed_legacy(client, text) for text in texts)
        response.raise_for_status()
        payload = response.json()
        embeddings = payload.get("embeddings") if isinstance(payload, dict) else None
        if not isinstance(embeddings, list):
            raise RuntimeError("ollama embedding response missing embeddings")
        return tuple(_coerce_vector(item) for item in embeddings)

    def _embed_legacy(self, client: httpx.Client, text: str) -> tuple[float, ...]:
        response = client.post(
            f"{self.base_url.rstrip('/')}/api/embeddings",
            json={"model": self.model, "prompt": text},
        )
        response.raise_for_status()
        payload = response.json()
        embedding = payload.get("embedding") if isinstance(payload, dict) else None
        return _coerce_vector(embedding)


@dataclass(frozen=True, slots=True)
class OpenAIEmbeddingProvider:
    api_key: str
    model: str
    timeout_seconds: float = 30.0
    base_url: str = "https://api.openai.com/v1"

    def embed_texts(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        if not texts:
            return ()
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                f"{self.base_url.rstrip('/')}/embeddings",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": self.model, "input": list(texts)},
            )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            raise RuntimeError("openai embedding response missing data")
        ordered = sorted((item for item in data if isinstance(item, dict)), key=lambda item: int(item.get("index") or 0))
        return tuple(_coerce_vector(item.get("embedding")) for item in ordered)


class CurrentInfoVectorIndexer:
    def __init__(self, *, vector_store: VectorStore, embedding_provider: EmbeddingProvider) -> None:
        self._vector_store = vector_store
        self._embedding_provider = embedding_provider

    def upsert_chunks(self, rows: tuple[CurrentInfoDocumentChunk, ...]) -> None:
        texts = tuple(row.text_excerpt for row in rows if row.text_excerpt.strip())
        rows_with_text = tuple(row for row in rows if row.text_excerpt.strip())
        if not rows_with_text:
            return
        embeddings = self._embedding_provider.embed_texts(texts)
        if len(embeddings) != len(rows_with_text):
            raise RuntimeError("embedding provider returned unexpected vector count")
        chunks = tuple(
            VectorChunk(
                point_id=_point_id(document_id=int(row.document_id), chunk_index=int(row.chunk_index)),
                chunk_id=int(row.id),
                document_id=int(row.document_id),
                chunk_index=int(row.chunk_index),
                vector=vector,
                metadata={
                    "canonical_url": row.canonical_url,
                    "canonical_url_hash": row.canonical_url_hash,
                    "host": row.host,
                    "title": row.title,
                    "language": row.language,
                    "source_type": row.source_type,
                    "chunk_hash": row.chunk_hash,
                    "source_timestamp": _iso(row.source_timestamp or row.fetched_at),
                    "fetched_at": _iso(row.fetched_at),
                    "expires_at": _iso(row.expires_at),
                },
            )
            for row, vector in zip(rows_with_text, embeddings, strict=True)
        )
        self._vector_store.upsert_chunks(chunks)

    def delete_document_ids(self, document_ids: tuple[int, ...]) -> None:
        self._vector_store.delete_document_ids(document_ids)


class VectorCurrentInfoRetrievalProvider:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        vector_store: VectorStore,
        embedding_provider: EmbeddingProvider,
        fallback_provider: CurrentInfoRetrievalProvider,
    ) -> None:
        self._session_factory = session_factory
        self._vector_store = vector_store
        self._embedding_provider = embedding_provider
        self._fallback_provider = fallback_provider

    def retrieve(
        self,
        *,
        request: CurrentInfoRequest,
        documents: tuple[FetchedDocument, ...],
        search_results: tuple[SearchResult, ...],
    ) -> tuple[EvidenceChunk, ...]:
        try:
            query_vector = self._embedding_provider.embed_texts((request.query,))[0]
            vector_results = self._vector_store.search(vector=query_vector, limit=max(request.max_results, 1) * 3)
            chunks = self._chunks_from_vector_results(
                vector_results,
                request=request,
                limit=max(request.max_results, 1),
            )
        except Exception as exc:
            logger.warning("current_info_vector_retrieval_failed: %s", exc.__class__.__name__)
            chunks = ()
        if chunks:
            return chunks
        return self._fallback_provider.retrieve(request=request, documents=documents, search_results=search_results)

    def _chunks_from_vector_results(
        self,
        vector_results: tuple[VectorSearchResult, ...],
        *,
        request: CurrentInfoRequest,
        limit: int,
    ) -> tuple[EvidenceChunk, ...]:
        chunk_ids = tuple(dict.fromkeys(item.chunk_id for item in vector_results))
        if not chunk_ids:
            return ()
        score_by_id = {item.chunk_id: item.score for item in vector_results}
        order_by_id = {chunk_id: index for index, chunk_id in enumerate(chunk_ids)}
        current = datetime.now(UTC)
        filters = _metadata_filters(request)
        with self._session_factory() as session:
            query = select(CurrentInfoDocumentChunk).where(
                CurrentInfoDocumentChunk.id.in_(chunk_ids),
                CurrentInfoDocumentChunk.expires_at > current,
            )
            if source_types := filters.get("source_type"):
                query = query.where(CurrentInfoDocumentChunk.source_type.in_(source_types))
            if languages := filters.get("language"):
                query = query.where(CurrentInfoDocumentChunk.language.in_(languages))
            if hosts := filters.get("host"):
                query = query.where(CurrentInfoDocumentChunk.host.in_(hosts))
            rows = list(session.scalars(query))
        resolved_ids = {int(row.id) for row in rows}
        missing_ids = tuple(chunk_id for chunk_id in chunk_ids if chunk_id not in resolved_ids)
        if missing_ids:
            logger.warning(
                "current_info_vector_unresolved_db_pointers: count=%s",
                len(missing_ids),
            )
        rows.sort(key=lambda row: order_by_id.get(int(row.id), len(order_by_id)))
        chunks: list[EvidenceChunk] = []
        for row in rows[: max(1, int(limit))]:
            chunks.append(
                EvidenceChunk(
                    text=row.text_excerpt,
                    source_url=row.canonical_url,
                    source_title=row.title,
                    relevance=round(float(score_by_id.get(int(row.id), 0.0)), 6),
                    metadata={
                        "document_id": row.document_id,
                        "chunk_id": row.id,
                        "chunk_index": row.chunk_index,
                        "chunk_hash": row.chunk_hash,
                        "host": row.host,
                        "language": row.language,
                        "source_type": row.source_type,
                        "quality_score": row.quality_score,
                        "source_timestamp": _iso(row.source_timestamp or row.fetched_at),
                        "fetched_at": _iso(row.fetched_at),
                        "expires_at": _iso(row.expires_at),
                        "cache": "current_info_documents",
                        "retrieval": "vector",
                        "pointer_status": "verified_db_pointer",
                    },
                )
            )
        return tuple(chunks)


def build_current_info_vector_components_from_settings(
    settings: Any,
    *,
    session_factory: sessionmaker[Session] | None = None,
) -> tuple[CurrentInfoVectorIndexer, VectorStore, EmbeddingProvider] | None:
    if not bool(getattr(settings, "amo_vector_enabled", False)):
        return None
    provider = str(getattr(settings, "amo_vector_provider", "postgres")).strip().casefold()
    if provider == "postgres":
        if session_factory is None:
            return None
        vector_store: VectorStore = PostgresVectorStore(session_factory=session_factory)
    elif provider == "qdrant":
        vector_store = QdrantVectorStore(
            QdrantVectorStoreConfig(
                url=str(getattr(settings, "amo_vector_url", "")).strip().rstrip("/"),
                collection=str(getattr(settings, "amo_vector_collection", "current_info_chunks") or "current_info_chunks"),
                api_key=getattr(settings, "amo_vector_api_key", None),
                timeout_seconds=float(getattr(settings, "amo_vector_timeout_seconds", 3.0)),
            )
        )
    else:
        return None
    embedding_provider = build_embedding_provider_from_settings(settings)
    return CurrentInfoVectorIndexer(vector_store=vector_store, embedding_provider=embedding_provider), vector_store, embedding_provider


def build_embedding_provider_from_settings(settings: Any) -> EmbeddingProvider:
    provider = str(getattr(settings, "amo_vector_embedding_provider", "ollama")).strip().casefold()
    model = str(getattr(settings, "amo_vector_embedding_model", "")).strip()
    timeout_seconds = float(getattr(settings, "amo_vector_timeout_seconds", 3.0))
    if provider == "openai":
        api_key = str(getattr(settings, "openai_api_key", "") or "").strip()
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required when AMO_VECTOR_EMBEDDING_PROVIDER=openai")
        return OpenAIEmbeddingProvider(api_key=api_key, model=model, timeout_seconds=timeout_seconds)
    return OllamaEmbeddingProvider(
        base_url=str(getattr(settings, "ollama_base_url", "http://127.0.0.1:11434")),
        model=model,
        timeout_seconds=timeout_seconds,
    )


def _coerce_vector(value: Any) -> tuple[float, ...]:
    if not isinstance(value, list):
        raise RuntimeError("embedding response vector is invalid")
    vector = tuple(float(item) for item in value)
    if not vector:
        raise RuntimeError("embedding response vector is empty")
    return vector


def _vector_literal(vector: tuple[float, ...]) -> str:
    return "[" + ",".join(str(float(item)) for item in vector) + "]"


def _point_id(*, document_id: int, chunk_index: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"amo-current-info:{document_id}:{chunk_index}"))


def _metadata_filters(request: CurrentInfoRequest) -> dict[str, tuple[str, ...]]:
    metadata = dict(request.metadata or {})
    filters = metadata.get("filters") if isinstance(metadata.get("filters"), dict) else metadata
    normalized: dict[str, tuple[str, ...]] = {}
    source_types = _filter_values(filters.get("source_type") or filters.get("source_types"))
    if source_types:
        normalized["source_type"] = tuple(item.title() for item in source_types)
    languages = _filter_values(filters.get("language") or filters.get("languages"))
    if languages:
        normalized["language"] = tuple(languages)
    hosts = _filter_values(filters.get("host") or filters.get("hosts"))
    if hosts:
        normalized["host"] = tuple(item.removeprefix("www.") for item in hosts)
    return normalized


def _filter_values(value: Any) -> tuple[str, ...]:
    if value is None or value == "":
        return ()
    if isinstance(value, str):
        return (value.strip().casefold(),) if value.strip() else ()
    if isinstance(value, (list, tuple, set)):
        return tuple(dict.fromkeys(str(item).strip().casefold() for item in value if str(item).strip()))
    return (str(value).strip().casefold(),)


def _iso(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(UTC).isoformat()
