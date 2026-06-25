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
    r"was\s+bedeutet|what\s+does|bedeutet|means?|"
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
    r"verf(?:ü|ue)gbarkeit|availability|available|stock|lieferbar|in\s+stock|"
    r"ausverkauft|sold\s+out|vorbestell(?:en|bar)|pre[-\s]?order)\b",
    re.IGNORECASE,
)
_STATUS_RE = re.compile(
    r"\b(?:status|down|st(?:ö|oe)rung(?:en)?|ausfall|outage(?:s)?|incident(?:s)?|"
    r"funktioniert|offline|online|erreichbar|läuft|laeuft|running|"
    r"statuspage|wartung|maintenance|degraded|problem(?:e)?|issue(?:s)?)\b",
    re.IGNORECASE,
)
_VERSION_RE = re.compile(
    r"\b(?:version(?:en)?|release(?:s|d)?|erschienen|drau(?:ß|ss)en|changelog|update(?:s)?|"
    r"(?:ver)?(?:ä|ae)nderung(?:en)?|change(?:s|d)?|"
    r"verf(?:ü|ue)gbar|available|published|launched|stable|beta|rc|"
    r"npm|pypi|github\s+release|release\s+notes)\b",
    re.IGNORECASE,
)
_FINANCE_MARKET_RE = re.compile(
    r"\b(?:"
    r"finanz(?:markt|e|en)?|financial(?:\s+market)?|markt(?:relevanz|umfeld)?|market(?:s|place| context)?|"
    r"b(?:ö|oe)rse|stock(?:s)?|share(?:s)?|aktie(?:n)?|equity|valuation|bewertung|"
    r"rating(?:s)?|bonit(?:ä|ae)t|anleihe(?:n)?|bond(?:s)?|credit|debt|"
    r"umsatz\w*|revenue|gewinn\w*|profit|ebit|ebitda|bilanz|earnings|quarterly|annual\s+report|"
    r"investor(?:en)?|investment|finanzierung|funding"
    r")\b",
    re.IGNORECASE,
)
_ORG_ROLE_RE = re.compile(
    r"\b(?:"
    r"ceo|cfo|cto|coo|chief\s+executive|chief\s+financial|chief\s+technology|"
    r"vorstand(?:svorsitzende[rn]?)?|geschäftsführer(?:in)?|geschaeftsfuehrer(?:in)?|"
    r"chef(?:in)?|leiter(?:in)?|president|chair(?:man|woman|person)?"
    r")\b",
    re.IGNORECASE,
)
_ORG_RELATION_RE = re.compile(
    r"\b(?:"
    r"partner(?:s|n|schaften)?|partners?|kunden?|customers?|lieferant(?:en)?|suppliers?|"
    r"zuliefer(?:er)?|kooperation(?:en)?|allianz(?:en)?|alliance(?:s)?|joint\s+venture(?:s)?|"
    r"konkurrent(?:en)?|competitor(?:s)?|wettbewerb(?:er)?|subsidiar(?:y|ies)|tochter(?:firma|gesellschaft)?"
    r")\b",
    re.IGNORECASE,
)
_SCHEDULE_RESULTS_RE = re.compile(
    r"\b(?:"
    r"spiel(?:t|en)?|match(?:es)?|game(?:s)?|fixture(?:s)?|schedule|spielplan|termin(?:e)?|"
    r"wann|when|kino|cinema|filme?|movies?|läuft|laeuft|running|"
    r"stehen|steht|stand|gruppe(?:n)?|tabelle|tabellenf(?:ü|ue)hrer|standing(?:s)?|leader|leaderboard|ergebnis(?:se)?|result(?:s)?|score(?:s)?|"
    r"umfrage(?:n)?|poll(?:s)?|prognose(?:n)?|forecast(?:s)?"
    r")\b",
    re.IGNORECASE,
)
_WEATHER_RE = re.compile(r"\b(?:wetter|weather|regen|rain|temperatur|temperature|forecast)\b", re.IGNORECASE)
_NEWS_RE = re.compile(r"\b(?:news|nachrichten|meldung(?:en)?|neu(?:es|igkeiten)|latest)\b", re.IGNORECASE)
_DOCS_OFFICIAL_RE = re.compile(
    r"\b(?:docs?|documentation|dokumentation|api\s+docs?|official|offiziell(?:e[nrms]?)?|"
    r"primary\s+source|quelle|release\s+notes|changelog)\b",
    re.IGNORECASE,
)
_LOCAL_REGION_RE = re.compile(
    r"\b(?:near|nearby|lokal|regional|region|stadt|city|"
    r"berlin|hamburg|münchen|muenchen|köln|koeln|deutschland|germany)\b",
    re.IGNORECASE,
)
_EXTERNAL_NOUN_RE = re.compile(
    r"\b(?:dienst|service|anbieter|provider|vodafone|telekom|o2|python|iphone|kino|berlin|"
    r"deutschland|germany|markt|market|produkt|product|app|website|server|"
    r"unternehmen|firma|company|organisation|organization|konzern|group|"
    r"gmbh|ag|se|kg|ohg|inc|corp|corporation|ltd|llc|plc|s\.?a\.?|sarl|nv|bv|"
    r"person|ceo|cfo|cto|vorstand|geschäftsführer|geschaeftsfuehrer|"
    r"api|sdk|library|bibliothek|package|paket|github|npm|pypi|docker|telegram\s+bot\s+api|openai|anthropic)\b",
    re.IGNORECASE,
)
_NAMED_ENTITY_RE = re.compile(
    r"\b[A-ZÄÖÜ][\w&.-]*(?:\s+[A-ZÄÖÜ][\w&.-]*){0,4}\s+"
    r"(?:GmbH|AG|SE|KG|OHG|Inc|Corp|Corporation|Ltd|LLC|PLC|S\.?A\.?|SARL|NV|BV)\b|"
    r"\b[A-Z][A-Za-z0-9&.-]*(?:\s+[A-Z][A-Za-z0-9&.-]*){0,3}\s+API\b|"
    r"\b[A-Z][a-z]+[A-Z][A-Za-z0-9&.-]*\b",
)
_ENTITY_CONTEXT_RE = re.compile(
    r"\b(?:von|bei|for|of|hat|has)\s+(?:der\s+|die\s+|das\s+)?"
    r"[A-ZÄÖÜ][A-Za-zÄÖÜäöüß0-9&.-]{2,}(?:\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß0-9&.-]{2,}){0,3}\b"
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
        if _FINANCE_MARKET_RE.search(raw):
            signals.append("finance_or_market")
        if _ORG_ROLE_RE.search(raw):
            signals.append("organization_role")
        if _ORG_RELATION_RE.search(raw):
            signals.append("organization_relationship")
        if _SCHEDULE_RESULTS_RE.search(raw):
            signals.append("schedule_results_polls")
        if _WEATHER_RE.search(raw):
            signals.append("weather")
        if _NEWS_RE.search(raw):
            signals.append("news")
        if _DOCS_OFFICIAL_RE.search(raw):
            signals.append("docs_or_official")
        if _LOCAL_REGION_RE.search(raw):
            signals.append("local_or_region")
        if _EXTERNAL_NOUN_RE.search(raw):
            signals.append("external_entity")
        if _NAMED_ENTITY_RE.search(raw) or _ENTITY_CONTEXT_RE.search(raw):
            signals.append("named_entity")
        if _QUESTION_RE.search(raw):
            signals.append("question_intent")

        signal_set = set(signals)
        external_lookup = bool(
            signal_set
            & {
                "price_or_availability",
                "service_status",
                "version_or_release",
                "finance_or_market",
                "organization_role",
                "organization_relationship",
                "schedule_results_polls",
                "weather",
                "news",
                "docs_or_official",
                "local_or_region",
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
        mutable_named_entity_question = "question_intent" in signal_set and "named_entity" in signal_set
        if "organization_role" in signal_set and mutable_named_entity_question:
            return CurrentDataDecision(
                "requires_current_data", "semantic_current_data_required", _dedupe(signals), True
            )
        if "finance_or_market" in signal_set and mutable_named_entity_question:
            return CurrentDataDecision(
                "requires_current_data", "semantic_current_data_required", _dedupe(signals), True
            )
        if "organization_relationship" in signal_set and mutable_named_entity_question:
            return CurrentDataDecision(
                "requires_current_data", "semantic_current_data_required", _dedupe(signals), True
            )
        if "schedule_results_polls" in signal_set and "temporal_current" in signal_set:
            return CurrentDataDecision(
                "requires_current_data", "semantic_current_data_required", _dedupe(signals), True
            )
        if "docs_or_official" in signal_set and (
            "version_or_release" in signal_set
            or "temporal_current" in signal_set
            or "question_intent" in signal_set
        ):
            return CurrentDataDecision(
                "requires_current_data", "semantic_current_data_required", _dedupe(signals), True
            )
        if "local_or_region" in signal_set and (
            "price_or_availability" in signal_set
            or "service_status" in signal_set
            or "schedule_results_polls" in signal_set
            or "weather" in signal_set
            or "temporal_current" in signal_set
        ):
            return CurrentDataDecision(
                "requires_current_data", "semantic_current_data_required", _dedupe(signals), True
            )

        # Timeless explanatory/creative requests may mention domains that often
        # have live data (sports, software, health) but do not need lookup unless
        # the user also asks for current status, schedules, prices, etc.
        if (
            _TIMELESS_CREATIVE_RE.search(raw)
            and "temporal_current" not in signal_set
            and not (
                "question_intent" in signal_set
                and (
                    signal_set
                    & {
                        "price_or_availability",
                        "service_status",
                        "version_or_release",
                        "news",
                        "docs_or_official",
                    }
                    or (
                        "named_entity" in signal_set
                        and signal_set
                        & {
                            "finance_or_market",
                            "organization_role",
                            "organization_relationship",
                        }
                    )
                )
            )
        ):
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
