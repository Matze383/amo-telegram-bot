from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from amo_bot.current_info.candidates import SOURCE_TYPE_NEWS, SOURCE_TYPE_UNKNOWN
from amo_bot.current_info.models import (
    CurrentInfoRequest,
    EvidenceChunk,
    EvidencePackage,
    EvidencePackageSource,
    FetchedDocument,
    SearchResult,
    TaskSpec,
)
from amo_bot.evidence_intents import classify_evidence_domain, is_finance_listing_query


DEFAULT_MAX_SOURCE_AGE_SECONDS = 7 * 24 * 60 * 60
NEWS_MAX_SOURCE_AGE_SECONDS = 48 * 60 * 60


def assemble_evidence_package(
    *,
    request: CurrentInfoRequest,
    task: TaskSpec,
    chunks: tuple[EvidenceChunk, ...],
    documents: tuple[FetchedDocument, ...],
    search_results: tuple[SearchResult, ...],
) -> EvidencePackage:
    """Assemble and verify current-info evidence.

    The rules intentionally mirror the older webtool evidence gate: current facts
    must be grounded in fetched sources, stale evidence lowers confidence, and
    conflicts stay visible as uncertainty instead of becoming confident answers.
    """

    domain = _evidence_domain(request=request, task=task, search_results=search_results)
    max_age_seconds = _max_source_age_seconds(request=request, domain=domain)
    sources = _package_sources(
        documents=documents,
        search_results=search_results,
        max_age_seconds=max_age_seconds,
    )
    fetched_urls = frozenset(document.url for document in documents if document.url)
    fetched_hosts = tuple(dict.fromkeys(source.host for source in sources if source.fetched and source.host))
    warnings: list[str] = []
    confidence = 0.0

    if not chunks:
        warnings.append("empty_evidence")
    if chunks and not _has_fetched_chunk(chunks=chunks, fetched_urls=fetched_urls):
        warnings.append("snippet_only_evidence")
    if chunks and documents and _has_unfetched_chunks(chunks=chunks, fetched_urls=fetched_urls):
        warnings.append("unfetched_chunk_evidence")

    if documents and "snippet_only_evidence" not in warnings:
        confidence = 0.72
        if len(fetched_hosts) >= 2:
            confidence = 0.86

    stale_count = sum(1 for source in sources if source.fetched and source.stale)
    if stale_count:
        warnings.append("stale_source")
        confidence = min(confidence, 0.55)

    if _requires_independent_hosts(domain=domain, search_results=search_results, request=request):
        if len(fetched_hosts) < 2:
            warnings.append("needs_independent_source")
            confidence = min(confidence, 0.58)
        elif _source_hosts_agree(chunks):
            confidence = max(confidence, 0.9)

    if _is_finance_listing_query(domain=domain, request=request, task=task):
        if len(fetched_hosts) < 2:
            warnings.append("finance_listing_requires_verified_sources")
            confidence = min(confidence, 0.58)

    if _has_source_conflict(chunks):
        warnings.append("source_conflict")
        confidence = min(confidence, 0.45)

    warnings.extend(_metadata_warning_codes(chunks))
    confidence = _apply_metadata_confidence(chunks, confidence)
    freshness = _freshness_label(sources=sources, documents=documents)
    return EvidencePackage(
        chunks=chunks,
        documents=documents,
        sources=sources,
        freshness=freshness,
        confidence=_bounded_confidence(confidence),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def _evidence_domain(
    *,
    request: CurrentInfoRequest,
    task: TaskSpec,
    search_results: tuple[SearchResult, ...],
) -> str:
    explicit = (task.domain or request.domain_hint or "").strip().lower()
    if explicit:
        return explicit
    text = " ".join((request.query, task.query, *(result.title for result in search_results)))
    return classify_evidence_domain(text)


def _max_source_age_seconds(*, request: CurrentInfoRequest, domain: str) -> int:
    raw = request.metadata.get("max_source_age_seconds")
    try:
        if raw is not None:
            return max(0, int(raw))
    except (TypeError, ValueError):
        pass
    if domain == "news":
        return NEWS_MAX_SOURCE_AGE_SECONDS
    return DEFAULT_MAX_SOURCE_AGE_SECONDS


def _package_sources(
    *,
    documents: tuple[FetchedDocument, ...],
    search_results: tuple[SearchResult, ...],
    max_age_seconds: int,
) -> tuple[EvidencePackageSource, ...]:
    by_url: dict[str, EvidencePackageSource] = {}
    for result in search_results:
        if not result.url:
            continue
        by_url[result.url] = EvidencePackageSource(
            url=result.url,
            title=result.title,
            host=result.host or _host(result.url),
            source_type=str(result.metadata.get("source_type") or SOURCE_TYPE_UNKNOWN),
            fetched=False,
        )

    for document in documents:
        if not document.url:
            continue
        existing = by_url.get(document.url)
        fetched_at = _timestamp_from_document(document)
        source = EvidencePackageSource(
            url=document.url,
            title=document.title or (existing.title if existing is not None else ""),
            host=(existing.host if existing is not None and existing.host else _host(document.url)),
            source_type=(existing.source_type if existing is not None else SOURCE_TYPE_UNKNOWN),
            fetched=True,
            fetched_at=document.fetched_at,
            stale=_is_stale(fetched_at, max_age_seconds=max_age_seconds) or _metadata_truthy(document.metadata.get("stale")),
        )
        by_url[document.url] = source
    return tuple(by_url.values())


def _requires_independent_hosts(
    *,
    domain: str,
    search_results: tuple[SearchResult, ...],
    request: CurrentInfoRequest,
) -> bool:
    if domain == "news":
        return True
    if any(str(result.metadata.get("source_type") or "") == SOURCE_TYPE_NEWS for result in search_results):
        return True
    if _is_finance_listing_query(domain=domain, request=request, task=TaskSpec(task_type="", query=request.query)):
        return True
    return classify_evidence_domain(request.query) == "news"


def _is_finance_listing_query(*, domain: str, request: CurrentInfoRequest, task: TaskSpec) -> bool:
    if domain not in {"stock", "crypto"}:
        return False
    text = " ".join((request.query, task.query))
    return is_finance_listing_query(text)


def _has_fetched_chunk(*, chunks: tuple[EvidenceChunk, ...], fetched_urls: frozenset[str]) -> bool:
    return any(chunk.source_url in fetched_urls for chunk in chunks if chunk.source_url)


def _has_unfetched_chunks(*, chunks: tuple[EvidenceChunk, ...], fetched_urls: frozenset[str]) -> bool:
    return any(chunk.source_url and chunk.source_url not in fetched_urls for chunk in chunks)


def _source_hosts_agree(chunks: tuple[EvidenceChunk, ...]) -> bool:
    values_by_key = _claim_values_by_key(chunks)
    return bool(values_by_key) and all(len(values) == 1 for values in values_by_key.values())


def _has_source_conflict(chunks: tuple[EvidenceChunk, ...]) -> bool:
    if any(_metadata_truthy(chunk.metadata.get("conflict")) for chunk in chunks):
        return True
    return any(len(values) > 1 for values in _claim_values_by_key(chunks).values())


def _claim_values_by_key(chunks: tuple[EvidenceChunk, ...]) -> dict[str, set[str]]:
    values_by_key: dict[str, set[str]] = defaultdict(set)
    for chunk in chunks:
        key = _metadata_str(chunk.metadata.get("claim_key") or chunk.metadata.get("fact_key"))
        value = _metadata_str(chunk.metadata.get("claim_value") or chunk.metadata.get("fact_value"))
        if key and value:
            values_by_key[key].add(value.casefold())
    return values_by_key


def _metadata_warning_codes(chunks: tuple[EvidenceChunk, ...]) -> tuple[str, ...]:
    warnings: list[str] = []
    for chunk in chunks:
        raw = chunk.metadata.get("warning_codes") or chunk.metadata.get("warnings")
        if isinstance(raw, str):
            warnings.extend(item.strip() for item in raw.replace(",", " ").split() if item.strip())
        elif isinstance(raw, (list, tuple, set)):
            warnings.extend(str(item).strip() for item in raw if str(item).strip())
    return tuple(dict.fromkeys(warnings))


def _apply_metadata_confidence(chunks: tuple[EvidenceChunk, ...], confidence: float) -> float:
    values: list[float] = []
    for chunk in chunks:
        raw = chunk.metadata.get("confidence")
        try:
            if raw is not None:
                values.append(_bounded_confidence(float(raw)))
        except (TypeError, ValueError):
            continue
    if not values:
        return confidence
    return min(confidence, sum(values) / len(values)) if confidence else sum(values) / len(values)


def _freshness_label(*, sources: tuple[EvidencePackageSource, ...], documents: tuple[FetchedDocument, ...]) -> str:
    if not documents:
        return "snippet_only"
    if any(source.stale for source in sources if source.fetched):
        return "stale"
    if any(source.fetched_at for source in sources if source.fetched):
        return "fresh"
    return "fetched_unknown_age"


def _timestamp_from_document(document: FetchedDocument) -> datetime | None:
    for value in (
        document.fetched_at,
        document.metadata.get("source_timestamp"),
        document.metadata.get("fetched_at"),
        document.metadata.get("published_at"),
    ):
        parsed = _parse_timestamp(value)
        if parsed is not None:
            return parsed
    return None


def _is_stale(value: datetime | None, *, max_age_seconds: int) -> bool:
    if value is None or max_age_seconds <= 0:
        return False
    return datetime.now(UTC) - value > timedelta(seconds=max_age_seconds)


def _parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    candidate = str(value or "").strip()
    if not candidate:
        return None
    normalized = candidate.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().rstrip(".").removeprefix("www.")


def _metadata_str(value: Any) -> str:
    return str(value or "").strip()


def _metadata_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "stale", "conflict"}


def _bounded_confidence(value: float) -> float:
    return max(0.0, min(float(value), 1.0))
