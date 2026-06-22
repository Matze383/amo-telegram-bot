from __future__ import annotations

from dataclasses import dataclass
import re


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")
_WHITESPACE_RE = re.compile(r"\s+")
_SUBJECT_TOKEN_RE = re.compile(r"[A-Za-z0-9ÄÖÜäöüß_+-]+")
_NON_CLAIM_PREFIX_RE = re.compile(
    r"^(?:bitte|please|mach|make|build|erstelle|create|zeige|show|such|search|finde|find|"
    r"kannst du|can you|could you|würdest du|would you)\b",
    re.IGNORECASE,
)
_QUESTION_PREFIX_RE = re.compile(
    r"^(?:was|wie|wer|wann|wo|warum|wieso|welche[rsn]?|what|why|who|when|where|how|is|are|do|does|did)\b",
    re.IGNORECASE,
)

_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "das",
    "der",
    "die",
    "ein",
    "eine",
    "for",
    "i",
    "ich",
    "ist",
    "mit",
    "of",
    "oder",
    "sind",
    "the",
    "und",
    "was",
    "what",
    "with",
}


@dataclass(frozen=True, slots=True)
class ExtractedClaim:
    text: str
    normalized_subject: str
    confidence: float


def extract_claims(text: str, *, max_claims: int = 5) -> tuple[ExtractedClaim, ...]:
    """Extract compact factual-looking statements without assigning truth."""

    safe_limit = max(1, min(int(max_claims), 20))
    result: list[ExtractedClaim] = []
    seen: set[str] = set()
    for raw_sentence in _SENTENCE_SPLIT_RE.split(text or ""):
        sentence = _clean_claim_text(raw_sentence)
        if not sentence or not _looks_like_claim(sentence):
            continue
        dedupe_key = sentence.casefold()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        result.append(
            ExtractedClaim(
                text=sentence,
                normalized_subject=_normalize_subject(sentence),
                confidence=_initial_confidence(sentence),
            )
        )
        if len(result) >= safe_limit:
            break
    return tuple(result)


def _clean_claim_text(value: str) -> str:
    cleaned = _WHITESPACE_RE.sub(" ", (value or "").strip())
    return cleaned.strip(" \t\r\n\"'")


def _looks_like_claim(sentence: str) -> bool:
    if len(sentence) < 8:
        return False
    if sentence.endswith("?"):
        return False
    if _QUESTION_PREFIX_RE.search(sentence) or _NON_CLAIM_PREFIX_RE.search(sentence):
        return False
    lower = sentence.casefold()
    return any(
        marker in lower
        for marker in (
            " ist ",
            " sind ",
            " war ",
            " waren ",
            " bleibt ",
            " hat ",
            " haben ",
            " is ",
            " are ",
            " was ",
            " were ",
            " has ",
            " have ",
            " will ",
        )
    )


def _normalize_subject(sentence: str) -> str:
    tokens = [
        token.casefold()
        for token in _SUBJECT_TOKEN_RE.findall(sentence)
        if len(token) > 1 and token.casefold() not in _STOPWORDS
    ]
    return " ".join(tokens[:8])


def _initial_confidence(sentence: str) -> float:
    words = _SUBJECT_TOKEN_RE.findall(sentence)
    if len(words) < 4:
        return 0.35
    if len(words) > 24:
        return 0.5
    return 0.6
