from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Protocol

CurrentDataLabel = Literal[
    "requires_current_data",
    "does_not_require_current_data",
    "uncertain",
]


@dataclass(frozen=True, slots=True)
class CurrentDataDecision:
    """Metadata-safe decision for whether a message needs live external data.

    Reasons and signal names are intentionally enum-like strings so callers can
    log them without exposing raw user text, prompts, snippets, URLs, or memory.
    """

    label: CurrentDataLabel
    reason: str
    signals: tuple[str, ...] = ()
    external_lookup_signal: bool = False

    @property
    def should_research(self) -> bool:
        return self.label == "requires_current_data" or (
            self.label == "uncertain" and self.external_lookup_signal
        )


class CurrentDataClassifier(Protocol):
    def classify(self, text: str, *, metadata: dict[str, object] | None = None) -> CurrentDataDecision:
        ...


_QUESTION_RE = re.compile(
    r"(?:\?|\b(?:wer|was|wann|wo|wie|welche(?:r|s|n)?|ist|gibt|kostet|spielt|läuft|laeuft|"
    r"who|what|when|where|which|is|are|does|do|costs?|plays?|running|available)\b)",
    re.IGNORECASE,
)
_TIMELESS_CREATIVE_RE = re.compile(
    r"\b(?:"
    r"erkl(?:ä|ae)r(?:e|en)?|explain|was\s+ist|what\s+is|warum|why|wie\s+kann|how\s+can|"
    r"schreib(?:e)?|write|geschichte|story|märchen|maerchen|poem|gedicht|"
    r"definition|grundlagen|basics|tutorial|concept|konzept"
    r")\b",
    re.IGNORECASE,
)
_SMALLTALK_RE = re.compile(
    r"^\s*(?:hallo|hi|hey|danke|thanks|thank\s+you|gute[nr]?\s+(?:morgen|abend|nacht)|"
    r"wie\s+geht'?s|how\s+are\s+you)\b",
    re.IGNORECASE,
)

_TEMPORAL_CURRENT_RE = re.compile(
    r"\b(?:"
    r"aktuell(?:e[nrms]?)?|current(?:ly)?|jetzt|gerade|derzeit|heute|morgen|"
    r"latest|neueste(?:n|r|s)?|newest|breaking|live|right\s+now|recent|recently|"
    r"vor\s+kurzem|kürzlich|kuerzlich|diese(?:n|r|s)?\s+(?:woche|monat|jahr)|"
    r"n(?:ä|ae)chste(?:s|n|r)?\s+mal|next\s+(?:time|match|game|week|month)|"
    r"schon\s+(?:drau(?:ß|ss)en|verf(?:ü|ue)gbar|erschienen)|"
    r"still|noch\s+aktuell"
    r")\b",
    re.IGNORECASE,
)
_PRICE_RE = re.compile(
    r"\b(?:kostet|preis(?:e)?|price(?:s)?|kurs(?:e)?|rate(?:s)?|tarif(?:e)?|angebot(?:e)?|"
    r"verf(?:ü|ue)gbarkeit|availability|available|stock|lieferbar|in\s+stock)\b",
    re.IGNORECASE,
)
_STATUS_RE = re.compile(
    r"\b(?:status|down|st(?:ö|oe)rung(?:en)?|ausfall|outage(?:s)?|incident(?:s)?|"
    r"funktioniert|offline|online|erreichbar|läuft|laeuft|running)\b",
    re.IGNORECASE,
)
_VERSION_RE = re.compile(
    r"\b(?:version(?:en)?|release(?:s|d)?|erschienen|drau(?:ß|ss)en|changelog|update(?:s)?|"
    r"verf(?:ü|ue)gbar|available|published|launched)\b",
    re.IGNORECASE,
)
_SCHEDULE_RESULTS_RE = re.compile(
    r"\b(?:"
    r"spiel(?:t|en)?|match(?:es)?|game(?:s)?|fixture(?:s)?|schedule|spielplan|termin(?:e)?|"
    r"wann|when|kino|cinema|filme?|movies?|läuft|laeuft|running|"
    r"tabelle|tabellenf(?:ü|ue)hrer|standing(?:s)?|leader|leaderboard|ergebnis(?:se)?|result(?:s)?|score(?:s)?|"
    r"umfrage(?:n)?|poll(?:s)?|prognose(?:n)?|forecast(?:s)?"
    r")\b",
    re.IGNORECASE,
)
_WEATHER_RE = re.compile(r"\b(?:wetter|weather|regen|rain|temperatur|temperature|forecast)\b", re.IGNORECASE)
_NEWS_RE = re.compile(r"\b(?:news|nachrichten|meldung(?:en)?|neu(?:es|igkeiten)|latest)\b", re.IGNORECASE)
_EXTERNAL_NOUN_RE = re.compile(
    r"\b(?:dienst|service|anbieter|provider|vodafone|telekom|o2|python|iphone|kino|berlin|"
    r"deutschland|germany|markt|market|produkt|product|app|website|server)\b",
    re.IGNORECASE,
)


def _dedupe(values: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return tuple(result)


class HeuristicCurrentDataClassifier:
    """Bounded semantic fallback for current/external-data routing.

    This is deliberately category-based rather than a growing list of one-off
    trigger words. A provider-backed classifier can implement the same Protocol
    later and be injected without changing webtool routing semantics.
    """

    def classify(self, text: str, *, metadata: dict[str, object] | None = None) -> CurrentDataDecision:
        raw = (text or "").strip()
        if not raw:
            return CurrentDataDecision("does_not_require_current_data", "empty")

        if _SMALLTALK_RE.search(raw) and not _QUESTION_RE.search(raw):
            return CurrentDataDecision("does_not_require_current_data", "timeless_smalltalk")

        signals: list[str] = []
        if _TEMPORAL_CURRENT_RE.search(raw):
            signals.append("temporal_current")
        if _PRICE_RE.search(raw):
            signals.append("price_or_availability")
        if _STATUS_RE.search(raw):
            signals.append("service_status")
        if _VERSION_RE.search(raw):
            signals.append("version_or_release")
        if _SCHEDULE_RESULTS_RE.search(raw):
            signals.append("schedule_results_polls")
        if _WEATHER_RE.search(raw):
            signals.append("weather")
        if _NEWS_RE.search(raw):
            signals.append("news")
        if _EXTERNAL_NOUN_RE.search(raw):
            signals.append("external_entity")
        if _QUESTION_RE.search(raw):
            signals.append("question_intent")

        signal_set = set(signals)
        external_lookup = bool(
            signal_set
            & {
                "price_or_availability",
                "service_status",
                "version_or_release",
                "schedule_results_polls",
                "weather",
                "news",
                "external_entity",
            }
        )

        if "weather" in signal_set:
            return CurrentDataDecision(
                "requires_current_data", "semantic_current_data_required", _dedupe(signals), True
            )
        if "temporal_current" in signal_set and external_lookup:
            return CurrentDataDecision(
                "requires_current_data", "semantic_current_data_required", _dedupe(signals), True
            )
        if "service_status" in signal_set and ("question_intent" in signal_set or "external_entity" in signal_set):
            return CurrentDataDecision(
                "requires_current_data", "semantic_current_data_required", _dedupe(signals), True
            )
        if "version_or_release" in signal_set and ("question_intent" in signal_set or "external_entity" in signal_set):
            return CurrentDataDecision(
                "requires_current_data", "semantic_current_data_required", _dedupe(signals), True
            )
        if "schedule_results_polls" in signal_set and "temporal_current" in signal_set:
            return CurrentDataDecision(
                "requires_current_data", "semantic_current_data_required", _dedupe(signals), True
            )

        # Timeless explanatory/creative requests may mention domains that often
        # have live data (sports, software, health) but do not need lookup unless
        # the user also asks for current status, schedules, prices, etc.
        if _TIMELESS_CREATIVE_RE.search(raw) and "temporal_current" not in signal_set:
            return CurrentDataDecision("does_not_require_current_data", "timeless_explanatory", _dedupe(signals), False)

        if external_lookup and "question_intent" in signal_set:
            return CurrentDataDecision("uncertain", "semantic_uncertain_external_lookup", _dedupe(signals), True)

        return CurrentDataDecision("does_not_require_current_data", "timeless_or_personal", _dedupe(signals), False)


DEFAULT_CURRENT_DATA_CLASSIFIER = HeuristicCurrentDataClassifier()


def classify_current_data(
    text: str,
    *,
    metadata: dict[str, object] | None = None,
    classifier: CurrentDataClassifier | None = None,
) -> CurrentDataDecision:
    active = classifier or DEFAULT_CURRENT_DATA_CLASSIFIER
    return active.classify(text, metadata=metadata)
