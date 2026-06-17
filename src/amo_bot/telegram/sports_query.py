from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True, slots=True)
class SportsAlias:
    canonical: str
    aliases: tuple[str, ...]


COMPETITION_ALIASES: tuple[SportsAlias, ...] = (
    SportsAlias(
        canonical="world cup",
        aliases=(
            "wm",
            "fussball wm",
            "fußball wm",
            "fifa wm",
            "fifa world cup",
            "fifa weltmeisterschaft",
            "weltmeisterschaft",
            "world cup",
        ),
    ),
    SportsAlias(
        canonical="euro",
        aliases=("em", "europameisterschaft", "euro", "uefa euro"),
    ),
    SportsAlias(
        canonical="champions league",
        aliases=("champions league", "uefa champions league"),
    ),
    SportsAlias(
        canonical="europa league",
        aliases=("europa league", "uefa europa league"),
    ),
    SportsAlias(canonical="bundesliga", aliases=("bundesliga",)),
    SportsAlias(canonical="premier league", aliases=("premier league",)),
    SportsAlias(canonical="la liga", aliases=("la liga",)),
    SportsAlias(canonical="serie a", aliases=("serie a",)),
    SportsAlias(canonical="dfb pokal", aliases=("dfb pokal", "dfb-pokal")),
    SportsAlias(canonical="nba", aliases=("nba",)),
    SportsAlias(canonical="nfl", aliases=("nfl",)),
    SportsAlias(canonical="nhl", aliases=("nhl",)),
    SportsAlias(canonical="mlb", aliases=("mlb",)),
    SportsAlias(canonical="formula 1", aliases=("formel 1", "formel1", "formula 1", "f1")),
)

PHASE_ALIASES: tuple[SportsAlias, ...] = (
    SportsAlias(canonical="group stage", aliases=("vorrunde", "gruppenphase", "group stage")),
    SportsAlias(canonical="group", aliases=("gruppe", "gruppen", "group", "groups")),
    SportsAlias(canonical="qualifying", aliases=("qualifikation", "qualifying", "qualifier")),
    SportsAlias(canonical="round", aliases=("runde", "round")),
    SportsAlias(canonical="semifinal", aliases=("halbfinale", "semifinal", "semi final")),
    SportsAlias(canonical="final", aliases=("finale", "final")),
)

NEED_ALIASES: dict[str, tuple[SportsAlias, ...]] = {
    "sport_schedule": (
        SportsAlias(canonical="schedule", aliases=("spielplan", "schedule", "termine", "fixtures", "fixture")),
        SportsAlias(canonical="matches", aliases=("matches", "match", "partien", "spiele")),
    ),
    "sport_table": (
        SportsAlias(canonical="standings", aliases=("tabelle", "standings", "standing", "rangliste")),
        SportsAlias(canonical="points", aliases=("punkte", "points")),
    ),
    "sport_result": (
        SportsAlias(canonical="result", aliases=("ergebnis", "ergebnisse", "result", "results")),
        SportsAlias(canonical="score", aliases=("score", "scores", "stand", "live")),
    ),
}
RESULT_CONTEXT_ALIASES: tuple[SportsAlias, ...] = (
    SportsAlias(canonical="vs", aliases=("gegen", "gegen wen", "vs", "versus")),
    SportsAlias(canonical="match", aliases=("match", "matches", "spiel", "spiele", "partie", "partien")),
)

SPORT_GENERAL_ALIASES: tuple[SportsAlias, ...] = (
    SportsAlias(canonical="football", aliases=("fußball", "fussball", "football", "soccer")),
    SportsAlias(canonical="uefa", aliases=("uefa",)),
    SportsAlias(canonical="fifa", aliases=("fifa",)),
)

TEAM_NAME_ALIASES: tuple[SportsAlias, ...] = (
    SportsAlias(canonical="Brazil", aliases=("brasilien",)),
    SportsAlias(canonical="Germany", aliases=("deutschland",)),
    SportsAlias(canonical="Spain", aliases=("spanien",)),
    SportsAlias(canonical="France", aliases=("frankreich",)),
    SportsAlias(canonical="Italy", aliases=("italien",)),
    SportsAlias(canonical="England", aliases=("england",)),
    SportsAlias(canonical="Argentina", aliases=("argentinien",)),
    SportsAlias(canonical="Netherlands", aliases=("niederlande",)),
    SportsAlias(canonical="Portugal", aliases=("portugal",)),
    SportsAlias(canonical="Uruguay", aliases=("uruguay",)),
    SportsAlias(canonical="Belgium", aliases=("belgien",)),
    SportsAlias(canonical="Croatia", aliases=("kroatien",)),
    SportsAlias(canonical="Switzerland", aliases=("schweiz",)),
    SportsAlias(canonical="Denmark", aliases=("dänemark", "daenemark")),
)

_YEAR_RE = re.compile(r"(?<!\d)(?:19\d{2}|20\d{2}|21\d{2})(?!\d)")
_WORD_BOUNDARY_LEFT = r"(?<!\w)"
_WORD_BOUNDARY_RIGHT = r"(?!\w)"


def alias_pattern(aliases: Iterable[SportsAlias]) -> re.Pattern[str]:
    tokens: list[str] = []
    for entry in aliases:
        for alias in _match_terms(entry):
            tokens.append(_alias_to_pattern(alias))
    tokens.sort(key=len, reverse=True)
    return re.compile(rf"{_WORD_BOUNDARY_LEFT}(?:{'|'.join(tokens)}){_WORD_BOUNDARY_RIGHT}", re.IGNORECASE)


def has_competition(text: str) -> bool:
    return bool(_COMPETITION_RE.search(text or ""))


def has_phase(text: str) -> bool:
    return bool(_PHASE_RE.search(text or ""))


def has_sports_signal(text: str) -> bool:
    raw = text or ""
    return bool(_SPORTS_SIGNAL_RE.search(raw))


def has_result_context(text: str) -> bool:
    raw = text or ""
    return infer_need(raw) == "sport_result" or bool(_RESULT_CONTEXT_RE.search(raw))


def infer_need(text: str) -> str:
    raw = text or ""
    if _TABLE_RE.search(raw):
        return "sport_table"
    if _SCHEDULE_RE.search(raw):
        return "sport_schedule"
    if _RESULT_RE.search(raw):
        return "sport_result"
    return "sport_context"


def normalize_search_terms(text: str) -> str:
    value = text or ""
    for alias_group in (
        COMPETITION_ALIASES,
        PHASE_ALIASES,
        NEED_ALIASES["sport_schedule"],
        NEED_ALIASES["sport_table"],
        NEED_ALIASES["sport_result"],
        RESULT_CONTEXT_ALIASES,
        TEAM_NAME_ALIASES,
    ):
        value = _replace_aliases(value, alias_group)
    value = re.sub(r"(?<!\w)heute(?!\w)", "today", value, flags=re.IGNORECASE)
    value = re.sub(r"(?<!\w)aktuell(?:e[nrms]?)?(?!\w)", "current", value, flags=re.IGNORECASE)
    value = re.sub(r"(?<!\w)gegen(?!\w)", "vs", value, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", value).strip(" -–—:;,.!?")


def query_terms(text: str) -> dict[str, object]:
    raw = text or ""
    return {
        "competition": _first_canonical(raw, COMPETITION_ALIASES),
        "year": _first_year(raw),
        "phase": _first_canonical(raw, PHASE_ALIASES),
        "need": infer_need(raw),
    }


def first_team(text: str) -> str | None:
    return _first_canonical(text or "", TEAM_NAME_ALIASES)


def matching_teams(text: str) -> tuple[str, ...]:
    raw = text or ""
    teams: list[str] = []
    for entry in TEAM_NAME_ALIASES:
        if alias_pattern((entry,)).search(raw):
            teams.append(entry.canonical)
    return tuple(teams)


def _replace_aliases(text: str, aliases: Iterable[SportsAlias]) -> str:
    value = text
    for entry in aliases:
        for alias in sorted(_match_terms(entry), key=len, reverse=True):
            value = re.sub(
                rf"{_WORD_BOUNDARY_LEFT}{_alias_to_pattern(alias)}{_WORD_BOUNDARY_RIGHT}",
                entry.canonical,
                value,
                flags=re.IGNORECASE,
            )
    return value


def _first_canonical(text: str, aliases: Iterable[SportsAlias]) -> str | None:
    for entry in aliases:
        if alias_pattern((entry,)).search(text or ""):
            return entry.canonical
    return None


def _first_year(text: str) -> int | None:
    match = _YEAR_RE.search(text or "")
    return int(match.group(0)) if match else None


def _alias_to_pattern(alias: str) -> str:
    parts = re.split(r"[-\s]+", alias.strip())
    return r"[-\s]*".join(re.escape(part) for part in parts if part)


def _match_terms(entry: SportsAlias) -> tuple[str, ...]:
    return (entry.canonical, *entry.aliases)


_COMPETITION_RE = alias_pattern(COMPETITION_ALIASES)
_PHASE_RE = alias_pattern(PHASE_ALIASES)
_SCHEDULE_RE = alias_pattern(NEED_ALIASES["sport_schedule"])
_TABLE_RE = alias_pattern(NEED_ALIASES["sport_table"])
_RESULT_RE = alias_pattern(NEED_ALIASES["sport_result"])
_RESULT_CONTEXT_RE = alias_pattern(RESULT_CONTEXT_ALIASES)
_SPORTS_SIGNAL_RE = alias_pattern(
    (
        *COMPETITION_ALIASES,
        *PHASE_ALIASES,
        *NEED_ALIASES["sport_schedule"],
        *NEED_ALIASES["sport_table"],
        *NEED_ALIASES["sport_result"],
        *RESULT_CONTEXT_ALIASES,
        *SPORT_GENERAL_ALIASES,
    )
)
