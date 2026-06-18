from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Iterable


_DATE_RE = re.compile(r"\b(20\d{2})[-/.](0[1-9]|1[0-2])[-/.](0[1-9]|[12]\d|3[01])\b")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")
_WORD_RE = re.compile(r"[a-z0-9äöüß]{3,}", re.IGNORECASE)
_STOPWORDS = {
    "about",
    "after",
    "also",
    "and",
    "are",
    "auf",
    "aus",
    "bei",
    "but",
    "das",
    "dem",
    "den",
    "der",
    "die",
    "ein",
    "eine",
    "for",
    "from",
    "has",
    "have",
    "ist",
    "latest",
    "laut",
    "mit",
    "nach",
    "news",
    "not",
    "oder",
    "official",
    "said",
    "sagt",
    "sagte",
    "says",
    "the",
    "und",
    "von",
    "was",
    "with",
    "zur",
}
_NEGATION_RE = re.compile(
    r"\b(?:not|no|denied|denies|false|nicht|kein(?:e[nrms]?)?|dementiert)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class NewsClaimCandidate:
    host: str
    claim_key: str
    text: str
    published_at: datetime | None
    source_role: str
    has_negation: bool
    weak_snippet: bool


@dataclass(frozen=True, slots=True)
class NewsCorroborationResult:
    status: str
    supporting_hosts: tuple[str, ...] = ()
    conflict_hosts: tuple[str, ...] = ()
    stale_hosts: tuple[str, ...] = ()
    primary_hosts: tuple[str, ...] = ()
    claim_key: str = ""

    @property
    def corroborated(self) -> bool:
        return self.status == "corroborated"


def assess_news_corroboration(
    extracts: tuple[tuple[str, str, str], ...],
    *,
    now: datetime | None = None,
    max_age_days: int = 14,
) -> NewsCorroborationResult:
    """Assess whether checked news extracts support the same compact claim."""
    now = now or datetime.now(UTC)
    candidates = tuple(_extract_candidates(extracts))
    if not candidates:
        return NewsCorroborationResult(status="no_claim_candidates")

    stale_hosts = tuple(
        sorted(
            {
                candidate.host
                for candidate in candidates
                if _is_stale(candidate, now=now, max_age_days=max_age_days)
            }
        )
    )
    fresh_candidates = tuple(candidate for candidate in candidates if candidate.host not in stale_hosts)
    if not fresh_candidates:
        return NewsCorroborationResult(status="stale_sources", stale_hosts=stale_hosts)

    conflicts = _find_conflicts(fresh_candidates)
    if conflicts:
        return NewsCorroborationResult(
            status="conflicting_claims",
            conflict_hosts=tuple(sorted(conflicts)),
            stale_hosts=stale_hosts,
        )

    grouped_claims = _group_related_claims(fresh_candidates)

    best_claim = ""
    best_candidates: tuple[NewsClaimCandidate, ...] = ()
    for grouped in grouped_claims:
        hosts = {candidate.host for candidate in grouped}
        if len(hosts) < 2:
            continue
        current = tuple(grouped)
        if not best_candidates or _claim_score(current) > _claim_score(best_candidates):
            best_claim = grouped[0].claim_key
            best_candidates = current

    if not best_candidates:
        primary_candidates = tuple(
            candidate
            for candidate in fresh_candidates
            if candidate.source_role == "primary" and not candidate.weak_snippet
        )
        if primary_candidates:
            best_primary = max(primary_candidates, key=lambda candidate: len(candidate.text))
            return NewsCorroborationResult(
                status="corroborated",
                supporting_hosts=(best_primary.host,),
                stale_hosts=stale_hosts,
                primary_hosts=(best_primary.host,),
                claim_key=best_primary.claim_key,
            )
        return NewsCorroborationResult(status="no_corroborated_claim", stale_hosts=stale_hosts)

    if all(candidate.weak_snippet for candidate in best_candidates) and _looks_like_repeated_weak_snippet(
        best_candidates
    ):
        return NewsCorroborationResult(
            status="weak_repeated_snippet",
            supporting_hosts=tuple(sorted({candidate.host for candidate in best_candidates})),
            stale_hosts=stale_hosts,
            claim_key=best_claim,
        )

    primary_hosts = tuple(sorted({candidate.host for candidate in best_candidates if candidate.source_role == "primary"}))
    return NewsCorroborationResult(
        status="corroborated",
        supporting_hosts=tuple(sorted({candidate.host for candidate in best_candidates})),
        stale_hosts=stale_hosts,
        primary_hosts=primary_hosts,
        claim_key=best_claim,
    )


def _extract_candidates(extracts: tuple[tuple[str, str, str], ...]) -> Iterable[NewsClaimCandidate]:
    for _, host, text in extracts:
        host = (host or "").strip().lower().removeprefix("www.")
        if not host:
            continue
        sentence = _first_claim_sentence(text)
        if not sentence:
            continue
        claim_key = _claim_key(sentence)
        if not claim_key:
            continue
        yield NewsClaimCandidate(
            host=host,
            claim_key=claim_key,
            text=sentence,
            published_at=_extract_published_at(text),
            source_role=_source_role(host=host, text=text),
            has_negation=bool(_NEGATION_RE.search(sentence)),
            weak_snippet=len(sentence) < 140,
        )


def _first_claim_sentence(text: str) -> str:
    compact = " ".join((text or "").split())
    for raw in _SENTENCE_SPLIT_RE.split(compact):
        sentence = raw.strip(" -")
        if len(sentence) < 40:
            continue
        if _looks_like_boilerplate(sentence):
            continue
        return sentence[:500]
    return compact[:500] if len(compact) >= 40 and not _looks_like_boilerplate(compact) else ""


def _looks_like_boilerplate(text: str) -> bool:
    lowered = text.lower()
    boilerplate_terms = ("cookie", "javascript", "subscribe", "newsletter", "enable js", "loading")
    return any(term in lowered for term in boilerplate_terms)


def _claim_key(sentence: str) -> str:
    normalized = re.sub(r"\b20\d{2}[-/.]\d{2}[-/.]\d{2}\b", " ", sentence.lower())
    tokens = [token for token in _WORD_RE.findall(normalized) if token not in _STOPWORDS]
    deduped = tuple(dict.fromkeys(tokens))
    if len(deduped) < 3:
        return ""
    return " ".join(deduped[:8])


def _extract_published_at(text: str) -> datetime | None:
    match = _DATE_RE.search(text or "")
    if not match:
        return None
    try:
        return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)), tzinfo=UTC)
    except ValueError:
        return None


def _source_role(*, host: str, text: str) -> str:
    lowered_host = _normalize_host(host)
    if _is_trusted_primary_news_host(lowered_host):
        return "primary"
    return "secondary"


def _normalize_host(host: str) -> str:
    return (host or "").strip().lower().removeprefix("www.")


def _is_trusted_primary_news_host(host: str) -> bool:
    normalized = _normalize_host(host)
    if not normalized:
        return False

    trusted_suffixes = (".gov", ".mil", ".int")
    trusted_exact_or_parent_hosts = (
        "gov.uk",
        "who.int",
        "europa.eu",
        "bund.de",
        "regierung.de",
    )
    return normalized.endswith(trusted_suffixes) or any(
        normalized == trusted_host or normalized.endswith(f".{trusted_host}")
        for trusted_host in trusted_exact_or_parent_hosts
    )


def _is_stale(candidate: NewsClaimCandidate, *, now: datetime, max_age_days: int) -> bool:
    if candidate.published_at is None:
        return False
    age = now - candidate.published_at
    return age.days > max_age_days


def _find_conflicts(candidates: tuple[NewsClaimCandidate, ...]) -> set[str]:
    conflicts: set[str] = set()
    for index, left in enumerate(candidates):
        left_tokens = set(left.claim_key.split())
        for right in candidates[index + 1:]:
            overlap = left_tokens.intersection(right.claim_key.split())
            if len(overlap) < 3:
                continue
            if left.has_negation != right.has_negation:
                conflicts.update((left.host, right.host))
    return conflicts


def _group_related_claims(candidates: tuple[NewsClaimCandidate, ...]) -> tuple[tuple[NewsClaimCandidate, ...], ...]:
    groups: list[list[NewsClaimCandidate]] = []
    for candidate in candidates:
        candidate_tokens = set(candidate.claim_key.split())
        placed = False
        for group in groups:
            representative = group[0]
            if candidate.has_negation != representative.has_negation:
                continue
            overlap = candidate_tokens.intersection(representative.claim_key.split())
            if len(overlap) >= 3:
                group.append(candidate)
                placed = True
                break
        if not placed:
            groups.append([candidate])
    return tuple(tuple(group) for group in groups)


def _looks_like_repeated_weak_snippet(candidates: tuple[NewsClaimCandidate, ...]) -> bool:
    return len({candidate.claim_key for candidate in candidates}) == 1


def _claim_score(candidates: tuple[NewsClaimCandidate, ...]) -> tuple[int, int, int]:
    hosts = {candidate.host for candidate in candidates}
    primary_count = sum(1 for candidate in candidates if candidate.source_role == "primary")
    non_weak_count = sum(1 for candidate in candidates if not candidate.weak_snippet)
    return (primary_count, len(hosts), non_weak_count)
