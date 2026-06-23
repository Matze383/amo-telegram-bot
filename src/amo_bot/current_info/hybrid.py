from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime, timezone
from typing import Any
from urllib.parse import urlparse

from amo_bot.current_info.candidates import (
    SOURCE_TYPE_DOCS,
    SOURCE_TYPE_NEWS,
    SOURCE_TYPE_OFFICIAL,
    SOURCE_TYPE_UNKNOWN,
    canonicalize_url,
)
from amo_bot.current_info.models import CurrentInfoRequest, EvidenceChunk, FetchedDocument, SearchResult
from amo_bot.current_info.ports import CurrentInfoRetrievalProvider

logger = logging.getLogger(__name__)


class HybridCurrentInfoRetrievalProvider:
    """Fuse database keyword retrieval with optional semantic vector retrieval."""

    def __init__(
        self,
        *,
        keyword_provider: CurrentInfoRetrievalProvider,
        vector_provider: CurrentInfoRetrievalProvider | None = None,
        rrf_k: int = 60,
    ) -> None:
        self._keyword_provider = keyword_provider
        self._vector_provider = vector_provider
        self._rrf_k = max(1, int(rrf_k))

    def retrieve(
        self,
        *,
        request: CurrentInfoRequest,
        documents: tuple[FetchedDocument, ...],
        search_results: tuple[SearchResult, ...],
    ) -> tuple[EvidenceChunk, ...]:
        keyword_chunks = self._retrieve_provider(
            self._keyword_provider,
            label="keyword",
            request=request,
            documents=documents,
            search_results=search_results,
        )
        vector_chunks = self._retrieve_provider(
            self._vector_provider,
            label="vector",
            request=request,
            documents=documents,
            search_results=search_results,
        )
        return self._fuse(
            request=request,
            keyword_chunks=self._filter_chunks(keyword_chunks, request=request),
            vector_chunks=self._filter_chunks(vector_chunks, request=request),
        )

    def _retrieve_provider(
        self,
        provider: CurrentInfoRetrievalProvider | None,
        *,
        label: str,
        request: CurrentInfoRequest,
        documents: tuple[FetchedDocument, ...],
        search_results: tuple[SearchResult, ...],
    ) -> tuple[EvidenceChunk, ...]:
        if provider is None:
            return ()
        try:
            chunks = provider.retrieve(request=request, documents=documents, search_results=search_results)
        except Exception as exc:
            logger.warning("current_info_hybrid_%s_retrieval_failed: %s", label, exc.__class__.__name__)
            return ()
        return tuple(_tag_retrieval(chunk, label) for chunk in chunks)

    def _fuse(
        self,
        *,
        request: CurrentInfoRequest,
        keyword_chunks: tuple[EvidenceChunk, ...],
        vector_chunks: tuple[EvidenceChunk, ...],
    ) -> tuple[EvidenceChunk, ...]:
        if not keyword_chunks and not vector_chunks:
            return ()

        now = _request_now(request)
        fused_by_key: dict[str, _FusedCandidate] = {}
        for source, chunks in (("keyword", keyword_chunks), ("vector", vector_chunks)):
            for rank, chunk in enumerate(chunks, start=1):
                key = _dedupe_key(chunk)
                candidate = fused_by_key.get(key)
                if candidate is None:
                    candidate = _FusedCandidate(chunk=chunk)
                    fused_by_key[key] = candidate
                candidate.source_ranks[source] = min(rank, candidate.source_ranks.get(source, rank))
                candidate.max_relevance = max(candidate.max_relevance, float(chunk.relevance or 0.0))
                candidate.retrieval_sources.add(source)
                if len(chunk.text) > len(candidate.chunk.text):
                    candidate.chunk = chunk

        scored: list[tuple[float, str, _FusedCandidate]] = []
        host_counts: dict[str, int] = {}
        for key, candidate in fused_by_key.items():
            chunk = candidate.chunk
            host = _host(chunk)
            host_occurrence = host_counts.get(host, 0)
            host_counts[host] = host_occurrence + 1
            score = self._score_candidate(candidate, now=now, host_occurrence=host_occurrence)
            scored.append((score, key, candidate))

        scored.sort(key=lambda item: (item[0], item[2].max_relevance, item[1]), reverse=True)
        limit = max(1, int(request.max_results or 5))
        return tuple(self._with_hybrid_metadata(candidate.chunk, score=score, candidate=candidate) for score, _, candidate in scored[:limit])

    def _score_candidate(self, candidate: _FusedCandidate, *, now: datetime, host_occurrence: int) -> float:
        score = 0.0
        for rank in candidate.source_ranks.values():
            score += 1.0 / (self._rrf_k + rank)
        score += min(max(candidate.max_relevance, 0.0), 1.0) * 0.08
        score += _source_type_boost(candidate.chunk)
        score += _recency_boost(candidate.chunk, now=now)
        score -= min(host_occurrence, 4) * 0.025
        score -= _weak_source_penalty(candidate.chunk)
        if {"keyword", "vector"}.issubset(candidate.retrieval_sources):
            score += 0.04
        return score

    def _with_hybrid_metadata(
        self,
        chunk: EvidenceChunk,
        *,
        score: float,
        candidate: _FusedCandidate,
    ) -> EvidenceChunk:
        metadata = dict(chunk.metadata)
        metadata["retrieval"] = "hybrid" if len(candidate.retrieval_sources) > 1 else next(iter(candidate.retrieval_sources))
        metadata["hybrid_trace"] = {
            "keyword_rank": candidate.source_ranks.get("keyword"),
            "vector_rank": candidate.source_ranks.get("vector"),
            "score": round(score, 6),
            "source_type": str(metadata.get("source_type") or SOURCE_TYPE_UNKNOWN),
            "host": _host(chunk),
        }
        return replace(chunk, source_url=canonicalize_url(chunk.source_url), relevance=round(score, 6), metadata=metadata)

    def _filter_chunks(self, chunks: tuple[EvidenceChunk, ...], *, request: CurrentInfoRequest) -> tuple[EvidenceChunk, ...]:
        filters = _metadata_filters(request)
        if not filters:
            return chunks
        return tuple(chunk for chunk in chunks if _matches_filters(chunk, filters=filters))


class _EmptyRetrievalProvider:
    def retrieve(
        self,
        *,
        request: CurrentInfoRequest,
        documents: tuple[FetchedDocument, ...],
        search_results: tuple[SearchResult, ...],
    ) -> tuple[EvidenceChunk, ...]:
        del request, documents, search_results
        return ()


EMPTY_RETRIEVAL_PROVIDER = _EmptyRetrievalProvider()


class _FusedCandidate:
    def __init__(self, *, chunk: EvidenceChunk) -> None:
        self.chunk = chunk
        self.source_ranks: dict[str, int] = {}
        self.retrieval_sources: set[str] = set()
        self.max_relevance = float(chunk.relevance or 0.0)


def _tag_retrieval(chunk: EvidenceChunk, retrieval: str) -> EvidenceChunk:
    metadata = dict(chunk.metadata)
    metadata.setdefault("retrieval", retrieval)
    return replace(chunk, metadata=metadata)


def _dedupe_key(chunk: EvidenceChunk) -> str:
    metadata = chunk.metadata
    chunk_hash = str(metadata.get("chunk_hash") or "").strip()
    if chunk_hash:
        return f"chunk_hash:{chunk_hash}"
    chunk_id = metadata.get("chunk_id")
    if chunk_id is not None:
        return f"chunk_id:{chunk_id}"
    canonical_url = canonicalize_url(chunk.source_url).casefold()
    text_key = " ".join(chunk.text.casefold().split())[:160]
    return f"url:{canonical_url}|text:{text_key}"


def _host(chunk: EvidenceChunk) -> str:
    raw_host = str(chunk.metadata.get("host") or "").strip().lower()
    if raw_host:
        return raw_host.removeprefix("www.")
    return (urlparse(chunk.source_url).hostname or "").lower().removeprefix("www.")


def _source_type_boost(chunk: EvidenceChunk) -> float:
    source_type = str(chunk.metadata.get("source_type") or SOURCE_TYPE_UNKNOWN)
    return {
        SOURCE_TYPE_OFFICIAL: 0.08,
        SOURCE_TYPE_DOCS: 0.05,
        SOURCE_TYPE_NEWS: 0.03,
    }.get(source_type, 0.0)


def _recency_boost(chunk: EvidenceChunk, *, now: datetime) -> float:
    timestamp = _parse_datetime(chunk.metadata.get("source_timestamp") or chunk.metadata.get("fetched_at"))
    if timestamp is None:
        return 0.0
    age_days = max(0.0, (now - timestamp).total_seconds() / 86400)
    return 0.06 / (1.0 + min(age_days, 365.0) / 14.0)


def _weak_source_penalty(chunk: EvidenceChunk) -> float:
    metadata = chunk.metadata
    penalty = 0.0
    source_type = str(metadata.get("source_type") or SOURCE_TYPE_UNKNOWN)
    if source_type == SOURCE_TYPE_UNKNOWN:
        penalty += 0.025
    try:
        quality = float(metadata.get("quality_score") or metadata.get("extraction_quality_score") or 0.0)
    except (TypeError, ValueError):
        quality = 0.0
    if 0.0 < quality < 0.4:
        penalty += 0.035
    outcome = str(
        metadata.get("source_observation_outcome")
        or metadata.get("observation_outcome")
        or metadata.get("outcome")
        or ""
    ).casefold()
    if outcome in {"unconfirmed", "low_quality", "fail_closed", "error", "denied", "blocked"}:
        penalty += 0.06
    return penalty


def _metadata_filters(request: CurrentInfoRequest) -> dict[str, set[str]]:
    metadata = dict(request.metadata or {})
    filters = metadata.get("filters") if isinstance(metadata.get("filters"), dict) else metadata
    normalized: dict[str, set[str]] = {}
    for key in ("source_type", "language", "host", "status"):
        values = _filter_values(filters.get(key) or filters.get(f"{key}s"))
        if values:
            normalized[key] = values
    freshness = _filter_values(filters.get("freshness") or filters.get("freshness_status"))
    if freshness:
        normalized["freshness"] = freshness
    return normalized


def _filter_values(value: Any) -> set[str]:
    if value is None or value == "":
        return set()
    if isinstance(value, str):
        return {value.strip().casefold()} if value.strip() else set()
    if isinstance(value, (list, tuple, set)):
        return {str(item).strip().casefold() for item in value if str(item).strip()}
    return {str(value).strip().casefold()}


def _matches_filters(chunk: EvidenceChunk, *, filters: dict[str, set[str]]) -> bool:
    metadata = chunk.metadata
    if "source_type" in filters and str(metadata.get("source_type") or "").casefold() not in filters["source_type"]:
        return False
    if "language" in filters and str(metadata.get("language") or "").casefold() not in filters["language"]:
        return False
    if "host" in filters and _host(chunk).casefold() not in filters["host"]:
        return False
    if "status" in filters and str(metadata.get("status") or metadata.get("cache_status") or "").casefold() not in filters["status"]:
        return False
    if "freshness" in filters:
        expires_at = _parse_datetime(metadata.get("expires_at"))
        is_fresh = expires_at is not None and expires_at > datetime.now(UTC)
        freshness = "fresh" if is_fresh else "expired"
        if freshness not in filters["freshness"]:
            return False
    return True


def _request_now(request: CurrentInfoRequest) -> datetime:
    raw_now = (request.metadata or {}).get("now")
    parsed = _parse_datetime(raw_now)
    return parsed or datetime.now(UTC)


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(UTC)
