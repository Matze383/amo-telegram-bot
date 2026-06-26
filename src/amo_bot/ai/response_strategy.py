from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from amo_bot.ai.current_data_classifier import CurrentDataDecision, classify_current_data

ResponseStrategyLabel = Literal["direct_answer", "research_needed", "clarify"]


@dataclass(frozen=True, slots=True)
class ResponseStrategy:
    label: ResponseStrategyLabel
    reason: str
    signals: tuple[str, ...] = ()
    current_data_decision: CurrentDataDecision | None = None

    @property
    def requires_research(self) -> bool:
        return self.label == "research_needed"


_VAGUE_REFERENCE_RE = re.compile(
    r"^\s*(?:"
    r"(?:was|wie|warum|wann|wo|wer)\s+(?:ist|sind|war|waren|macht|machen)\s+"
    r"(?:das|dies|dieser|diese|dieses|that|this|it)\??|"
    r"(?:erkl(?:ä|ae)r(?:e)?|explain)\s+(?:das|dies|that|this|it)\??|"
    r"(?:mach|do)\s+(?:das|that|this)\??"
    r")\s*$",
    re.IGNORECASE,
)

_DRAFT_SELF_LIMITATION_RE = re.compile(
    r"\b(?:"
    r"keine\s+(?:live[-\s]?daten|aktuellen\s+daten|echtzeitdaten)|"
    r"nicht\s+aktuell\s+abrufen|"
    r"kann\s+(?:ich\s+)?(?:nicht\s+)?(?:aktuell|live|in\s+echtzeit)\s+abrufen|"
    r"mein(?:em|er)?\s+wissensstand|"
    r"wissensstand\s+(?:ist|reicht|endet)|"
    r"trainingsdaten|"
    r"sofern\s+ich\s+(?:das\s+)?rekonstruieren\s+kann|"
    r"no\s+live\s+data|"
    r"can't\s+(?:access|browse|retrieve)\s+(?:live|current|real[-\s]?time)|"
    r"knowledge\s+cutoff|"
    r"training\s+data"
    r")\b",
    re.IGNORECASE,
)


def classify_response_strategy(
    message: str,
    *,
    context: dict[str, object] | None = None,
) -> ResponseStrategy:
    """Choose the response path before answer synthesis.

    The strategy is intentionally deterministic and conservative. Mutable
    outside-world facts go through current-info/research; stable explanations
    and creative/local requests stay on the direct answer path.
    """

    raw = (message or "").strip()
    if not raw:
        return ResponseStrategy("clarify", "empty_message")

    if _VAGUE_REFERENCE_RE.search(raw):
        return ResponseStrategy("clarify", "vague_reference")

    current_data_decision = classify_current_data(raw, metadata=context)
    if current_data_decision.should_research:
        return ResponseStrategy(
            "research_needed",
            current_data_decision.reason,
            current_data_decision.signals,
            current_data_decision,
        )

    return ResponseStrategy(
        "direct_answer",
        current_data_decision.reason,
        current_data_decision.signals,
        current_data_decision,
    )


def draft_self_limitation_requires_research(*, message: str, draft: str) -> bool:
    """Return True when a normal draft admits it lacks live data for a factual external request."""

    if not (draft or "").strip() or not _DRAFT_SELF_LIMITATION_RE.search(draft):
        return False

    strategy = classify_response_strategy(message)
    return strategy.requires_research
