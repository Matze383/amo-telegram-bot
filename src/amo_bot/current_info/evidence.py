from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
import re
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
    now = _request_now(request)
    sources = _package_sources(
        documents=documents,
        search_results=search_results,
        max_age_seconds=max_age_seconds,
        now=now,
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
        if chunks and _all_finance_listing_chunks_entity_mismatch(request.query, chunks):
            warnings.append("irrelevant_source")
            confidence = min(confidence, 0.0)

    if _has_source_conflict(chunks):
        warnings.append("source_conflict")
        confidence = min(confidence, 0.45)

    warnings.extend(_metadata_warning_codes_for_decision(chunks=chunks, sources=sources, fetched_urls=fetched_urls))
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
    now: datetime,
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
            source_role=str(result.metadata.get("source_role") or ""),
            quality_label=str(result.metadata.get("quality_label") or _fallback_quality_label(result)),
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
            source_role=_fetched_source_role(existing.source_role if existing is not None else ""),
            quality_label=_fetched_quality_label(
                existing.quality_label if existing is not None else "",
                stale=_is_stale(fetched_at, max_age_seconds=max_age_seconds, now=now)
                or _metadata_truthy(document.metadata.get("stale")),
            ),
            fetched=True,
            fetched_at=document.fetched_at,
            stale=_is_stale(fetched_at, max_age_seconds=max_age_seconds, now=now)
            or _metadata_truthy(document.metadata.get("stale")),
        )
        by_url[document.url] = source
    return tuple(by_url.values())


def _fallback_quality_label(result: SearchResult) -> str:
    if result.provider == "direct_user_url":
        return "direct_user_url"
    if result.snippet:
        return "snippet_only"
    return "corroborating_source"


def _fetched_quality_label(existing: str, *, stale: bool) -> str:
    if stale:
        return "stale"
    if existing and existing != "snippet_only":
        return existing
    return "checked_source"


def _fetched_source_role(existing: str) -> str:
    if existing and existing != "snippet_only":
        return existing
    return "corroborating_source"


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


_FINANCE_QUERY_STOPWORDS = {
    "ag",
    "aktie",
    "aktien",
    "an",
    "boerse",
    "börse",
    "boersennotiert",
    "börsennotiert",
    "buy",
    "der",
    "die",
    "exchange",
    "is",
    "ist",
    "kann",
    "kaufen",
    "listed",
    "man",
    "nasdaq",
    "nyse",
    "on",
    "stock",
    "ticker",
    "publicly",
    "traded",
}
_ENTITY_COMPANY_FOLLOWERS = {
    "ag",
    "aktie",
    "aktien",
    "company",
    "corp",
    "corporation",
    "gmbh",
    "inc",
    "incorporated",
    "investor",
    "isin",
    "ltd",
    "nv",
    "plc",
    "profile",
    "public",
    "sa",
    "se",
    "share",
    "shares",
    "stock",
    "ticker",
    "wkn",
}


def _all_finance_listing_chunks_entity_mismatch(query: str, chunks: tuple[EvidenceChunk, ...]) -> bool:
    subject_tokens = _finance_query_subject_tokens(query)
    if len(subject_tokens) != 1:
        return False
    if finance_listing_parent_support(query, chunks):
        return False
    target = subject_tokens[0]
    saw_listing_evidence = False
    saw_matching_entity = False
    for chunk in chunks:
        text = " ".join((chunk.source_title or "", chunk.text or "")).casefold()
        if not _has_finance_listing_indicator(text):
            continue
        saw_listing_evidence = True
        if not _chunk_mentions_different_entity(text, target=target):
            saw_matching_entity = True
            break
    return saw_listing_evidence and not saw_matching_entity


def _finance_query_subject_tokens(query: str) -> tuple[str, ...]:
    without_urls = re.sub(r"https?://\S+", " ", query or "")
    tokens = []
    for token in re.findall(r"[A-Za-zÄÖÜäöüß][\wÄÖÜäöüß.-]*", without_urls.casefold()):
        normalized = token.strip(".-")
        if len(normalized) < 2 or normalized in _FINANCE_QUERY_STOPWORDS:
            continue
        tokens.append(normalized)
    return tuple(dict.fromkeys(tokens[:4]))


_PARENT_RELATION_MARKERS_RE = re.compile(
    r"\b(?:"
    r"part\s+of|subsidiary\s+of|brand\s+of|owned\s+by|parent\s+company\s+of|owns|owner\s+of|"
    r"geh[oö]rt\s+zu(?:m|r)?|teil\s+von|tochter(?:gesellschaft)?\s+von|marke\s+von|"
    r"muttergesellschaft\s+von|mutter\s+von|mutter"
    r")\b",
    re.IGNORECASE,
)
_PARENT_CANDIDATE_STOPWORDS = {
    "aktie",
    "automobilhersteller",
    "company",
    "corp",
    "corporation",
    "der",
    "des",
    "die",
    "inc",
    "konzern",
    "konzerns",
    "mutter",
    "muttergesellschaft",
    "owner",
    "parent",
    "plc",
    "sa",
    "se",
    "teil",
    "the",
}
_PARENT_LEGAL_SUFFIX_RE = re.compile(
    r"\s+(?:ag|corp(?:oration)?|group|inc(?:orporated)?|konzerns?|ltd|n\.?\s*v\.?|plc|s\.?\s*a\.?|se)$",
    re.IGNORECASE,
)


def finance_listing_parent_support(query: str, chunks: tuple[EvidenceChunk, ...]) -> tuple[str, ...]:
    """Return parent names that bridge a subsidiary/brand query to listing evidence."""

    subject_tokens = _finance_query_subject_tokens(query)
    if len(subject_tokens) != 1:
        return ()
    target = subject_tokens[0]
    parent_names: list[str] = []
    for chunk in chunks:
        text = " ".join((chunk.source_title or "", chunk.text or "")).strip()
        if text and target in text.casefold() and _PARENT_RELATION_MARKERS_RE.search(text):
            for candidate in _parent_company_candidates(text, target=target):
                if candidate not in parent_names:
                    parent_names.append(candidate)

    supported: list[str] = []
    for parent_name in parent_names:
        parent_key = parent_name.casefold()
        for chunk in chunks:
            text = " ".join((chunk.source_title or "", chunk.text or "")).casefold()
            if parent_key in text and _has_finance_listing_indicator(text):
                supported.append(parent_name)
                break
    return tuple(dict.fromkeys(supported))


def _parent_company_candidates(text: str, *, target: str) -> tuple[str, ...]:
    candidates: list[str] = []
    for raw in re.findall(
        r"\b[A-ZÄÖÜ][A-Za-zÄÖÜäöüß0-9&.-]{2,}"
        r"(?:\s+(?:AG|Corp\.?|Corporation|Group|Inc\.?|Konzern|Ltd\.?|N\.?\s*V\.?|PLC|S\.?\s*A\.?|SE))?",
        text,
    ):
        candidate = _normalize_parent_candidate(raw, target=target)
        if candidate and candidate not in candidates:
            candidates.append(candidate)

    lowered = text.casefold()
    patterns = (
        rf"\b{re.escape(target)}\s*-\s*mutter\s+([a-z0-9][a-z0-9&.-]{{2,}})",
        rf"\b{re.escape(target)}\b.{{0,80}}\b"
        r"(?:part\s+of|subsidiary\s+of|brand\s+of|owned\s+by|geh[oö]rt\s+zu(?:m|r)?|"
        r"teil\s+von|tochter(?:gesellschaft)?\s+von|marke\s+von)\b\s+"
        r"(?:der|die|dem|den|des|the|a|an|zum|zur|to|of)?\s*"
        r"([a-z0-9][a-z0-9&.-]{2,})",
        r"\b([a-z0-9][a-z0-9&.-]{2,})\b.{0,60}\b"
        r"(?:parent\s+company\s+of|owns|owner\s+of|muttergesellschaft\s+von|mutter\s+von)\b"
        rf".{{0,30}}\b{re.escape(target)}\b",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, lowered, re.IGNORECASE):
            candidate = _normalize_parent_candidate(match.group(1), target=target)
            if candidate and candidate not in candidates:
                candidates.append(candidate)
    return tuple(candidates)


def _normalize_parent_candidate(raw: str, *, target: str) -> str:
    candidate = re.sub(r"[-_/]+(?:group|konzerns?)$", "", (raw or "").strip(" .,:;()[]{}"), flags=re.IGNORECASE)
    candidate = _PARENT_LEGAL_SUFFIX_RE.sub("", candidate).strip(" .,:;()[]{}")
    if not candidate:
        return ""
    candidate_key = candidate.casefold()
    if target in candidate_key or candidate_key in _PARENT_CANDIDATE_STOPWORDS:
        return ""
    if len(candidate_key) < 3:
        return ""
    return candidate[:1].upper() + candidate[1:]


def _has_finance_listing_indicator(text: str) -> bool:
    return bool(
        re.search(
            r"\b(?:aktie|wkn|isin|ticker|symbol|börsennotiert|boersennotiert|publicly\s+listed|publicly\s+traded|listed)\b",
            text,
            re.IGNORECASE,
        )
    )


def _chunk_mentions_different_entity(text: str, *, target: str) -> bool:
    tokens = [token for token in re.findall(r"[a-zäöüß]+", text.casefold())]
    for index, token in enumerate(tokens):
        if token != target:
            continue
        next_token = tokens[index + 1] if index + 1 < len(tokens) else ""
        following_token = tokens[index + 2] if index + 2 < len(tokens) else ""
        if next_token and next_token not in _ENTITY_COMPANY_FOLLOWERS and following_token in _ENTITY_COMPANY_FOLLOWERS:
            return True
        return False
    return True


def _metadata_warning_codes(chunks: tuple[EvidenceChunk, ...]) -> tuple[str, ...]:
    warnings: list[str] = []
    for chunk in chunks:
        raw = chunk.metadata.get("warning_codes") or chunk.metadata.get("warnings")
        if isinstance(raw, str):
            warnings.extend(item.strip() for item in raw.replace(",", " ").split() if item.strip())
        elif isinstance(raw, (list, tuple, set)):
            warnings.extend(str(item).strip() for item in raw if str(item).strip())
    return tuple(dict.fromkeys(warnings))


_NON_BLOCKING_WITH_STRONG_SUPPORT_WARNINGS = {
    "irrelevant_source",
    "weak_source",
    "low_quality_source",
}
_BLOCKING_WARNING_MARKERS = ("conflict", "mismatch", "stale")
_WEAK_QUALITY_LABELS = {"snippet_only", "stale", "weak_source", "low_quality_source"}


def _metadata_warning_codes_for_decision(
    *,
    chunks: tuple[EvidenceChunk, ...],
    sources: tuple[EvidencePackageSource, ...],
    fetched_urls: frozenset[str],
) -> tuple[str, ...]:
    if not chunks:
        return ()
    strong_support_urls = _strong_support_urls(chunks=chunks, sources=sources, fetched_urls=fetched_urls)
    has_strong_support = len(strong_support_urls) >= 2
    warnings: list[str] = []
    for chunk in chunks:
        chunk_warnings = _metadata_warning_codes((chunk,))
        if has_strong_support and chunk.source_url not in strong_support_urls:
            warnings.extend(
                warning
                for warning in chunk_warnings
                if warning not in _NON_BLOCKING_WITH_STRONG_SUPPORT_WARNINGS
            )
            continue
        warnings.extend(chunk_warnings)
    return tuple(dict.fromkeys(warnings))


def _strong_support_urls(
    *,
    chunks: tuple[EvidenceChunk, ...],
    sources: tuple[EvidencePackageSource, ...],
    fetched_urls: frozenset[str],
) -> frozenset[str]:
    sources_by_url = {source.url: source for source in sources if source.url}
    urls: list[str] = []
    hosts: set[str] = set()
    for chunk in chunks:
        if not chunk.source_url or chunk.source_url not in fetched_urls:
            continue
        source = sources_by_url.get(chunk.source_url)
        if source is not None and (source.stale or source.quality_label in _WEAK_QUALITY_LABELS):
            continue
        chunk_warnings = _metadata_warning_codes((chunk,))
        if any(marker in warning for warning in chunk_warnings for marker in _BLOCKING_WARNING_MARKERS):
            continue
        if any(warning in _NON_BLOCKING_WITH_STRONG_SUPPORT_WARNINGS for warning in chunk_warnings):
            continue
        host = source.host if source is not None else _host(chunk.source_url)
        if not host or host in hosts:
            continue
        hosts.add(host)
        urls.append(chunk.source_url)
    return frozenset(urls)


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


def _request_now(request: CurrentInfoRequest) -> datetime:
    parsed = _parse_timestamp((request.metadata or {}).get("now"))
    return parsed or datetime.now(UTC)


def _is_stale(value: datetime | None, *, max_age_seconds: int, now: datetime | None = None) -> bool:
    if value is None or max_age_seconds <= 0:
        return False
    current = now or datetime.now(UTC)
    return current - value > timedelta(seconds=max_age_seconds)


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
