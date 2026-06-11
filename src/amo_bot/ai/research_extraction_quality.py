from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ExtractionQuality:
    usable: bool
    warning_codes: tuple[str, ...] = ()
    text_length: int = 0
    text_length_bucket: str = "zero"


_ERROR_PAGE_RE = re.compile(
    r"\b(?:"
    r"404|403|500|502|503|not\s+found|page\s+not\s+found|access\s+denied|forbidden|"
    r"service\s+unavailable|bad\s+gateway|captcha|cloudflare|just\s+a\s+moment|"
    r"seite\s+nicht\s+gefunden|zugriff\s+verweigert|nicht\s+verf(?:ü|ue)gbar"
    r")\b",
    re.IGNORECASE,
)
_JS_PLACEHOLDER_RE = re.compile(
    r"\b(?:"
    r"enable\s+javascript|javascript\s+(?:is\s+)?(?:required|disabled)|"
    r"requires\s+javascript|please\s+enable\s+js|"
    r"aktiviere(?:n)?\s+sie\s+javascript|javascript\s+aktivieren|"
    r"loading\s*(?:\.\.\.)?|app\s+is\s+loading|root\s+element|__next_data__"
    r")\b",
    re.IGNORECASE,
)
_SNIPPET_ONLY_RE = re.compile(
    r"(?:^|\s)(?:\d+\.\s+\S.{0,80}:\s+|\.\.\.|…|read\s+more|weiterlesen)(?:\s|$)",
    re.IGNORECASE,
)
_CONFLICT_RE = re.compile(
    r"\b(?:conflicting|contradictory|widerspr(?:ü|ue)chlich|unconfirmed|not\s+confirmed|"
    r"rumou?r|speculation|unclear|unklar|nicht\s+best(?:ä|ae)tigt)\b",
    re.IGNORECASE,
)


def classify_extraction_quality(text: str, *, min_chars: int = 40) -> ExtractionQuality:
    compact = " ".join((text or "").split())
    text_length = len(compact)
    warnings: list[str] = []

    if text_length <= 0:
        warnings.append("extraction_empty_text")
    elif text_length < min_chars:
        warnings.append("extraction_too_short")

    lowered = compact.lower()
    if text_length and _ERROR_PAGE_RE.search(compact):
        if text_length < 500 or _mostly_boilerplate(lowered):
            warnings.append("extraction_error_page")
    if text_length and _JS_PLACEHOLDER_RE.search(compact):
        if text_length < 700 or _mostly_boilerplate(lowered):
            warnings.append("extraction_js_placeholder")
    if text_length and _SNIPPET_ONLY_RE.search(compact) and text_length < 260:
        warnings.append("extraction_snippet_like")
    if text_length and _CONFLICT_RE.search(compact):
        warnings.append("extraction_conflicting_or_unconfirmed")

    warning_codes = tuple(dict.fromkeys(warnings))
    return ExtractionQuality(
        usable=not warning_codes,
        warning_codes=warning_codes,
        text_length=text_length,
        text_length_bucket=extraction_length_bucket(text_length, min_chars=min_chars),
    )


def extraction_length_bucket(length: int, *, min_chars: int = 40) -> str:
    if length <= 0:
        return "zero"
    if length < min_chars:
        return "short"
    if length < 500:
        return "usable_small"
    if length < 1500:
        return "usable_medium"
    return "usable_large"


def _mostly_boilerplate(lowered: str) -> bool:
    if not lowered:
        return False
    markers = (
        "error",
        "not found",
        "forbidden",
        "access denied",
        "javascript",
        "loading",
        "cloudflare",
        "captcha",
        "nicht gefunden",
        "zugriff",
    )
    return sum(1 for marker in markers if marker in lowered) >= 2
