from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import httpx

from amo_bot.evidence_intents import classify_evidence_domain as _classify_evidence_domain
from amo_bot.evidence_intents import is_finance_listing_query
from amo_bot.telegram import sports_query
from amo_bot.telegram.webtool_domain_profiles import build_domain_research_profile


@dataclass(frozen=True, slots=True)
class EvidenceSource:
    name: str
    url: str
    fetched_at: str


@dataclass(frozen=True, slots=True)
class DomainEvidenceResult:
    domain: str
    status: str
    confidence: float
    text: str
    sources: tuple[EvidenceSource, ...] = ()
    warnings: tuple[str, ...] = ()
    quality_flags: tuple[str, ...] = ()

    @property
    def confirmed(self) -> bool:
        return self.status == "confirmed" and bool(self.text.strip()) and bool(self.sources)


class WeatherEvidenceProvider(Protocol):
    def get_weather(self, *, query: str, locale: str) -> DomainEvidenceResult:
        ...


class CryptoEvidenceProvider(Protocol):
    def get_crypto(self, *, query: str, locale: str) -> DomainEvidenceResult:
        ...


@dataclass(frozen=True, slots=True)
class SourceDefinition:
    name: str
    domains: tuple[str, ...]
    coverage: tuple[str, ...]
    freshness_expectation: str
    authority: str
    cost: str
    supports_structured_data: bool
    failure_modes: tuple[str, ...]
    default_priority: int
    enabled_by_default: bool = True


@dataclass(frozen=True, slots=True)
class ProviderDefinition:
    name: str
    source_name: str
    domain: str
    default_priority: int
    fallback_allowed: bool
    min_confidence: float
    max_age_seconds: int | None
    enabled_by_default: bool = True


@dataclass(slots=True)
class ProviderHealth:
    success_count: int = 0
    failure_count: int = 0
    timeout_count: int = 0
    rate_limit_count: int = 0
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    last_error: str = ""

    @property
    def penalty(self) -> int:
        penalty = min(self.failure_count, 5) * 10 + min(self.timeout_count, 3) * 8 + min(self.rate_limit_count, 3) * 12
        if self.last_failure_at and datetime.now(UTC) - self.last_failure_at < timedelta(minutes=15):
            penalty += 30
        if self.last_success_at and datetime.now(UTC) - self.last_success_at < timedelta(minutes=15):
            penalty -= 10
        return penalty


class ProviderHealthStore(Protocol):
    def get(self, provider_name: str) -> ProviderHealth:
        ...

    def record_success(self, provider_name: str) -> None:
        ...

    def record_failure(self, provider_name: str, reason: str) -> None:
        ...


@dataclass(slots=True)
class ProviderHealthRegistry:
    _items: dict[str, ProviderHealth] = field(default_factory=dict)

    def get(self, provider_name: str) -> ProviderHealth:
        return self._items.setdefault(provider_name, ProviderHealth())

    def record_success(self, provider_name: str) -> None:
        health = self.get(provider_name)
        health.success_count += 1
        health.last_success_at = datetime.now(UTC)
        health.last_error = ""

    def record_failure(self, provider_name: str, reason: str) -> None:
        health = self.get(provider_name)
        health.failure_count += 1
        health.last_failure_at = datetime.now(UTC)
        health.last_error = reason
        lowered = reason.lower()
        if "timeout" in lowered:
            health.timeout_count += 1
        if "rate" in lowered or "429" in lowered:
            health.rate_limit_count += 1


class DbBackedProviderHealthRegistry:
    """Provider health adapter backed by short-lived DB sessions."""

    def __init__(self, *, session_factory) -> None:
        self._session_factory = session_factory

    def get(self, provider_name: str) -> ProviderHealth:
        from amo_bot.db.repositories import ResearchProviderHealthRepository

        with self._session_factory() as session:
            record = ResearchProviderHealthRepository(session).load_provider_health(provider_name)
            return ProviderHealth(
                success_count=record.success_count,
                failure_count=record.failure_count,
                timeout_count=record.timeout_count,
                rate_limit_count=record.rate_limit_count,
                last_success_at=_ensure_utc(record.last_success_at),
                last_failure_at=_ensure_utc(record.last_failure_at),
                last_error=record.last_error,
            )

    def record_success(self, provider_name: str) -> None:
        from amo_bot.db.repositories import ResearchProviderHealthRepository

        with self._session_factory() as session:
            ResearchProviderHealthRepository(session).record_success(provider_name)

    def record_failure(self, provider_name: str, reason: str) -> None:
        from amo_bot.db.repositories import ResearchProviderHealthRepository

        lowered = (reason or "").lower()
        with self._session_factory() as session:
            repo = ResearchProviderHealthRepository(session)
            if "timeout" in lowered:
                repo.record_timeout(provider_name, reason=reason)
            elif "rate" in lowered or "429" in lowered:
                repo.record_rate_limit(provider_name, reason=reason)
            else:
                repo.record_failure(provider_name, reason=reason)


SOURCE_REGISTRY: dict[str, SourceDefinition] = {
    "open_meteo": SourceDefinition(
        name="Open-Meteo",
        domains=("weather",),
        coverage=("global_geocoding", "current_weather", "short_forecast"),
        freshness_expectation="minutes_to_hours",
        authority="specialized_weather_api",
        cost="free_keyless",
        supports_structured_data=True,
        failure_modes=("timeout", "geocode_empty", "forecast_empty", "rate_limit"),
        default_priority=10,
    ),
    "weather_future_slot": SourceDefinition(
        name="Weather fallback provider slot",
        domains=("weather",),
        coverage=("future_structured_weather_fallback",),
        freshness_expectation="minutes_to_hours",
        authority="not_configured",
        cost="unknown",
        supports_structured_data=True,
        failure_modes=("not_configured",),
        default_priority=40,
        enabled_by_default=False,
    ),
    "wttr_in": SourceDefinition(
        name="wttr.in",
        domains=("weather",),
        coverage=("global_current_weather", "short_forecast"),
        freshness_expectation="minutes_to_hours",
        authority="public_weather_service",
        cost="free_keyless",
        supports_structured_data=True,
        failure_modes=("timeout", "location_empty", "current_missing", "rate_limit"),
        default_priority=40,
    ),
    "coingecko": SourceDefinition(
        name="CoinGecko",
        domains=("crypto",),
        coverage=("major_crypto_assets", "spot_price", "24h_change"),
        freshness_expectation="seconds_to_minutes",
        authority="specialized_market_data_api",
        cost="free_keyless",
        supports_structured_data=True,
        failure_modes=("timeout", "asset_missing", "price_missing", "rate_limit"),
        default_priority=10,
    ),
    "crypto_future_slot": SourceDefinition(
        name="Crypto fallback provider slot",
        domains=("crypto",),
        coverage=("future_structured_crypto_fallback",),
        freshness_expectation="seconds_to_minutes",
        authority="not_configured",
        cost="unknown",
        supports_structured_data=True,
        failure_modes=("not_configured",),
        default_priority=35,
        enabled_by_default=False,
    ),
    "binance": SourceDefinition(
        name="Binance",
        domains=("crypto",),
        coverage=("btc_eth_usdt_spot_pair", "24h_change"),
        freshness_expectation="seconds_to_minutes",
        authority="exchange_market_data_api",
        cost="free_keyless",
        supports_structured_data=True,
        failure_modes=("timeout", "symbol_missing", "price_missing", "rate_limit"),
        default_priority=35,
    ),
    "stock_future_slot": SourceDefinition(
        name="Stock provider slot",
        domains=("stock",),
        coverage=("future_structured_stock_quotes",),
        freshness_expectation="seconds_to_minutes",
        authority="not_configured",
        cost="unknown",
        supports_structured_data=True,
        failure_modes=("not_configured",),
        default_priority=100,
    ),
    "sports_future_slot": SourceDefinition(
        name="Sports provider slot",
        domains=("sports",),
        coverage=("future_structured_scores_standings",),
        freshness_expectation="minutes",
        authority="not_configured",
        cost="unknown",
        supports_structured_data=True,
        failure_modes=("not_configured",),
        default_priority=100,
    ),
}

PROVIDER_REGISTRY: dict[str, ProviderDefinition] = {
    "open_meteo_weather": ProviderDefinition("open_meteo_weather", "Open-Meteo", "weather", 10, True, 0.75, 3 * 60 * 60),
    "weather_future_provider": ProviderDefinition(
        "weather_future_provider",
        "Weather fallback provider slot",
        "weather",
        40,
        False,
        0.75,
        3 * 60 * 60,
        enabled_by_default=False,
    ),
    "wttr_in_weather": ProviderDefinition("wttr_in_weather", "wttr.in", "weather", 40, True, 0.70, 3 * 60 * 60),
    "coingecko_crypto": ProviderDefinition("coingecko_crypto", "CoinGecko", "crypto", 10, True, 0.75, 15 * 60),
    "crypto_future_provider": ProviderDefinition(
        "crypto_future_provider",
        "Crypto fallback provider slot",
        "crypto",
        35,
        False,
        0.75,
        15 * 60,
        enabled_by_default=False,
    ),
    "binance_crypto": ProviderDefinition("binance_crypto", "Binance", "crypto", 35, True, 0.76, 15 * 60),
    "stock_future_provider": ProviderDefinition(
        "stock_future_provider",
        "Stock provider slot",
        "stock",
        100,
        False,
        0.85,
        15 * 60,
        enabled_by_default=False,
    ),
    "sports_future_provider": ProviderDefinition(
        "sports_future_provider",
        "Sports provider slot",
        "sports",
        100,
        False,
        0.85,
        10 * 60,
        enabled_by_default=False,
    ),
}


_WEATHER_RE = re.compile(r"\b(?:wetter|weather|temperatur|temperature|regen|rain|forecast|vorhersage)\b", re.IGNORECASE)
_CRYPTO_RE = re.compile(
    r"\b(?:btc|bitcoin|eth|ethereum|crypto|krypto|kryptow(?:ä|ae)hrung|kurs|price|preis|usdt|token|bybit)\b",
    re.IGNORECASE,
)
_STOCK_RE = re.compile(
    r"\b(?:aktie|aktien|stock|share|shares|nasdaq|nyse|dax|etf|börse|boerse|ticker|filing|filings|"
    r"börsennotiert|boersennotiert|listed|publicly\s+traded|ipo|derivat|derivative|tokeni[sz]ed|"
    r"fundamental|fundamentals|research|dividende|dividend|earnings|kgv)\b",
    re.IGNORECASE,
)
_NEWS_RE = re.compile(r"\b(?:news|nachrichten|neueste(?:n)?|latest|breaking|was\s+gibt\s+es\s+(?:heute\s+)?neues)\b", re.IGNORECASE)
_CURRENT_MARKET_RE = re.compile(
    r"\b(?:kurs|price|preis|jetzt|now|aktuell|current|macht|steht|börsennotiert|boersennotiert|"
    r"listed|publicly\s+traded|ipo|listing|derivat|derivative|kaufen|buy|trade|traden|handeln|handelbar)\b",
    re.IGNORECASE,
)
_FINANCE_RESEARCH_SIGNAL_RE = re.compile(
    r"\b(?:fundamental|research|filing|filings|earnings|dividende|dividend|kgv)\b",
    re.IGNORECASE,
)
def classify_evidence_domain(text: str) -> str:
    return _classify_evidence_domain(text)


def _target_answer_language_instruction(locale: str) -> str:
    if (locale or "").lower().startswith("en"):
        return (
            "Target answer language: English. Keep source names, team names, titles, "
            "and technical identifiers in their original wording when appropriate."
        )
    return (
        "Ziel-Antwortsprache: Deutsch. Übersetze oder verändere keine Quellennamen, "
        "Teamnamen, Titel, Zahlen, Datumsangaben oder technischen Bezeichner; übernimm "
        "sie im Original, wenn sie aus der Quelle stammen."
    )


def format_domain_evidence_note(result: DomainEvidenceResult, *, locale: str = "de") -> str:
    source_lines = "\n".join(
        f"- {source.name}: {source.url} (Stand: {source.fetched_at})" for source in result.sources[:5]
    )
    warning_text = "\n".join(f"- {warning}" for warning in result.warnings)
    warnings = f"\nWarnings:\n{warning_text}" if warning_text else ""
    return (
        "DOMAIN EVIDENCE (STRUCTURED/FRESH) — STRICT INSTRUCTION:\n"
        f"{_target_answer_language_instruction(locale)}\n"
        f"Domain: {result.domain}; evidence status: {result.status}; confidence: {result.confidence:.2f}.\n"
        "Use this structured evidence before generic web snippets or model memory. "
        "Do NOT invent numbers, standings, forecasts, prices, or timestamps beyond this evidence. "
        "Mention source and timestamp/stand when answering.\n"
        f"Evidence:\n{result.text}\n"
        f"Sources:\n{source_lines}"
        f"{warnings}"
    )


def format_domain_fail_closed_response(*, domain: str, locale: str, warnings: tuple[str, ...] = ()) -> str:
    reason = warnings[0] if warnings else "keine belastbare strukturierte Quelle verfügbar"
    if (locale or "").lower().startswith("en"):
        if domain == "sports" and reason == "sports_result_opponent_score_not_confirmed":
            return (
                "I cannot reliably confirm a completed match result for the requested sports question. "
                f"Evidence status: {reason}. I will not infer an opponent or score from snippets or partial source context.\n"
                "Source/status: no confirmed source with opponent plus score in this attempt."
            )
        labels = {
            "weather": "weather values or forecast",
            "crypto": "crypto price",
            "stock": "stock listing or finance question" if _is_listing_warning(reason) else "stock price",
            "sports": "sports table or standings",
            "news": "current news",
        }
        label = labels.get(domain, "current facts")
        return (
            f"I cannot reliably confirm the requested {label} right now. "
            f"Evidence status: {reason}. I will not guess from snippets or stale model memory.\n"
            "Source/status: no confirmed source in this attempt."
        )
    labels = {
        "weather": "Wetterwerte oder die Vorhersage",
        "crypto": "Krypto-Kurs",
        "stock": "das Börsenlisting oder die Finanzfrage" if _is_listing_warning(reason) else "Aktienkurs",
        "sports": "Tabelle oder den Stand",
        "news": "aktuellen Nachrichten",
    }
    if domain == "sports" and reason == "sports_result_opponent_score_not_confirmed":
        return (
            "Ich finde kein belastbares Ergebnis für ein bereits absolviertes Sportspiel zur Anfrage "
            "und kann es gerade nicht belastbar bestätigen. "
            f"Evidenzstatus: {reason}. Ich leite keinen Gegner oder Score aus Such-Snippets oder Teilkontext ab.\n"
            "Quelle/Stand: keine bestätigte Quelle mit Gegner plus Score in diesem Versuch."
        )
    label = labels.get(domain, "aktuellen Fakten")
    return (
        f"Ich kann {label} gerade nicht belastbar bestätigen. "
        f"Evidenzstatus: {reason}. Ich rate nicht aus Such-Snippets oder altem Modellwissen.\n"
        "Quelle/Stand: keine bestätigte Quelle in diesem Versuch."
    )


def _is_listing_warning(reason: str) -> bool:
    lowered = (reason or "").casefold()
    return "finance_listing" in lowered or "listing" in lowered or "derivative" in lowered


class WebEvidencePipeline:
    def __init__(
        self,
        *,
        weather_provider: WeatherEvidenceProvider | None = None,
        crypto_provider: CryptoEvidenceProvider | None = None,
        session_factory=None,
    ) -> None:
        self._weather_provider = weather_provider
        self._crypto_provider = crypto_provider
        self._session_factory = session_factory

    def evaluate(self, *, query: str, locale: str) -> DomainEvidenceResult:
        domain = classify_evidence_domain(query)
        if domain == "weather":
            if self._weather_provider is None:
                return _unavailable(domain, "weather_provider_not_configured")
            return self._weather_provider.get_weather(query=query, locale=locale)
        if domain == "crypto":
            if self._crypto_provider is None:
                return _unavailable(domain, "crypto_provider_not_configured")
            return self._crypto_provider.get_crypto(query=query, locale=locale)
        if domain in {"stock", "sports"}:
            profile = build_domain_research_profile(
                session_factory=self._session_factory,
                domain=domain,
                query=query,
            )
            if not profile.usable:
                return DomainEvidenceResult(
                    domain=domain,
                    status="unavailable",
                    confidence=0.0,
                    text="",
                    warnings=profile.warnings or (f"{domain}_domain_profile_not_configured",),
                )
            source_text = ", ".join(profile.source_names[:3]) or "DB source registry"
            return DomainEvidenceResult(
                domain=domain,
                status="needs_profiled_web_research",
                confidence=0.0,
                text=f"Need: {profile.need}. Strategy: {profile.strategy}. Candidate sources: {source_text}.",
                warnings=profile.warnings,
            )
        if domain == "news":
            return DomainEvidenceResult(
                domain="news",
                status="needs_multi_source_web",
                confidence=0.0,
                text="",
                warnings=("news_requires_multiple_checked_sources",),
            )
        return DomainEvidenceResult(domain="generic", status="not_applicable", confidence=0.0, text="")


@dataclass(frozen=True, slots=True)
class EvidenceProviderCandidate:
    definition: ProviderDefinition
    provider: WeatherEvidenceProvider | CryptoEvidenceProvider


def build_evidence_candidates_from_db(
    *,
    session_factory,
    domain: str,
    providers: dict[str, WeatherEvidenceProvider | CryptoEvidenceProvider],
) -> tuple[EvidenceProviderCandidate, ...]:
    """Build runtime candidates from DB provider metadata, matched to local implementations."""

    from amo_bot.db.repositories import ResearchProviderRepository

    with session_factory() as session:
        records = ResearchProviderRepository(session).list_ranked_by_domain(domain)
    candidates: list[EvidenceProviderCandidate] = []
    for record in records:
        provider = providers.get(record.provider_name)
        if provider is None:
            continue
        priority = record.selection_score if record.selection_score is not None else record.default_priority
        candidates.append(
            EvidenceProviderCandidate(
                ProviderDefinition(
                    name=record.provider_name,
                    source_name=record.source_name,
                    domain=record.domain,
                    default_priority=priority,
                    fallback_allowed=record.fallback_allowed,
                    min_confidence=record.min_confidence,
                    max_age_seconds=record.max_age_seconds,
                    enabled_by_default=record.enabled,
                ),
                provider,
            )
        )
    return tuple(candidates)


def _quality_gate(result: DomainEvidenceResult, definition: ProviderDefinition) -> DomainEvidenceResult:
    flags: list[str] = list(result.quality_flags)
    if result.domain != definition.domain:
        flags.append("domain_mismatch")
    if not (result.text or "").strip():
        flags.append("empty")
    if not result.sources:
        flags.append("source_missing")
    if result.confidence < definition.min_confidence:
        flags.append("low_confidence")
    if definition.max_age_seconds is not None and _is_stale(result, max_age_seconds=definition.max_age_seconds):
        flags.append("stale")
    if flags:
        return DomainEvidenceResult(
            domain=result.domain or definition.domain,
            status="low_quality" if result.status == "confirmed" else result.status,
            confidence=min(result.confidence, definition.min_confidence - 0.01),
            text=result.text,
            sources=result.sources,
            warnings=(*result.warnings, *tuple(dict.fromkeys(flags))),
            quality_flags=tuple(dict.fromkeys(flags)),
        )
    return result


class ResilientWeatherEvidenceProvider:
    def __init__(
        self,
        candidates: tuple[EvidenceProviderCandidate, ...] | None = None,
        *,
        health: ProviderHealthStore | None = None,
    ) -> None:
        self._health = health or ProviderHealthRegistry()
        self._candidates = candidates if candidates is not None else (
            EvidenceProviderCandidate(PROVIDER_REGISTRY["open_meteo_weather"], OpenMeteoEvidenceProvider()),
            EvidenceProviderCandidate(PROVIDER_REGISTRY["wttr_in_weather"], WttrInEvidenceProvider()),
        )

    @property
    def health(self) -> ProviderHealthStore:
        return self._health

    def get_weather(self, *, query: str, locale: str) -> DomainEvidenceResult:
        return _run_resilient_candidates(
            domain="weather",
            candidates=self._candidates,
            health=self._health,
            invoke=lambda provider: provider.get_weather(query=query, locale=locale),  # type: ignore[attr-defined]
        )


class ResilientCryptoEvidenceProvider:
    def __init__(
        self,
        candidates: tuple[EvidenceProviderCandidate, ...] | None = None,
        *,
        health: ProviderHealthStore | None = None,
    ) -> None:
        self._health = health or ProviderHealthRegistry()
        self._candidates = candidates if candidates is not None else (
            EvidenceProviderCandidate(PROVIDER_REGISTRY["coingecko_crypto"], CoinGeckoEvidenceProvider()),
            EvidenceProviderCandidate(PROVIDER_REGISTRY["binance_crypto"], BinanceTickerEvidenceProvider()),
        )

    @property
    def health(self) -> ProviderHealthStore:
        return self._health

    def get_crypto(self, *, query: str, locale: str) -> DomainEvidenceResult:
        return _run_resilient_candidates(
            domain="crypto",
            candidates=self._candidates,
            health=self._health,
            invoke=lambda provider: provider.get_crypto(query=query, locale=locale),  # type: ignore[attr-defined]
        )


def _run_resilient_candidates(
    *,
    domain: str,
    candidates: tuple[EvidenceProviderCandidate, ...],
    health: ProviderHealthStore,
    invoke,
) -> DomainEvidenceResult:
    ordered = sorted(candidates, key=lambda candidate: candidate.definition.default_priority + health.get(candidate.definition.name).penalty)
    warnings: list[str] = []
    low_quality_result: DomainEvidenceResult | None = None
    for idx, candidate in enumerate(ordered):
        definition = candidate.definition
        if definition.domain != domain:
            continue
        if idx > 0 and not definition.fallback_allowed:
            warnings.append(f"{definition.name}:fallback_not_allowed")
            continue
        try:
            result = invoke(candidate.provider)
        except Exception as exc:
            reason = f"{definition.name}:exception:{type(exc).__name__}"
            health.record_failure(definition.name, reason)
            warnings.append(reason)
            continue

        checked = _quality_gate(result, definition)
        if checked.confirmed and not checked.quality_flags:
            health.record_success(definition.name)
            if warnings:
                return DomainEvidenceResult(
                    domain=checked.domain,
                    status=checked.status,
                    confidence=checked.confidence,
                    text=checked.text,
                    sources=checked.sources,
                    warnings=(*checked.warnings, *tuple(warnings), f"provider:{definition.source_name}"),
                    quality_flags=checked.quality_flags,
                )
            return DomainEvidenceResult(
                domain=checked.domain,
                status=checked.status,
                confidence=checked.confidence,
                text=checked.text,
                sources=checked.sources,
                warnings=(*checked.warnings, f"provider:{definition.source_name}"),
                quality_flags=checked.quality_flags,
            )

        reason = checked.status or "unconfirmed"
        diagnostics = tuple(dict.fromkeys((*checked.warnings, *checked.quality_flags)))
        diagnostic_text = ",".join(diagnostics)
        failure_reason = f"{definition.name}:{reason}" + (f":{diagnostic_text}" if diagnostic_text else "")
        if not _is_neutral_unavailability(checked):
            health.record_failure(definition.name, failure_reason)
        warnings.append(failure_reason)
        if low_quality_result is None or checked.confidence > low_quality_result.confidence:
            low_quality_result = checked

    if low_quality_result is not None and low_quality_result.status in {"confirmed", "low_quality"}:
        return DomainEvidenceResult(
            domain=domain,
            status="low_quality",
            confidence=low_quality_result.confidence,
            text="",
            sources=(),
            warnings=tuple(warnings) or ("provider_quality_gate_failed",),
            quality_flags=low_quality_result.quality_flags,
        )
    return DomainEvidenceResult(
        domain=domain,
        status="unavailable",
        confidence=0.0,
        text="",
        sources=(),
        warnings=tuple(warnings) or (f"{domain}_provider_not_configured",),
    )


def _is_neutral_unavailability(result: DomainEvidenceResult) -> bool:
    """Do not let user/entity misses poison provider health."""
    neutral_warnings = {
        "crypto_asset_not_recognized",
        "crypto_fallback_supports_usd_only",
        "weather_location_missing",
        "weather_location_not_found",
    }
    return bool(set(result.warnings).intersection(neutral_warnings))


def _ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _unavailable(domain: str, warning: str) -> DomainEvidenceResult:
    return DomainEvidenceResult(domain=domain, status="unavailable", confidence=0.0, text="", warnings=(warning,))


class OpenMeteoEvidenceProvider:
    _GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
    _FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

    def __init__(self, *, client: httpx.Client | None = None, timeout_seconds: float = 5.0) -> None:
        self._client = client
        self._timeout = timeout_seconds

    def get_weather(self, *, query: str, locale: str) -> DomainEvidenceResult:
        location = _extract_location(query)
        if not location:
            return _unavailable("weather", "weather_location_missing")
        fetched_at = _now_iso()
        try:
            geocode = self._get(
                self._GEOCODE_URL,
                params={"name": location, "count": 1, "language": _language(locale), "format": "json"},
            )
            first = (geocode.get("results") or [None])[0]
            if not isinstance(first, dict):
                return _unavailable("weather", "weather_location_not_found")
            latitude = first.get("latitude")
            longitude = first.get("longitude")
            name = str(first.get("name") or location)
            country = str(first.get("country_code") or first.get("country") or "").strip()
            forecast = self._get(
                self._FORECAST_URL,
                params={
                    "latitude": latitude,
                    "longitude": longitude,
                    "current": "temperature_2m,relative_humidity_2m,precipitation,weather_code,wind_speed_10m",
                    "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
                    "timezone": "auto",
                    "forecast_days": 2,
                },
            )
        except Exception as exc:
            return _unavailable("weather", f"weather_provider_error:{type(exc).__name__}")

        current = forecast.get("current")
        if not isinstance(current, dict):
            return _unavailable("weather", "weather_current_missing")
        units = forecast.get("current_units") if isinstance(forecast.get("current_units"), dict) else {}
        daily = forecast.get("daily") if isinstance(forecast.get("daily"), dict) else {}
        place = f"{name}, {country}" if country else name
        temp = _value_with_unit(current.get("temperature_2m"), units.get("temperature_2m", "°C"))
        wind = _value_with_unit(current.get("wind_speed_10m"), units.get("wind_speed_10m", "km/h"))
        precipitation = _value_with_unit(current.get("precipitation"), units.get("precipitation", "mm"))
        weather_code = current.get("weather_code")
        current_time = str(current.get("time") or fetched_at)
        daily_text = _daily_weather_summary(daily)
        text = (
            f"Ort: {place}. Stand: {current_time}. Aktuell: Temperatur {temp}, Niederschlag {precipitation}, "
            f"Wind {wind}, Wettercode {weather_code}."
        )
        if daily_text:
            text = f"{text} {daily_text}"
        return DomainEvidenceResult(
            domain="weather",
            status="confirmed",
            confidence=0.92,
            text=text,
            sources=(EvidenceSource("Open-Meteo", self._FORECAST_URL, fetched_at),),
            warnings=("weather_code_requires_interpretation",),
        )

    def _get(self, url: str, *, params: dict[str, Any]) -> dict[str, Any]:
        if self._client is not None:
            response = self._client.get(url, params=params, timeout=self._timeout)
        else:
            response = httpx.get(url, params=params, timeout=self._timeout)
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}


class CoinGeckoEvidenceProvider:
    _SEARCH_URL = "https://api.coingecko.com/api/v3/search"
    _PRICE_URL = "https://api.coingecko.com/api/v3/simple/price"

    _KNOWN_IDS = {
        "btc": "bitcoin",
        "bitcoin": "bitcoin",
        "eth": "ethereum",
        "ethereum": "ethereum",
    }

    def __init__(self, *, client: httpx.Client | None = None, timeout_seconds: float = 5.0) -> None:
        self._client = client
        self._timeout = timeout_seconds

    def get_crypto(self, *, query: str, locale: str) -> DomainEvidenceResult:
        coin_id = self._coin_id(query)
        currency = _extract_currency(query, default="eur" if (locale or "").lower().startswith("de") else "usd")
        fetched_at = _now_iso()
        if not coin_id:
            return _unavailable("crypto", "crypto_asset_not_recognized")
        try:
            payload = self._get(
                self._PRICE_URL,
                params={
                    "ids": coin_id,
                    "vs_currencies": currency,
                    "include_last_updated_at": "true",
                    "include_24hr_change": "true",
                },
            )
        except Exception as exc:
            return _unavailable("crypto", f"crypto_provider_error:{type(exc).__name__}")
        row = payload.get(coin_id)
        if not isinstance(row, dict) or row.get(currency) is None:
            return _unavailable("crypto", "crypto_price_missing")
        updated_at = row.get("last_updated_at")
        updated_text = _unix_to_iso(updated_at) if isinstance(updated_at, (int, float)) else fetched_at
        change = row.get(f"{currency}_24h_change")
        change_text = f", 24h-Veränderung {float(change):.2f}%" if isinstance(change, (int, float)) else ""
        text = f"Asset: {coin_id}. Stand: {updated_text}. Kurs: {row[currency]} {currency.upper()}{change_text}."
        return DomainEvidenceResult(
            domain="crypto",
            status="confirmed",
            confidence=0.9,
            text=text,
            sources=(EvidenceSource("CoinGecko", self._PRICE_URL, fetched_at),),
            warnings=("market_prices_are_volatile",),
        )

    def _coin_id(self, query: str) -> str:
        lowered = (query or "").lower()
        for token, coin_id in self._KNOWN_IDS.items():
            if re.search(rf"\b{re.escape(token)}\b", lowered):
                return coin_id
        return ""

    def _get(self, url: str, *, params: dict[str, Any]) -> dict[str, Any]:
        if self._client is not None:
            response = self._client.get(url, params=params, timeout=self._timeout)
        else:
            response = httpx.get(url, params=params, timeout=self._timeout)
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}


class BinanceTickerEvidenceProvider:
    _TICKER_URL = "https://api.binance.com/api/v3/ticker/24hr"
    _SYMBOLS = {
        "btc": ("BTCUSDT", "bitcoin"),
        "bitcoin": ("BTCUSDT", "bitcoin"),
        "eth": ("ETHUSDT", "ethereum"),
        "ethereum": ("ETHUSDT", "ethereum"),
    }

    def __init__(self, *, client: httpx.Client | None = None, timeout_seconds: float = 5.0) -> None:
        self._client = client
        self._timeout = timeout_seconds

    def get_crypto(self, *, query: str, locale: str) -> DomainEvidenceResult:
        symbol, asset = self._symbol(query)
        if not symbol:
            return _unavailable("crypto", "crypto_asset_not_recognized")
        requested_currency = _extract_currency(query, default="usd")
        if requested_currency not in {"usd", "usdt"}:
            return _unavailable("crypto", "crypto_fallback_supports_usd_only")
        fetched_at = _now_iso()
        try:
            payload = self._get(self._TICKER_URL, params={"symbol": symbol})
        except Exception as exc:
            return _unavailable("crypto", f"crypto_provider_error:{type(exc).__name__}")
        price = payload.get("lastPrice")
        if price is None:
            return _unavailable("crypto", "crypto_price_missing")
        try:
            price_value = float(price)
        except (TypeError, ValueError):
            return _unavailable("crypto", "crypto_price_invalid")
        if price_value <= 0:
            return _unavailable("crypto", "crypto_price_invalid")
        change = payload.get("priceChangePercent")
        price_text = f"{price_value:.2f}"
        change_text = ""
        try:
            change_text = f", 24h-Veränderung {float(change):.2f}%"
        except (TypeError, ValueError):
            pass
        text = f"Asset: {asset}. Stand: {fetched_at}. Kurs: {price_text} USD/USDT{change_text}."
        return DomainEvidenceResult(
            domain="crypto",
            status="confirmed",
            confidence=0.78,
            text=text,
            sources=(EvidenceSource("Binance", self._TICKER_URL, fetched_at),),
            warnings=("market_prices_are_volatile", "exchange_specific_usdt_pair"),
        )

    def _symbol(self, query: str) -> tuple[str, str]:
        lowered = (query or "").lower()
        for token, value in self._SYMBOLS.items():
            if re.search(rf"\b{re.escape(token)}\b", lowered):
                return value
        return "", ""

    def _get(self, url: str, *, params: dict[str, Any]) -> dict[str, Any]:
        if self._client is not None:
            response = self._client.get(url, params=params, timeout=self._timeout)
        else:
            response = httpx.get(url, params=params, timeout=self._timeout)
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}


class WttrInEvidenceProvider:
    _WEATHER_URL_TEMPLATE = "https://wttr.in/{location}"

    def __init__(self, *, client: httpx.Client | None = None, timeout_seconds: float = 5.0) -> None:
        self._client = client
        self._timeout = timeout_seconds

    def get_weather(self, *, query: str, locale: str) -> DomainEvidenceResult:
        location = _extract_location(query)
        if not location:
            return _unavailable("weather", "weather_location_missing")
        fetched_at = _now_iso()
        url = self._WEATHER_URL_TEMPLATE.format(location=location.replace(" ", "%20"))
        try:
            payload = self._get(url, params={"format": "j1", "lang": _language(locale)})
        except Exception as exc:
            return _unavailable("weather", f"weather_provider_error:{type(exc).__name__}")

        current_rows = payload.get("current_condition")
        current = current_rows[0] if isinstance(current_rows, list) and current_rows and isinstance(current_rows[0], dict) else None
        if current is None:
            return _unavailable("weather", "weather_current_missing")
        nearest_area_rows = payload.get("nearest_area")
        nearest_area = nearest_area_rows[0] if isinstance(nearest_area_rows, list) and nearest_area_rows and isinstance(nearest_area_rows[0], dict) else {}
        place = _wttr_area_name(nearest_area) or location
        temp = current.get("temp_C")
        humidity = current.get("humidity")
        precipitation = current.get("precipMM")
        wind = current.get("windspeedKmph")
        if any(value in {None, ""} for value in (temp, humidity, precipitation, wind)):
            return _unavailable("weather", "weather_current_incomplete")
        observed_at = str(current.get("localObsDateTime") or fetched_at)
        daily_text = _wttr_daily_summary(payload.get("weather"))
        text = (
            f"Ort: {place}. Stand: {observed_at}. Aktuell: Temperatur {temp} °C, "
            f"Luftfeuchte {humidity}%, Niederschlag {precipitation} mm, Wind {wind} km/h."
        )
        if daily_text:
            text = f"{text} {daily_text}"
        return DomainEvidenceResult(
            domain="weather",
            status="confirmed",
            confidence=0.72,
            text=text,
            sources=(EvidenceSource("wttr.in", url, fetched_at),),
            warnings=("weather_provider_fallback",),
        )

    def _get(self, url: str, *, params: dict[str, Any]) -> dict[str, Any]:
        if self._client is not None:
            response = self._client.get(url, params=params, timeout=self._timeout)
        else:
            response = httpx.get(url, params=params, timeout=self._timeout)
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}


def _extract_location(query: str) -> str:
    raw = " ".join((query or "").split())
    match = re.search(r"\b(?:in|für|fuer|for)\s+(.+)$", raw, re.IGNORECASE)
    if match:
        location = match.group(1)
    else:
        location = _WEATHER_RE.sub(" ", raw)
    location = re.sub(r"\b(?:heute|morgen|today|tomorrow|jetzt|aktuell|current|forecast|vorhersage|wetter|weather)\b", " ", location, flags=re.IGNORECASE)
    location = re.sub(r"[?.!,;:]+", " ", location)
    return " ".join(location.split())[:120]


def _extract_currency(query: str, *, default: str) -> str:
    match = re.search(r"\b(usd|eur|gbp|chf|jpy|cad|aud)\b", query or "", re.IGNORECASE)
    return (match.group(1).lower() if match else default.lower())


def _language(locale: str) -> str:
    normalized = (locale or "").strip().lower()
    return "de" if normalized.startswith("de") else "en"


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _unix_to_iso(value: int | float) -> str:
    return datetime.fromtimestamp(float(value), tz=UTC).replace(microsecond=0).isoformat()


def _parse_iso(value: str) -> datetime | None:
    raw = (value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _is_stale(result: DomainEvidenceResult, *, max_age_seconds: int) -> bool:
    timestamps: list[datetime] = []
    for source in result.sources:
        parsed = _parse_iso(source.fetched_at)
        if parsed is not None:
            timestamps.append(parsed)
    for match in re.finditer(r"\bStand:\s*([0-9T:+\-Z]{10,32})", result.text or "", re.IGNORECASE):
        parsed = _parse_iso(match.group(1))
        if parsed is not None:
            timestamps.append(parsed)
    if not timestamps:
        return True
    newest = max(timestamps)
    return datetime.now(UTC) - newest > timedelta(seconds=max_age_seconds)


def _value_with_unit(value: object, unit: object) -> str:
    if value is None:
        return "n/a"
    return f"{value} {unit}".strip()


def _daily_weather_summary(daily: dict[str, Any]) -> str:
    dates = daily.get("time")
    tmax = daily.get("temperature_2m_max")
    tmin = daily.get("temperature_2m_min")
    rain = daily.get("precipitation_sum")
    if not (isinstance(dates, list) and dates):
        return ""
    parts: list[str] = []
    for idx, day in enumerate(dates[:2]):
        min_value = tmin[idx] if isinstance(tmin, list) and idx < len(tmin) else "n/a"
        max_value = tmax[idx] if isinstance(tmax, list) and idx < len(tmax) else "n/a"
        rain_value = rain[idx] if isinstance(rain, list) and idx < len(rain) else "n/a"
        parts.append(f"{day}: {min_value} bis {max_value} °C, Niederschlag {rain_value} mm")
    return "Vorhersage: " + "; ".join(parts) + "."


def _wttr_area_name(area: dict[str, Any]) -> str:
    names = area.get("areaName")
    country = area.get("country")
    name = _wttr_first_value(names)
    country_name = _wttr_first_value(country)
    if name and country_name:
        return f"{name}, {country_name}"
    return name or country_name


def _wttr_first_value(rows: object) -> str:
    if not isinstance(rows, list) or not rows:
        return ""
    first = rows[0]
    if isinstance(first, dict):
        return str(first.get("value") or "").strip()
    return ""


def _wttr_daily_summary(weather_rows: object) -> str:
    if not isinstance(weather_rows, list) or not weather_rows:
        return ""
    parts: list[str] = []
    for row in weather_rows[:2]:
        if not isinstance(row, dict):
            continue
        date_value = row.get("date")
        min_value = row.get("mintempC")
        max_value = row.get("maxtempC")
        hourly = row.get("hourly")
        rain_value = ""
        if isinstance(hourly, list) and hourly and isinstance(hourly[0], dict):
            rain_value = str(hourly[0].get("precipMM") or "")
        parts.append(f"{date_value}: {min_value} bis {max_value} °C, Niederschlag {rain_value or 'n/a'} mm")
    return ("Vorhersage: " + "; ".join(parts) + ".") if parts else ""
