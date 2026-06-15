from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from amo_bot.ai.current_data_classifier import classify_current_data
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
    "changelog", "lage", "status", "live", "current", "right now",
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
    r"\b(?:aktie|stock|share|shares|nasdaq|nyse|dax|etf|nvidia|nvda|tesla|tsla|apple|aapl|microsoft|msft)\b",
    re.IGNORECASE,
)
_MARKET_CURRENT_INTENT_RE = re.compile(
    r"\b(?:macht|steht|stand|kurs|price|preis|current|aktuell|jetzt|now|wert|market)\b",
    re.IGNORECASE,
)

_SMALLTALK_PATTERNS = (
    r"\bhallo\b", r"\bhi\b", r"\bhey\b", r"\bdanke\b", r"\bwie geht'?s\b",
    r"\bwas machst du\b", r"\bgute[nr]? morgen\b", r"\bgute[nr]? abend\b",
)



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
        url = _sanitize_text(url_match.group(0), max_len=240)
        capability = "browser" if url.startswith("https://") else "webscraping"
        return AutoResearchDecision(True, capability, "contains_url", "", url)

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
    has_market_current_signal = bool(
        _MARKET_CURRENT_SIGNAL_RE.search(raw)
        and (_MARKET_CURRENT_INTENT_RE.search(raw) or re.search(r"\b(?:wie|was)\b", lowered))
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

    classifier_decision = classify_current_data(
        raw,
        metadata={
            "has_year": has_year,
            "has_date": has_date,
            "has_current_year": has_current_year,
        },
    )
    if classifier_decision.should_research:
        return AutoResearchDecision(
            True,
            "websearch",
            classifier_decision.reason,
            _sanitize_text(raw, max_len=220),
            "",
        )

    return AutoResearchDecision(False, "", "timeless_or_unclear", "", "")
