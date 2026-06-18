from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Iterable

from amo_bot.evidence_intents import is_finance_listing_query
from amo_bot.telegram import sports_query


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
_URL_RE = re.compile(r"https?://[^\s<>()\[\]{}\"']+", re.IGNORECASE)
_FINANCE_PAIR_RE = re.compile(r"\b[a-z0-9]{2,20}usdt\b", re.IGNORECASE)
_FINANCE_SUBJECT_TOKEN_RE = re.compile(r"\b[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß0-9&.-]{1,}\b")
_FINANCE_STOPWORDS = {
    "a",
    "about",
    "aktie",
    "aktien",
    "an",
    "auf",
    "boerse",
    "börse",
    "buy",
    "bybit",
    "can",
    "der",
    "die",
    "ein",
    "eine",
    "es",
    "exchange",
    "for",
    "frage",
    "fundamentals",
    "gibt",
    "handeln",
    "ipo",
    "is",
    "ist",
    "kann",
    "kaufen",
    "kurs",
    "listed",
    "listing",
    "jetzt",
    "macht",
    "man",
    "nasdaq",
    "news",
    "nyse",
    "ob",
    "offering",
    "on",
    "price",
    "public",
    "publicly",
    "quelle",
    "research",
    "share",
    "shares",
    "stock",
    "the",
    "traded",
    "usdt",
    "was",
    "whether",
    "wie",
}


def build_domain_research_profile(*, session_factory, domain: str, query: str) -> DomainResearchProfile:
    """Build a metadata-driven finance/sport source profile from the DB registry."""

    normalized_domain = "stock" if domain == "finance" else (domain or "generic")
    if normalized_domain not in {"stock", "sports", "crypto"}:
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

    if session_factory is None:
        built_in = _built_in_profile(domain=normalized_domain, need=need)
        if built_in is not None:
            return built_in
        return DomainResearchProfile(
            domain=normalized_domain,
            need=need,
            strategy="fail_closed",
            candidate_count=0,
            warnings=(f"{normalized_domain}_domain_profile_not_configured",),
        )

    from amo_bot.db.repositories import ResearchProviderRepository

    with session_factory() as session:
        records = ResearchProviderRepository(session).list_ranked_by_domain(normalized_domain)

    matching = tuple(record for record in records if _record_supports_need(record.metadata or {}, need))
    if not matching:
        built_in = _built_in_profile(domain=normalized_domain, need=need)
        if built_in is not None:
            return built_in
        learned = _learned_profile_from_observations(
            session_factory=session_factory,
            domain=normalized_domain,
            need=need,
        )
        if learned is not None:
            return learned
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


def _learned_profile_from_observations(*, session_factory, domain: str, need: str) -> DomainResearchProfile | None:
    if domain != "sports":
        return None

    from amo_bot.db.repositories import ResearchSourceObservationRepository

    since = datetime.now(UTC) - timedelta(days=14)
    with session_factory() as session:
        hosts = ResearchSourceObservationRepository(session).list_reliable_hosts(
            domain=domain,
            since=since,
            max_hosts=3,
            min_success_count=2,
        )
    if not hosts:
        return None

    source_names = tuple(record.host for record in hosts)
    return DomainResearchProfile(
        domain=domain,
        need=need,
        strategy="learned_search_scrape_chain",
        candidate_count=len(source_names),
        provider_names=tuple(f"learned_host:{host}" for host in source_names),
        source_names=source_names,
        warnings=(f"{domain}_domain_profile_learned_source:{need}", f"learned_sources:{'|'.join(source_names)}"),
    )


def _built_in_profile(*, domain: str, need: str) -> DomainResearchProfile | None:
    if domain == "stock" and need == "finance_listing":
        return DomainResearchProfile(
            domain=domain,
            need=need,
            strategy="verified_listing_web_research",
            candidate_count=3,
            provider_names=(
                "builtin:company_profile",
                "builtin:sec_company_tickers",
                "builtin:exchange_listing_directories",
            ),
            source_names=(
                "Company/official profile source",
                "SEC company ticker data",
                "Exchange listing directories",
            ),
            warnings=(f"{domain}_domain_profile_builtin_source:{need}", "strategy:verified_listing_web_research"),
        )
    if domain == "stock" and need == "finance_quote":
        return DomainResearchProfile(
            domain=domain,
            need=need,
            strategy="verified_quote_web_research",
            candidate_count=2,
            provider_names=(
                "builtin:exchange_or_market_data_quote",
                "builtin:issuer_or_finance_quote_source",
            ),
            source_names=(
                "Exchange or current market data quote source",
                "Issuer or finance quote source",
            ),
            warnings=(f"{domain}_domain_profile_builtin_source:{need}", "strategy:verified_quote_web_research"),
        )
    if domain == "stock" and need == "finance_research":
        return DomainResearchProfile(
            domain=domain,
            need=need,
            strategy="verified_finance_research_web",
            candidate_count=2,
            provider_names=(
                "builtin:issuer_investor_relations",
                "builtin:filings_or_finance_research_source",
            ),
            source_names=(
                "Issuer investor relations or filings",
                "Checked finance research source",
            ),
            warnings=(f"{domain}_domain_profile_builtin_source:{need}", "strategy:verified_finance_research_web"),
        )
    if domain == "crypto":
        strategy = "verified_crypto_listing_web_research" if need == "crypto_listing" else "verified_crypto_quote_web_research"
        return DomainResearchProfile(
            domain=domain,
            need=need,
            strategy=strategy,
            candidate_count=2,
            provider_names=(
                "builtin:crypto_exchange_or_market_data",
                "builtin:project_or_exchange_source",
            ),
            source_names=(
                "Crypto exchange or market data source",
                "Project or exchange source",
            ),
            warnings=(f"{domain}_domain_profile_builtin_source:{need}", f"strategy:{strategy}"),
        )
    return None


def _infer_need(domain: str, query: str) -> tuple[str, tuple[str, ...]]:
    raw = query or ""
    if domain == "stock":
        if not _has_finance_entity(raw):
            return "finance_unknown_entity", ("stock_entity_not_identified",)
        if is_finance_listing_query(raw):
            return "finance_listing", ()
        if _FINANCE_RESEARCH_RE.search(raw):
            return "finance_research", ()
        if _FINANCE_QUOTE_RE.search(raw):
            return "finance_quote", ()
        return "finance_research", ()

    if domain == "sports":
        if not sports_query.has_competition(raw):
            return "sport_unknown_competition", ("sports_competition_not_identified",)
        return sports_query.infer_need(raw), ()

    if domain == "crypto":
        if is_finance_listing_query(raw):
            return "crypto_listing", ()
        return "crypto_quote", ()

    return "generic", ()


def _has_finance_entity(query: str) -> bool:
    raw = query or ""
    if _FINANCE_PAIR_RE.search(raw):
        return True
    if _URL_RE.search(raw) and is_finance_listing_query(raw):
        return True
    for match in _FINANCE_ENTITY_RE.finditer(raw):
        token = match.group(0).strip()
        if token and not _is_finance_stopword_token(token):
            return True
    if is_finance_listing_query(raw):
        without_urls = _URL_RE.sub(" ", raw)
        for match in _FINANCE_SUBJECT_TOKEN_RE.finditer(without_urls):
            token = match.group(0).strip()
            if token and not _is_finance_stopword_token(token):
                return True
    return False


def _is_finance_stopword_token(token: str) -> bool:
    parts = tuple(part.casefold() for part in re.findall(r"[A-Za-zÄÖÜäöüß]+", token or ""))
    return bool(parts) and all(part in _FINANCE_STOPWORDS for part in parts)


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
