from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True, slots=True)
class DomainResearchProfile:
    domain: str
    need: str
    strategy: str
    candidate_count: int
    provider_names: tuple[str, ...] = ()
    source_names: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def usable(self) -> bool:
        return self.candidate_count > 0 and not any(warning.endswith("_not_identified") for warning in self.warnings)


_FINANCE_QUOTE_RE = re.compile(
    r"\b(?:kurs|price|preis|quote|aktuell|jetzt|now|current|steht|macht|market\s+cap|börsenwert|boersenwert)\b",
    re.IGNORECASE,
)
_FINANCE_RESEARCH_RE = re.compile(
    r"\b(?:fundamental|fundamentals|research|filing|filings|sec|10-k|10-q|earnings|umsatz|revenue|"
    r"gewinn|profit|dividende|dividend|bewertung|valuation|kgv|pe|bilanz|balance\s+sheet|news|nachrichten)\b",
    re.IGNORECASE,
)
_FINANCE_ENTITY_RE = re.compile(
    r"\b(?:[A-Z]{1,5}(?:\.[A-Z])?|[A-ZÄÖÜ][A-Za-zÄÖÜäöüß.-]{2,}(?:\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß.-]{2,}){0,3})\b"
)
_FINANCE_STOPWORDS = {
    "Was",
    "Wie",
    "Aktie",
    "Aktien",
    "Stock",
    "Share",
    "Shares",
    "Kurs",
    "Price",
    "Research",
    "Fundamentals",
    "News",
    "Börse",
    "Boerse",
}

_SPORT_SCHEDULE_RE = re.compile(r"\b(?:spielplan|fixtures?|schedule|termine|matches|partien|spiele)\b", re.IGNORECASE)
_SPORT_TABLE_RE = re.compile(r"\b(?:tabelle|standings?|gruppe(?:n)?|group(?:s)?|punkte|points|rangliste)\b", re.IGNORECASE)
_SPORT_RESULT_RE = re.compile(r"\b(?:ergebnis(?:se)?|result(?:s)?|score|scores?|live|stand)\b", re.IGNORECASE)
_SPORT_COMPETITION_RE = re.compile(
    r"\b(?:wm|weltmeisterschaft|world\s+cup|em|europameisterschaft|euro|bundesliga|champions\s+league|"
    r"europa\s+league|premier\s+league|la\s+liga|serie\s+a|nba|nfl|nhl|mlb|dfb\s+pokal)\b",
    re.IGNORECASE,
)


def build_domain_research_profile(*, session_factory, domain: str, query: str) -> DomainResearchProfile:
    """Build a metadata-driven finance/sport source profile from the DB registry."""

    normalized_domain = "stock" if domain == "finance" else (domain or "generic")
    if normalized_domain not in {"stock", "sports"} or session_factory is None:
        return DomainResearchProfile(
            domain=normalized_domain,
            need="not_applicable",
            strategy="fail_closed",
            candidate_count=0,
            warnings=(f"{normalized_domain}_domain_profile_not_configured",),
        )

    need, intent_warnings = _infer_need(normalized_domain, query)
    if intent_warnings:
        return DomainResearchProfile(
            domain=normalized_domain,
            need=need,
            strategy="fail_closed",
            candidate_count=0,
            warnings=intent_warnings,
        )

    from amo_bot.db.repositories import ResearchProviderRepository

    with session_factory() as session:
        records = ResearchProviderRepository(session).list_ranked_by_domain(normalized_domain)

    matching = tuple(record for record in records if _record_supports_need(record.metadata or {}, need))
    if not matching:
        return DomainResearchProfile(
            domain=normalized_domain,
            need=need,
            strategy="fail_closed",
            candidate_count=0,
            warnings=(f"{normalized_domain}_domain_profile_no_usable_source:{need}",),
        )

    strategy = _select_strategy(record.metadata or {} for record in matching)
    return DomainResearchProfile(
        domain=normalized_domain,
        need=need,
        strategy=strategy,
        candidate_count=len(matching),
        provider_names=tuple(record.provider_name for record in matching[:5]),
        source_names=tuple(record.source_name for record in matching[:5]),
        warnings=(f"{normalized_domain}_domain_profile:{need}", f"strategy:{strategy}"),
    )


def _infer_need(domain: str, query: str) -> tuple[str, tuple[str, ...]]:
    raw = query or ""
    if domain == "stock":
        if not _has_finance_entity(raw):
            return "finance_unknown_entity", ("stock_entity_not_identified",)
        if _FINANCE_RESEARCH_RE.search(raw):
            return "finance_research", ()
        if _FINANCE_QUOTE_RE.search(raw):
            return "finance_quote", ()
        return "finance_research", ()

    if domain == "sports":
        if not _SPORT_COMPETITION_RE.search(raw):
            return "sport_unknown_competition", ("sports_competition_not_identified",)
        if _SPORT_TABLE_RE.search(raw):
            return "sport_table", ()
        if _SPORT_SCHEDULE_RE.search(raw):
            return "sport_schedule", ()
        if _SPORT_RESULT_RE.search(raw):
            return "sport_result", ()
        return "sport_context", ()

    return "generic", ()


def _has_finance_entity(query: str) -> bool:
    for match in _FINANCE_ENTITY_RE.finditer(query or ""):
        token = match.group(0).strip()
        if token and token not in _FINANCE_STOPWORDS:
            return True
    return False


def _record_supports_need(metadata: dict[str, object], need: str) -> bool:
    needs = _metadata_tokens(metadata, "profile_needs", "coverage", "needs")
    if not needs:
        return True
    return need in needs or need.split("_", 1)[0] in needs or "all" in needs


def _select_strategy(metadata_items: Iterable[dict[str, object]]) -> str:
    strategies: list[str] = []
    for metadata in metadata_items:
        strategy = str(metadata.get("strategy") or "").strip().lower()
        source_type = str(metadata.get("source_type") or "").strip().lower()
        if strategy:
            strategies.append(strategy)
        elif "structured" in source_type or "official" in source_type:
            strategies.append("structured_first")
        elif "browser" in source_type or bool(metadata.get("browser_preferred")):
            strategies.append("browser_capable_chain")
        else:
            strategies.append("search_scrape_chain")
    if "structured_first" in strategies:
        return "structured_first"
    if "browser_capable_chain" in strategies:
        return "browser_capable_chain"
    return "search_scrape_chain"


def _metadata_tokens(metadata: dict[str, object], *keys: str) -> set[str]:
    tokens: set[str] = set()
    for key in keys:
        value = metadata.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            raw_items = re.split(r"[,;\s]+", value)
        else:
            raw_items = (str(value),)
        tokens.update(item.strip().lower() for item in raw_items if item.strip())
    return tokens
