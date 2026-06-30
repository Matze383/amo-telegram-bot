from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timezone
from hashlib import sha256
from typing import Any, Protocol

from sqlalchemy import and_, bindparam, or_, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from amo_bot.db.models import RetrievableMemory, TopicDailyMemory, TopicLongMemory, TopicRecentMessage


logger = logging.getLogger(__name__)

CONTEXT_VECTOR_TABLE = "context_memory_vectors"
VECTOR_SOURCE_RECENT = "topic_recent_messages"
VECTOR_SOURCE_DAILY = "topic_daily_memories"
VECTOR_SOURCE_LONG = "topic_long_memories"
VECTOR_SOURCE_RETRIEVABLE = "retrievable_memories"
ALLOWED_VECTOR_SOURCES = {
    VECTOR_SOURCE_RECENT,
    VECTOR_SOURCE_DAILY,
    VECTOR_SOURCE_LONG,
    VECTOR_SOURCE_RETRIEVABLE,
}


@dataclass(frozen=True, slots=True)
class ContextVectorPointer:
    source_table: str
    source_id: int
    scope_type: str
    chat_id: int | None
    topic_id: int | None
    user_id: int | None
    visibility: str | None
    text: str
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ContextVectorSearchResult:
    source_table: str
    source_id: int
    score: float
    metadata: dict[str, Any]


class ContextMemoryVectorSearch(Protocol):
    def search(
        self,
        *,
        source_table: str,
        vector: tuple[float, ...],
        limit: int,
        scope_type: str | None = None,
        chat_id: int | None = None,
        topic_id: int | None = None,
        user_id: int | None = None,
        include_retrievable_visibility: bool = False,
    ) -> tuple[ContextVectorSearchResult, ...]:
        ...


class EmbeddingProvider(Protocol):
    def embed_texts(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        ...


class ContextMemoryVectorRepository:
    """Small pgvector pointer store for chat context and memory rows.

    Normal SQL tables remain the source of truth. This table only stores source
    pointers, scope columns, embedding metadata, and optional embedding values.
    """

    def __init__(self, *, session_factory: sessionmaker[Session], embedding_model: str) -> None:
        self._session_factory = session_factory
        self._embedding_model = (embedding_model or "").strip() or "unknown"

    def mark_pending(self, pointer: ContextVectorPointer, *, session: Session | None = None) -> None:
        if pointer.source_table not in ALLOWED_VECTOR_SOURCES:
            raise ValueError("unsupported vector source")
        text_hash = _hash_text(pointer.text)
        metadata_json = json.dumps(pointer.metadata, ensure_ascii=False, sort_keys=True)
        if session is not None:
            self._mark_pending_in_session(session, pointer=pointer, text_hash=text_hash, metadata_json=metadata_json)
            return
        with self._session_factory() as own_session:
            self._mark_pending_in_session(
                own_session,
                pointer=pointer,
                text_hash=text_hash,
                metadata_json=metadata_json,
            )
            own_session.commit()

    def _mark_pending_in_session(
        self,
        session: Session,
        *,
        pointer: ContextVectorPointer,
        text_hash: str,
        metadata_json: str,
    ) -> None:
        if not _table_exists(session, CONTEXT_VECTOR_TABLE):
            return
        existing = session.execute(
            text(
                """
                SELECT id, text_hash, status
                FROM context_memory_vectors
                WHERE source_table = :source_table
                  AND source_id = :source_id
                  AND embedding_model = :embedding_model
                """
            ),
            {
                "source_table": pointer.source_table,
                "source_id": int(pointer.source_id),
                "embedding_model": self._embedding_model,
            },
        ).mappings().first()
        status = "indexed" if existing and existing.get("text_hash") == text_hash and existing.get("status") == "indexed" else "pending"
        embedding_reset = "" if status == "indexed" else ", embedding = NULL, embedding_dimension = NULL"
        if existing:
            session.execute(
                text(
                    f"""
                    UPDATE context_memory_vectors
                    SET scope_type = :scope_type,
                        chat_id = :chat_id,
                        topic_id = :topic_id,
                        user_id = :user_id,
                        visibility = :visibility,
                        text_hash = :text_hash,
                        metadata_json = :metadata_json,
                        status = :status,
                        last_error = NULL,
                        updated_at = CURRENT_TIMESTAMP
                        {embedding_reset}
                    WHERE id = :id
                    """
                ),
                {
                    "id": int(existing["id"]),
                    "scope_type": pointer.scope_type,
                    "chat_id": pointer.chat_id,
                    "topic_id": pointer.topic_id,
                    "user_id": pointer.user_id,
                    "visibility": pointer.visibility,
                    "text_hash": text_hash,
                    "metadata_json": metadata_json,
                    "status": status,
                },
            )
            return
        session.execute(
            text(
                """
                INSERT INTO context_memory_vectors (
                    source_table,
                    source_id,
                    scope_type,
                    chat_id,
                    topic_id,
                    user_id,
                    visibility,
                    embedding_model,
                    text_hash,
                    metadata_json,
                    status
                )
                VALUES (
                    :source_table,
                    :source_id,
                    :scope_type,
                    :chat_id,
                    :topic_id,
                    :user_id,
                    :visibility,
                    :embedding_model,
                    :text_hash,
                    :metadata_json,
                    'pending'
                )
                """
            ),
            {
                "source_table": pointer.source_table,
                "source_id": int(pointer.source_id),
                "scope_type": pointer.scope_type,
                "chat_id": pointer.chat_id,
                "topic_id": pointer.topic_id,
                "user_id": pointer.user_id,
                "visibility": pointer.visibility,
                "embedding_model": self._embedding_model,
                "text_hash": text_hash,
                "metadata_json": metadata_json,
            },
        )

    def search(
        self,
        *,
        source_table: str,
        vector: tuple[float, ...],
        limit: int,
        scope_type: str | None = None,
        chat_id: int | None = None,
        topic_id: int | None = None,
        user_id: int | None = None,
        include_retrievable_visibility: bool = False,
    ) -> tuple[ContextVectorSearchResult, ...]:
        if source_table not in ALLOWED_VECTOR_SOURCES:
            raise ValueError("unsupported vector source")
        if not vector:
            return ()
        with self._session_factory() as session:
            if session.get_bind().dialect.name != "postgresql" or not _table_exists(session, CONTEXT_VECTOR_TABLE):
                return ()
            predicates = [
                "source_table = :source_table",
                "embedding_model = :embedding_model",
                "embedding_dimension = :embedding_dimension",
                "embedding IS NOT NULL",
                "status = 'indexed'",
            ]
            params: dict[str, Any] = {
                "source_table": source_table,
                "embedding_model": self._embedding_model,
                "embedding_dimension": len(vector),
                "embedding": _vector_literal(vector),
                "limit": max(1, int(limit)),
            }
            if source_table == VECTOR_SOURCE_RETRIEVABLE and include_retrievable_visibility:
                visibility_sql, visibility_params = _retrievable_visibility_sql(chat_id=chat_id, topic_id=topic_id, user_id=user_id)
                predicates.append(visibility_sql)
                params.update(visibility_params)
            else:
                if scope_type is None:
                    return ()
                predicates.append("scope_type = :scope_type")
                params["scope_type"] = scope_type
                if chat_id is None:
                    predicates.append("chat_id IS NULL")
                else:
                    predicates.append("chat_id = :chat_id")
                    params["chat_id"] = chat_id
                if topic_id is None:
                    predicates.append("topic_id IS NULL")
                else:
                    predicates.append("topic_id = :topic_id")
                    params["topic_id"] = topic_id
                if user_id is None:
                    predicates.append("user_id IS NULL")
                else:
                    predicates.append("user_id = :user_id")
                    params["user_id"] = user_id

            rows = session.execute(
                text(
                    f"""
                    SELECT source_table,
                           source_id,
                           metadata_json,
                           1 - (embedding <=> CAST(:embedding AS vector)) AS score
                    FROM context_memory_vectors
                    WHERE {' AND '.join(predicates)}
                    ORDER BY embedding <=> CAST(:embedding AS vector)
                    LIMIT :limit
                    """
                ),
                params,
            ).mappings()
            results: list[ContextVectorSearchResult] = []
            for row in rows:
                results.append(
                    ContextVectorSearchResult(
                        source_table=str(row["source_table"]),
                        source_id=int(row["source_id"]),
                        score=float(row.get("score") or 0.0),
                        metadata=_loads_metadata(row.get("metadata_json")),
                    )
                )
            return tuple(results)

    def index_pending(self, *, embedding_provider: EmbeddingProvider, limit: int = 100) -> int:
        safe_limit = max(1, min(int(limit), 1000))
        with self._session_factory() as session:
            if not _table_exists(session, CONTEXT_VECTOR_TABLE):
                return 0
            self.mark_missing_source_rows_pending(session=session, limit=safe_limit)
            pending = self._claim_pending_rows(session, limit=safe_limit)
            pointers = _load_source_pointers(session, pending)
            self._delete_unresolved_claimed_rows(session, pending, pointers)
            if not pointers:
                session.commit()
                return 0
            texts = tuple(pointer.text for pointer in pointers)
            try:
                embeddings = embedding_provider.embed_texts(texts)
                if len(embeddings) != len(pointers):
                    raise RuntimeError("embedding provider returned unexpected vector count")
            except Exception as exc:  # noqa: BLE001
                self._release_claimed_rows(session, pending, error=exc)
                session.commit()
                logger.warning("context_memory_vector_backfill_failed: %s", exc.__class__.__name__)
                return 0
            for pointer, vector in zip(pointers, embeddings, strict=True):
                self._upsert_embedding(session, pointer=pointer, vector=vector)
            session.commit()
            return len(pointers)

    def mark_missing_source_rows_pending(self, *, session: Session | None = None, limit: int = 100) -> int:
        safe_limit = max(1, min(int(limit), 1000))
        if session is not None:
            return self._mark_missing_source_rows_pending_in_session(session, limit=safe_limit)
        with self._session_factory() as own_session:
            count = self._mark_missing_source_rows_pending_in_session(own_session, limit=safe_limit)
            own_session.commit()
            return count

    def _mark_missing_source_rows_pending_in_session(self, session: Session, *, limit: int) -> int:
        if not _table_exists(session, CONTEXT_VECTOR_TABLE):
            return 0
        remaining = max(1, min(int(limit), 1000))
        count = 0
        for source_table, model, pointer_builder in (
            (VECTOR_SOURCE_RECENT, TopicRecentMessage, pointer_for_recent),
            (VECTOR_SOURCE_DAILY, TopicDailyMemory, pointer_for_daily),
            (VECTOR_SOURCE_LONG, TopicLongMemory, pointer_for_long),
            (VECTOR_SOURCE_RETRIEVABLE, RetrievableMemory, pointer_for_retrievable),
        ):
            if remaining <= 0:
                break
            ids = self._source_ids_missing_vector_rows(
                session,
                source_table=source_table,
                limit=remaining,
            )
            if not ids:
                continue
            for row in session.scalars(select(model).where(model.id.in_(ids))):
                pointer = pointer_builder(row)
                if pointer is None:
                    continue
                self._mark_pending_in_session(
                    session,
                    pointer=pointer,
                    text_hash=_hash_text(pointer.text),
                    metadata_json=json.dumps(pointer.metadata, ensure_ascii=False, sort_keys=True),
                )
                count += 1
                remaining -= 1
                if remaining <= 0:
                    break
        return count

    def _source_ids_missing_vector_rows(
        self,
        session: Session,
        *,
        source_table: str,
        limit: int,
    ) -> tuple[int, ...]:
        text_predicate = {
            VECTOR_SOURCE_RECENT: "source.message_text IS NOT NULL AND trim(source.message_text) <> ''",
            VECTOR_SOURCE_DAILY: "source.summary_text IS NOT NULL AND trim(source.summary_text) <> ''",
            VECTOR_SOURCE_LONG: "source.fact_text IS NOT NULL AND trim(source.fact_text) <> ''",
            VECTOR_SOURCE_RETRIEVABLE: (
                "(source.summary IS NOT NULL AND trim(source.summary) <> '') "
                "OR (source.content IS NOT NULL AND trim(source.content) <> '')"
            ),
        }.get(source_table)
        if text_predicate is None:
            return ()
        rows = session.execute(
            text(
                f"""
                SELECT source.id
                FROM {source_table} AS source
                WHERE ({text_predicate})
                  AND NOT EXISTS (
                    SELECT 1
                    FROM context_memory_vectors AS vectors
                    WHERE vectors.source_table = :source_table
                      AND vectors.source_id = source.id
                      AND vectors.embedding_model = :embedding_model
                )
                ORDER BY source.id ASC
                LIMIT :limit
                """
            ),
            {
                "source_table": source_table,
                "embedding_model": self._embedding_model,
                "limit": max(1, min(int(limit), 1000)),
            },
        )
        return tuple(int(row_id) for row_id in rows.scalars())

    def _claim_pending_rows(self, session: Session, *, limit: int) -> list[Any]:
        self._release_stale_claims(session)
        safe_limit = max(1, min(int(limit), 1000))
        if session.get_bind().dialect.name == "postgresql":
            return list(
                session.execute(
                    text(
                        """
                        WITH claimed AS (
                            SELECT id
                            FROM context_memory_vectors
                            WHERE embedding_model = :embedding_model
                              AND status IN ('pending', 'stale')
                            ORDER BY updated_at ASC, id ASC
                            LIMIT :limit
                            FOR UPDATE SKIP LOCKED
                        )
                        UPDATE context_memory_vectors AS vectors
                        SET status = 'indexing',
                            updated_at = CURRENT_TIMESTAMP
                        FROM claimed
                        WHERE vectors.id = claimed.id
                        RETURNING vectors.id, vectors.source_table, vectors.source_id
                        """
                    ),
                    {"embedding_model": self._embedding_model, "limit": safe_limit},
                ).mappings()
            )
        pending = list(
            session.execute(
                text(
                    """
                    SELECT id, source_table, source_id
                    FROM context_memory_vectors
                    WHERE embedding_model = :embedding_model
                      AND status IN ('pending', 'stale')
                    ORDER BY updated_at ASC, id ASC
                    LIMIT :limit
                    """
                ),
                {"embedding_model": self._embedding_model, "limit": safe_limit},
            ).mappings()
        )
        if not pending:
            return []
        session.execute(
            text(
                """
                UPDATE context_memory_vectors
                SET status = 'indexing',
                    updated_at = CURRENT_TIMESTAMP
                WHERE id IN :ids
                  AND status IN ('pending', 'stale')
                """
            ).bindparams(bindparam("ids", expanding=True)),
            {"ids": tuple(int(row["id"]) for row in pending)},
        )
        return pending

    def _release_stale_claims(self, session: Session) -> None:
        if session.get_bind().dialect.name == "postgresql":
            session.execute(
                text(
                    """
                    UPDATE context_memory_vectors
                    SET status = 'stale',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE embedding_model = :embedding_model
                      AND status = 'indexing'
                      AND updated_at < now() - interval '10 minutes'
                    """
                ),
                {"embedding_model": self._embedding_model},
            )
            return
        session.execute(
            text(
                """
                UPDATE context_memory_vectors
                SET status = 'stale',
                    updated_at = CURRENT_TIMESTAMP
                WHERE embedding_model = :embedding_model
                  AND status = 'indexing'
                  AND updated_at < datetime('now', '-10 minutes')
                """
            ),
            {"embedding_model": self._embedding_model},
        )

    def _release_claimed_rows(self, session: Session, pending: list[Any], *, error: Exception) -> None:
        if not pending:
            return
        session.execute(
            text(
                """
                UPDATE context_memory_vectors
                SET status = 'pending',
                    last_error = :last_error,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id IN :ids
                  AND status = 'indexing'
                """
            ).bindparams(bindparam("ids", expanding=True)),
            {
                "ids": tuple(int(row["id"]) for row in pending),
                "last_error": _compact_error(error),
            },
        )

    def _delete_unresolved_claimed_rows(
        self,
        session: Session,
        pending: list[Any],
        pointers: list[ContextVectorPointer],
    ) -> None:
        resolved = {(pointer.source_table, pointer.source_id) for pointer in pointers}
        unresolved_ids = tuple(
            int(row["id"])
            for row in pending
            if (str(row["source_table"]), int(row["source_id"])) not in resolved
        )
        if not unresolved_ids:
            return
        session.execute(
            text(
                """
                DELETE FROM context_memory_vectors
                WHERE id IN :ids
                  AND status = 'indexing'
                """
            ).bindparams(bindparam("ids", expanding=True)),
            {"ids": unresolved_ids},
        )

    def _upsert_embedding(
        self,
        session: Session,
        *,
        pointer: ContextVectorPointer,
        vector: tuple[float, ...],
    ) -> None:
        if not vector:
            raise ValueError("vector dimension must be > 0")
        embedding_value = _vector_literal(vector) if session.get_bind().dialect.name == "postgresql" else json.dumps(list(vector))
        embedding_sql = "CAST(:embedding AS vector)" if session.get_bind().dialect.name == "postgresql" else ":embedding"
        session.execute(
            text(
                f"""
                UPDATE context_memory_vectors
                SET scope_type = :scope_type,
                    chat_id = :chat_id,
                    topic_id = :topic_id,
                    user_id = :user_id,
                    visibility = :visibility,
                    embedding = {embedding_sql},
                    embedding_dimension = :embedding_dimension,
                    text_hash = :text_hash,
                    metadata_json = :metadata_json,
                    status = 'indexed',
                    last_error = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE source_table = :source_table
                  AND source_id = :source_id
                  AND embedding_model = :embedding_model
                """
            ),
            {
                "source_table": pointer.source_table,
                "source_id": int(pointer.source_id),
                "scope_type": pointer.scope_type,
                "chat_id": pointer.chat_id,
                "topic_id": pointer.topic_id,
                "user_id": pointer.user_id,
                "visibility": pointer.visibility,
                "embedding_model": self._embedding_model,
                "embedding": embedding_value,
                "embedding_dimension": len(vector),
                "text_hash": _hash_text(pointer.text),
                "metadata_json": json.dumps(pointer.metadata, ensure_ascii=False, sort_keys=True),
            },
        )


class ContextMemoryVectorRecall:
    def __init__(self, *, vector_search: ContextMemoryVectorSearch, embedding_provider: EmbeddingProvider) -> None:
        self._vector_search = vector_search
        self._embedding_provider = embedding_provider

    def search(self, **kwargs: Any) -> tuple[ContextVectorSearchResult, ...]:
        query_text = str(kwargs.pop("query_text", "") or "").strip()
        if not query_text:
            return ()
        vector = self._embedding_provider.embed_texts((query_text,))[0]
        return self._vector_search.search(vector=vector, **kwargs)


def pointer_for_recent(row: TopicRecentMessage) -> ContextVectorPointer | None:
    text_value = (row.message_text or "").strip()
    if not text_value:
        return None
    return ContextVectorPointer(
        source_table=VECTOR_SOURCE_RECENT,
        source_id=int(row.id),
        scope_type=row.scope_type,
        chat_id=row.chat_id,
        topic_id=row.topic_id,
        user_id=row.user_id,
        visibility=None,
        text=text_value,
        metadata={
            "telegram_message_id": row.telegram_message_id,
            "telegram_author_user_id": row.telegram_author_user_id,
            "source": row.source,
            "created_at": _iso(row.created_at),
        },
    )


def pointer_for_daily(row: TopicDailyMemory) -> ContextVectorPointer | None:
    text_value = (row.summary_text or "").strip()
    if not text_value:
        return None
    return ContextVectorPointer(
        source_table=VECTOR_SOURCE_DAILY,
        source_id=int(row.id),
        scope_type=row.scope_type,
        chat_id=row.chat_id,
        topic_id=row.topic_id,
        user_id=row.user_id,
        visibility=None,
        text=text_value,
        metadata={"memory_date": row.memory_date, "tokens_estimate": row.tokens_estimate},
    )


def pointer_for_long(row: TopicLongMemory) -> ContextVectorPointer | None:
    text_value = (row.fact_text or "").strip()
    if not text_value:
        return None
    return ContextVectorPointer(
        source_table=VECTOR_SOURCE_LONG,
        source_id=int(row.id),
        scope_type=row.scope_type,
        chat_id=row.chat_id,
        topic_id=row.topic_id,
        user_id=row.user_id,
        visibility=None,
        text=text_value,
        metadata={
            "is_active": bool(row.is_active),
            "source_daily_memory_id": row.source_daily_memory_id,
            "promotion_status": row.promotion_status,
            "answer_status": getattr(row, "answer_status", "legacy"),
        },
    )


def pointer_for_retrievable(row: RetrievableMemory) -> ContextVectorPointer | None:
    text_value = " ".join(
        part.strip()
        for part in (row.summary, row.content)
        if isinstance(part, str) and part.strip()
    ).strip()
    if not text_value:
        return None
    return ContextVectorPointer(
        source_table=VECTOR_SOURCE_RETRIEVABLE,
        source_id=int(row.id),
        scope_type=row.visibility,
        chat_id=row.chat_id,
        topic_id=row.message_thread_id,
        user_id=row.user_id,
        visibility=row.visibility,
        text=text_value,
        metadata={
            "memory_type": row.memory_type,
            "confidence": float(row.confidence or 0.0),
            "source": row.source,
            "active": bool(row.active),
            "expires_at": _iso(row.expires_at),
        },
    )


def _load_source_pointers(session: Session, pending_rows: list[Any]) -> list[ContextVectorPointer]:
    by_source: dict[str, list[int]] = {}
    for row in pending_rows:
        source_table = str(row["source_table"])
        if source_table not in ALLOWED_VECTOR_SOURCES:
            continue
        by_source.setdefault(source_table, []).append(int(row["source_id"]))

    pointers: list[ContextVectorPointer] = []
    for row in session.scalars(select(TopicRecentMessage).where(TopicRecentMessage.id.in_(by_source.get(VECTOR_SOURCE_RECENT, []) or [-1]))):
        if pointer := pointer_for_recent(row):
            pointers.append(pointer)
    for row in session.scalars(select(TopicDailyMemory).where(TopicDailyMemory.id.in_(by_source.get(VECTOR_SOURCE_DAILY, []) or [-1]))):
        if pointer := pointer_for_daily(row):
            pointers.append(pointer)
    for row in session.scalars(select(TopicLongMemory).where(TopicLongMemory.id.in_(by_source.get(VECTOR_SOURCE_LONG, []) or [-1]))):
        if pointer := pointer_for_long(row):
            pointers.append(pointer)
    for row in session.scalars(select(RetrievableMemory).where(RetrievableMemory.id.in_(by_source.get(VECTOR_SOURCE_RETRIEVABLE, []) or [-1]))):
        if pointer := pointer_for_retrievable(row):
            pointers.append(pointer)
    order = {(str(row["source_table"]), int(row["source_id"])): index for index, row in enumerate(pending_rows)}
    pointers.sort(key=lambda item: order.get((item.source_table, item.source_id), len(order)))
    return pointers


def _retrievable_visibility_sql(*, chat_id: int | None, topic_id: int | None, user_id: int | None) -> tuple[str, dict[str, Any]]:
    params: dict[str, Any] = {}
    conditions = ["visibility = 'global'"]
    if chat_id is not None:
        conditions.append("(visibility = 'chat' AND chat_id = :visibility_chat_id)")
        params["visibility_chat_id"] = chat_id
        if topic_id is not None:
            conditions.append(
                "(visibility = 'topic' AND chat_id = :visibility_chat_id AND topic_id = :visibility_topic_id)"
            )
            params["visibility_topic_id"] = topic_id
    if user_id is not None:
        params["visibility_user_id"] = user_id
        if chat_id is None:
            conditions.append("(visibility = 'user' AND user_id = :visibility_user_id AND chat_id IS NULL)")
        else:
            conditions.append(
                "(visibility = 'user' AND user_id = :visibility_user_id AND chat_id = :visibility_chat_id)"
            )
    return "(" + " OR ".join(conditions) + ")", params


def retrievable_visibility_filter(*, chat_id: int | None, topic_id: int | None, user_id: int | None):  # noqa: ANN201
    conditions = [RetrievableMemory.visibility == "global"]
    if chat_id is not None:
        conditions.append(and_(RetrievableMemory.visibility == "chat", RetrievableMemory.chat_id == chat_id))
        if topic_id is not None:
            conditions.append(
                and_(
                    RetrievableMemory.visibility == "topic",
                    RetrievableMemory.chat_id == chat_id,
                    RetrievableMemory.message_thread_id == topic_id,
                )
            )
    if user_id is not None:
        if chat_id is None:
            conditions.append(
                and_(
                    RetrievableMemory.visibility == "user",
                    RetrievableMemory.user_id == user_id,
                    RetrievableMemory.chat_id.is_(None),
                )
            )
        else:
            conditions.append(
                and_(
                    RetrievableMemory.visibility == "user",
                    RetrievableMemory.user_id == user_id,
                    RetrievableMemory.chat_id == chat_id,
                )
            )
    return or_(*conditions)


def _table_exists(session: Session, table_name: str) -> bool:
    try:
        return session.get_bind().dialect.has_table(session.connection(), table_name)
    except SQLAlchemyError:
        logger.debug("context memory vector table lookup failed", exc_info=True)
        return False


def _loads_metadata(value: object) -> dict[str, Any]:
    try:
        loaded = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _hash_text(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _compact_error(error: Exception) -> str:
    return f"{error.__class__.__name__}: {str(error)}"[:512]


def _vector_literal(vector: tuple[float, ...]) -> str:
    return "[" + ",".join(str(float(item)) for item in vector) + "]"


def _iso(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(UTC).isoformat()
