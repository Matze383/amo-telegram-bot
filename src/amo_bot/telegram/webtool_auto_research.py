from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime


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

    if has_temporal or has_year or has_date or has_current_year:
        return AutoResearchDecision(True, "websearch", "current_info_signal", _sanitize_text(raw, max_len=220), "")

    return AutoResearchDecision(False, "", "timeless_or_unclear", "", "")
