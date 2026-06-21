from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import re
from typing import Any

from amo_bot.ai.router import AIRouterContextV1


_QUESTION_RE = re.compile(r"\?|^(?:was|wie|wer|wann|wo|warum|wieso|welche[rsn]?|what|why|who|when|where|how)\b", re.IGNORECASE)
_ACTION_RE = re.compile(r"^(?:bitte|please|mach|make|build|erstelle|create|zeige|show|such|search|finde|find)\b", re.IGNORECASE)
_MENTION_RE = re.compile(r"@\w+")
_TOKEN_RE = re.compile(r"[A-Za-z0-9ÄÖÜäöüß_+-]+")

_STOPWORDS = {
    "amo",
    "amobot",
    "bot",
    "bitte",
    "please",
    "was",
    "wie",
    "wer",
    "wann",
    "wo",
    "warum",
    "wieso",
    "what",
    "why",
    "who",
    "when",
    "where",
    "how",
    "ist",
    "sind",
    "der",
    "die",
    "das",
    "den",
    "dem",
    "ein",
    "eine",
    "und",
    "oder",
    "von",
    "the",
    "and",
    "for",
    "with",
}


@dataclass(frozen=True, slots=True)
class ContextFrameCandidate:
    frame: str
    source: str
    evidence_count: int
    confidence: str


@dataclass(frozen=True, slots=True)
class ContextConflict:
    conflict_type: str
    frames: tuple[str, ...]
    description: str


@dataclass(frozen=True, slots=True)
class ContextSnapshotV1:
    schema_version: str
    current_user_intent: str
    active_subject: str
    frame_candidates: tuple[ContextFrameCandidate, ...]
    source_classes: dict[str, str]
    relevant_assumptions: tuple[str, ...]
    conflicts: tuple[ContextConflict, ...]
    uncertainty: tuple[str, ...]
    requires_current_info: bool
    context_source_counts: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_prompt_text(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, indent=2)


def build_context_snapshot(
    *,
    current_message: str,
    router_context: AIRouterContextV1,
    reply_context_text: str = "",
    normalized_current_message: str | None = None,
    existing_current_info_signal: bool | None = None,
) -> ContextSnapshotV1:
    """Build a deterministic runtime snapshot for diagnostic autoreply context.

    The snapshot intentionally avoids deciding semantic domains from static word
    lists. Frame candidates describe available context sources; richer frame and
    freshness resolution belongs to the existing router/research flow or a
    future resolver.
    """
    normalized_current = (normalized_current_message or current_message or "").strip()
    current = _clean_text(normalized_current)
    sources = {
        "current_message": current,
        "reply_context": _clean_text(reply_context_text),
        "recent_messages": _clean_text(router_context.recent_messages_text),
        "daily_memory": _clean_text(router_context.daily_memory_text),
        "long_memory": _clean_text(router_context.long_memory_text),
        "retrieved_memory": _clean_text(router_context.recall_memory_text),
        "user_profile": _clean_text(router_context.user_profile_context_text),
        "prompt_context_docs": _clean_text(router_context.prompt_context_docs_text),
    }

    frames = _build_frame_candidates(sources=sources)
    conflicts = _detect_conflicts(current=current, sources=sources, frame_candidates=frames)
    uncertainty = _detect_uncertainty(
        router_context=router_context,
        conflicts=conflicts,
        frame_candidates=frames,
        existing_current_info_signal=existing_current_info_signal,
    )

    return ContextSnapshotV1(
        schema_version="context_snapshot_v1",
        current_user_intent=_detect_intent(current),
        active_subject=_extract_active_subject(current),
        frame_candidates=tuple(frames),
        source_classes=_build_source_classes(sources=sources),
        relevant_assumptions=tuple(_build_assumptions(router_context=router_context, sources=sources)),
        conflicts=tuple(conflicts),
        uncertainty=tuple(uncertainty),
        requires_current_info=bool(existing_current_info_signal),
        context_source_counts={key: _line_count(value) for key, value in sources.items()},
    )


def _clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _line_count(value: str) -> int:
    if not value:
        return 0
    return max(1, len([line for line in value.splitlines() if line.strip()]))


def _detect_intent(current: str) -> str:
    if not current:
        return "unknown"
    if _QUESTION_RE.search(current):
        return "answer_question"
    if _ACTION_RE.search(current):
        return "perform_requested_action"
    return "continue_conversation"


def _extract_active_subject(current: str) -> str:
    without_mentions = _MENTION_RE.sub(" ", current)
    tokens = [
        token
        for token in _TOKEN_RE.findall(without_mentions)
        if len(token) > 2 and token.casefold() not in _STOPWORDS
    ]
    return " ".join(tokens[:8])


def _build_frame_candidates(*, sources: dict[str, str]) -> list[ContextFrameCandidate]:
    frames: list[ContextFrameCandidate] = []
    if sources.get("current_message"):
        frames.append(
            ContextFrameCandidate(
                frame="current_turn",
                source="current_message",
                evidence_count=_line_count(sources["current_message"]),
                confidence="high",
            )
        )
    source_frames = (
        ("reply_context", "telegram_reply_context", "high"),
        ("recent_messages", "recent_chat_context", "medium"),
        ("daily_memory", "daily_memory_context", "low"),
        ("long_memory", "long_memory_context", "low"),
        ("retrieved_memory", "retrieved_memory_context", "medium"),
        ("user_profile", "user_profile_context", "low"),
        ("prompt_context_docs", "operator_context_notes", "medium"),
    )
    for source_key, frame, confidence in source_frames:
        value = sources.get(source_key, "")
        if not value:
            continue
        frames.append(
            ContextFrameCandidate(
                frame=frame,
                source=source_key,
                evidence_count=_line_count(value),
                confidence=confidence,
            )
        )
    if not frames:
        frames.append(ContextFrameCandidate(frame="open_conversation", source="current_message", evidence_count=0, confidence="low"))
    return frames


def _build_source_classes(*, sources: dict[str, str]) -> dict[str, str]:
    """Expose synthesis trust classes without turning context into facts."""
    configured = {
        "current_message": "user_claim",
        "reply_context": _reply_context_source_class(sources.get("reply_context", "")),
        "recent_messages": "user_claim",
        "daily_memory": "topic_summary",
        "long_memory": "semantic_memory",
        "retrieved_memory": "semantic_memory",
        "user_profile": "semantic_memory",
        "prompt_context_docs": "model_prior",
    }
    return {source: source_class for source, source_class in configured.items() if sources.get(source)}


def _reply_context_source_class(value: str) -> str:
    if "Replied-to source type: bot" in value:
        return "bot_claim"
    if "Replied-to source type: user" in value:
        return "user_claim"
    return "user_claim_or_bot_claim"


def _detect_conflicts(
    *,
    current: str,
    sources: dict[str, str],
    frame_candidates: list[ContextFrameCandidate],
) -> list[ContextConflict]:
    frame_names = {candidate.frame for candidate in frame_candidates}
    if "current_turn" not in frame_names:
        return []

    background_sources = [
        key
        for key in ("recent_messages", "daily_memory", "long_memory", "retrieved_memory", "user_profile", "prompt_context_docs")
        if sources.get(key)
    ]
    if not background_sources:
        return []

    if sources.get("reply_context"):
        return []

    if _has_low_context_overlap(current=current, background="\n".join(sources[key] for key in background_sources)):
        return [
            ContextConflict(
                conflict_type="source_frame_boundary",
                frames=("current_turn", "background_context"),
                description=(
                    "Current turn and background context have low lexical overlap. "
                    "Treat this as a diagnostic boundary and prefer the current turn unless the model can connect them."
                ),
            )
        ]
    return []


def _has_low_context_overlap(*, current: str, background: str) -> bool:
    current_tokens = set(_meaningful_tokens(current))
    if len(current_tokens) < 2:
        return False
    background_tokens = set(_meaningful_tokens(background))
    if not background_tokens:
        return False
    return current_tokens.isdisjoint(background_tokens)


def _meaningful_tokens(value: str) -> list[str]:
    return [
        token.casefold()
        for token in _TOKEN_RE.findall(value)
        if len(token) > 2 and token.casefold() not in _STOPWORDS
    ]


def _detect_uncertainty(
    *,
    router_context: AIRouterContextV1,
    conflicts: list[ContextConflict],
    frame_candidates: list[ContextFrameCandidate],
    existing_current_info_signal: bool | None,
) -> list[str]:
    uncertainty: list[str] = []
    if conflicts:
        uncertainty.append("source_frame_boundary_needs_resolution")
    if router_context.context_error:
        uncertainty.append("context_read_error")
    if len(frame_candidates) > 1:
        uncertainty.append("multiple_context_sources")
    if not router_context.recent_messages_text and not router_context.recall_memory_text:
        uncertainty.append("limited_background_context")
    if existing_current_info_signal is None:
        uncertainty.append("current_info_need_not_resolved_by_snapshot")
    return uncertainty


def _build_assumptions(*, router_context: AIRouterContextV1, sources: dict[str, str]) -> list[str]:
    assumptions: list[str] = []
    if router_context.flag_bot_mention:
        assumptions.append("routed_by_bot_mention")
    if router_context.flag_reply_to_bot:
        assumptions.append("routed_by_reply_to_bot")
    if sources.get("recent_messages"):
        assumptions.append("recent_chat_context_available")
    if sources.get("reply_context"):
        assumptions.append("telegram_reply_context_available")
    if sources.get("retrieved_memory"):
        assumptions.append("retrieved_memory_available")
    if sources.get("prompt_context_docs"):
        assumptions.append("operator_context_notes_available")
    return assumptions
