from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from amo_bot.ai.current_data_classifier import classify_current_data
from amo_bot.evidence_intents import is_finance_listing_query
from amo_bot.telegram import sports_query


@dataclass(frozen=True, slots=True)
class AutoResearchDecision:
    enabled: bool
    capability: str
    reason: str
    query: str
    url: str


_URL_RE = re.compile(r"https?://[^\s<>()\[\]{}\"']+", re.IGNORECASE)
_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2}|21\d{2})\b")
_DATE_RE = re.compile(r"\b\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?\b")

_CURRENT_KEYWORDS = (
    "aktuell", "heute", "jetzt", "news", "neueste", "latest", "breaking",
    "wetter", "version", "release", "preis", "price", "kurs", "stand", "update",
    "changelog", "lage", "status", "live", "current", "right now", "outage",
    "störung", "stoerung", "available", "availability", "verfügbarkeit",
    "verfuegbarkeit", "lieferbar",
)

_SPORTS_CURRENT_INTENT_RE = re.compile(
    r"\b(?:"
    r"l(?:ä|ae)uft|stehen|steht|stand|spiel(?:t|en)?|wann|wer|gegen\s+wen|"
    r"next|upcoming|schedule|score|scored|plays?|fixtures?|standings?|"
    r"result(?:s)?|table|line\s*up|lineup"
    r")\b",
    re.IGNORECASE,
)
_MARKET_CURRENT_SIGNAL_RE = re.compile(
    r"\b(?:aktie|aktien|stock|share|shares|stock\s+exchange|nasdaq|nyse|dax|etf|börse|boerse|börsennotiert|boersennotiert|listed|"
    r"publicly\s+traded|ipo|ticker|derivat|derivative|tokeni[sz]ed|perpetual|bybit|usdt|"
    r"crypto|krypto|kryptow(?:ä|ae)hrung|btc|bitcoin|eth|ethereum|sol|solana|xrp|ripple|"
    r"ada|cardano|doge|dogecoin|bnb|dot|polkadot|matic|polygon|avax|avalanche|ltc|litecoin|"
    r"[A-Z]{2,16}USDT)\b",
    re.IGNORECASE,
)
_COMMON_CRYPTO_NOUN_RE = re.compile(r"\b(?:coin|coins|token)\b", re.IGNORECASE)
_CRYPTO_ASSET_HINT_RE = re.compile(
    r"\b(?:[A-Za-z][A-Za-z0-9-]{1,24}(?:coin|token)|[A-Z0-9]{2,20}USDT)\b",
    re.IGNORECASE,
)
_CRYPTO_CONTEXT_RE = re.compile(
    r"\b(?:"
    r"btc|bitcoin|eth|ethereum|sol|solana|xrp|ripple|ada|cardano|doge|dogecoin|"
    r"crypto|krypto|kryptow(?:ä|ae)hrung|kurs|price|preis|market|exchange|blockchain|"
    r"wallet|dex|cex|usdt|bybit|tokeni[sz]ed|perpetual|derivat|derivative|trade|traden|handeln"
    r")\b",
    re.IGNORECASE,
)
_MARKET_CURRENT_INTENT_RE = re.compile(
    r"\b(?:macht|steht|stand|kurs|price|preis|current|aktuell|jetzt|now|wert|market|listing|listed|"
    r"börsennotiert|boersennotiert|derivat|derivative|kaufen|buy|trade|traden|handeln|handelbar)\b",
    re.IGNORECASE,
)
_WEATHER_INTENT_RE = re.compile(
    r"\b(?:wetter|weather|temperatur|temperature|regen|rain|forecast|vorhersage)\b",
    re.IGNORECASE,
)

_SMALLTALK_PATTERNS = (
    r"\bhallo\b", r"\bhi\b", r"\bhey\b", r"\bdanke\b", r"\bwie geht'?s\b",
    r"\bwas machst du\b", r"\bgute[nr]? morgen\b", r"\bgute[nr]? abend\b",
)

_COMPLEX_RESEARCH_RE = re.compile(
    r"\b(?:"
    r"recherchier(?:e|en)?|research|analyse|analysiere|einordnung|hintergrund|"
    r"aktuelle\s+lage|aktueller\s+stand|latest\s+developments?|"
    r"vergleich(?:e|en)?|compare|pro\s*/?\s*contra|vor[-\s]?und\s+nachteile|"
    r"was\s+spricht\s+(?:daf(?:ü|ue)r|dagegen)|"
    r"fass(?:e)?\s+.*\s+zusammen|summari[sz]e"
    r")\b",
    re.IGNORECASE,
)
_COMPLEX_SOURCE_RE = re.compile(r"\b(?:quellen|sources?|belege|evidence)\b", re.IGNORECASE)



def _sanitize_text(value: str, *, max_len: int = 180) -> str:
    compact = " ".join((value or "").split())
    if len(compact) > max_len:
        compact = compact[:max_len].rstrip() + " …"
    return compact



def decide_auto_research(text: str, *, now: datetime | None = None) -> AutoResearchDecision:
    raw = (text or "").strip()
    if not raw:
        return AutoResearchDecision(False, "", "empty", "", "")

    lowered = raw.lower()

    for pat in _SMALLTALK_PATTERNS:
        if re.search(pat, lowered):
            if not _URL_RE.search(raw):
                return AutoResearchDecision(False, "", "smalltalk", "", "")

    url_match = _URL_RE.search(raw)
    if url_match:
        url = url_match.group(0).rstrip(".,;:!?)]}'\"")
        if is_finance_listing_query(raw):
            return AutoResearchDecision(True, "websearch", "market_current_info_signal", _sanitize_text(raw, max_len=220), url)
        capability = "browser" if url.startswith("https://") else "webscraping"
        return AutoResearchDecision(True, capability, "contains_url", "", url)

    if _is_complex_research_query(raw):
        return AutoResearchDecision(True, "webresearch", "complex_research_signal", _sanitize_text(raw, max_len=220), "")

    current_year = (now or datetime.now(UTC)).year
    has_temporal = any(k in lowered for k in _CURRENT_KEYWORDS)
    has_year = bool(_YEAR_RE.search(raw))
    has_date = bool(_DATE_RE.search(raw))
    has_current_year = str(current_year) in raw
    has_sports_current_signal = bool(
        sports_query.has_competition(raw)
        and (sports_query.has_phase(raw) or sports_query.infer_need(raw) != "sport_context")
        and (_SPORTS_CURRENT_INTENT_RE.search(raw) or re.search(r"\b(?:wie|was|wann|wer|wo)\b", lowered))
    )
    has_common_crypto_noun_with_context = bool(_COMMON_CRYPTO_NOUN_RE.search(raw) and _CRYPTO_CONTEXT_RE.search(raw))
    has_market_signal = bool(
        _MARKET_CURRENT_SIGNAL_RE.search(raw)
        or _CRYPTO_ASSET_HINT_RE.search(raw)
        or has_common_crypto_noun_with_context
        or is_finance_listing_query(raw)
    )
    has_market_current_signal = bool(
        has_market_signal
        and (
            _MARKET_CURRENT_INTENT_RE.search(raw)
            or is_finance_listing_query(raw)
            or re.search(r"\b(?:wie|was)\b", lowered)
        )
    )

    if has_temporal or has_year or has_date or has_current_year or has_sports_current_signal or has_market_current_signal:
        reason = (
            "sports_current_info_signal"
            if has_sports_current_signal and not (has_temporal or has_year or has_date or has_current_year or has_market_current_signal)
            else "market_current_info_signal"
            if has_market_current_signal and not (has_temporal or has_year or has_date or has_current_year)
            else "current_info_signal"
        )
        return AutoResearchDecision(True, "websearch", reason, _sanitize_text(raw, max_len=220), "")

    if _COMMON_CRYPTO_NOUN_RE.search(raw) and not (
        _CRYPTO_ASSET_HINT_RE.search(raw) or has_common_crypto_noun_with_context or _MARKET_CURRENT_SIGNAL_RE.search(raw)
    ):
        return AutoResearchDecision(False, "", "timeless_or_unclear", "", "")

    classifier_decision = classify_current_data(
        raw,
        metadata={
            "has_year": has_year,
            "has_date": has_date,
            "has_current_year": has_current_year,
        },
    )
    if classifier_decision.should_research:
        capability = "webresearch" if _should_use_deep_research(raw, classifier_decision.signals) else "websearch"
        return AutoResearchDecision(
            True,
            capability,
            classifier_decision.reason,
            _sanitize_text(raw, max_len=220),
            "",
        )

    return AutoResearchDecision(False, "", "timeless_or_unclear", "", "")


def _is_complex_research_query(raw: str) -> bool:
    strong_complex = bool(_COMPLEX_RESEARCH_RE.search(raw))
    source_only_complex = bool(_COMPLEX_SOURCE_RE.search(raw) and len(raw) >= 80)
    if not (strong_complex or source_only_complex):
        return False
    if _WEATHER_INTENT_RE.search(raw) and len(raw) < 140:
        return False
    if _MARKET_CURRENT_SIGNAL_RE.search(raw) and _MARKET_CURRENT_INTENT_RE.search(raw) and len(raw) < 140:
        return False
    if sports_query.has_competition(raw) and _SPORTS_CURRENT_INTENT_RE.search(raw) and len(raw) < 140:
        return False
    return True


def _should_use_deep_research(raw: str, signals: tuple[str, ...]) -> bool:
    """Route broad mutable company lookups to GPT-Researcher instead of one-shot search."""

    signal_set = set(signals)
    if "lookup_intent" not in signal_set:
        return False

    broad_company_facets = {"finance_or_market", "organization_relationship", "organization_role"}
    matched_facets = signal_set & broad_company_facets
    if len(matched_facets) >= 2:
        return True

    has_list_shape = bool(re.search(r"\b(?:und|and)\b|[,;/]", raw, re.IGNORECASE))
    return has_list_shape and bool(matched_facets)
