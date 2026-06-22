from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any

from amo_bot.ai.context_snapshot import ContextSnapshotV1
from amo_bot.db.repositories import ClaimRecord, TopicCompactStateRecord


_MAX_ITEMS = 12


@dataclass(frozen=True, slots=True)
class CompactTopicStatePayload:
    active_subjects: list[dict[str, object]]
    frames: list[dict[str, object]]
    conflicts: list[dict[str, object]]
    verified_facts: list[dict[str, object]]
    discarded_assumptions: list[dict[str, object]]
    last_snapshot: dict[str, object]


def build_compact_topic_state_payload(
    *,
    snapshot: ContextSnapshotV1,
    claims: list[ClaimRecord],
    existing: TopicCompactStateRecord | None = None,
) -> CompactTopicStatePayload:
    """Merge the latest diagnostic snapshot and scoped claims into compact state."""
    updated_at = datetime.now(timezone.utc).isoformat()
    active_subjects = list(existing.active_subjects if existing is not None else [])
    frames = list(existing.frames if existing is not None else [])
    conflicts = list(existing.conflicts if existing is not None else [])
    verified_facts = list(existing.verified_facts if existing is not None else [])
    discarded_assumptions = list(existing.discarded_assumptions if existing is not None else [])

    if snapshot.active_subject.strip():
        active_subjects = _upsert_by_key(
            active_subjects,
            {
                "subject": snapshot.active_subject.strip(),
                "source": "current_snapshot",
                "updated_at": updated_at,
            },
            key="subject",
        )

    for candidate in snapshot.frame_candidates:
        frames = _upsert_by_key(
            frames,
            {
                "frame": candidate.frame,
                "source": candidate.source,
                "confidence": candidate.confidence,
                "evidence_count": candidate.evidence_count,
                "updated_at": updated_at,
            },
            key="frame",
        )

    for conflict in snapshot.conflicts:
        frames_key = "|".join(conflict.frames)
        conflicts = _upsert_by_key(
            conflicts,
            {
                "conflict_type": conflict.conflict_type,
                "frames": list(conflict.frames),
                "description": conflict.description,
                "updated_at": updated_at,
            },
            key=lambda item: f"{item.get('conflict_type')}:{'|'.join(str(frame) for frame in item.get('frames', []))}",
            item_key=f"{conflict.conflict_type}:{frames_key}",
        )

    if snapshot.conflicts:
        discarded_assumptions = _upsert_by_key(
            discarded_assumptions,
            {
                "assumption": "background_context_shares_current_frame",
                "reason": "latest_snapshot_reported_frame_conflict",
                "updated_at": updated_at,
            },
            key="assumption",
        )

    for claim in claims:
        if claim.verification_status == "supported":
            verified_facts = _upsert_by_key(
                verified_facts,
                {
                    "fact": claim.text,
                    "subject": claim.normalized_subject,
                    "source_type": claim.source_type,
                    "evidence_ref": claim.evidence_ref,
                    "confidence": claim.confidence,
                    "claim_id": claim.id,
                    "updated_at": updated_at,
                },
                key="claim_id",
            )
        elif claim.verification_status == "refuted":
            discarded_assumptions = _upsert_by_key(
                discarded_assumptions,
                {
                    "assumption": claim.text,
                    "subject": claim.normalized_subject,
                    "reason": "claim_refuted",
                    "evidence_ref": claim.evidence_ref,
                    "claim_id": claim.id,
                    "updated_at": updated_at,
                },
                key="claim_id",
            )

    return CompactTopicStatePayload(
        active_subjects=_trim(active_subjects),
        frames=_trim(frames),
        conflicts=_trim(conflicts),
        verified_facts=_trim(verified_facts),
        discarded_assumptions=_trim(discarded_assumptions),
        last_snapshot=snapshot.to_dict(),
    )


def format_compact_topic_state_prompt(record: TopicCompactStateRecord | None) -> str:
    if record is None:
        return ""

    lines: list[str] = [
        "Compact topic state:",
        f"schema_version={record.schema_version}",
        "Use this as scoped context state. Only verified_facts are evidence; active_subjects, frames, conflicts, and discarded_assumptions guide context resolution.",
    ]
    _append_items(lines, "active_subjects", record.active_subjects, ("subject", "source"))
    _append_items(lines, "frames", record.frames, ("frame", "source", "confidence"))
    _append_items(lines, "conflicts", record.conflicts, ("conflict_type", "frames", "description"))
    _append_items(lines, "verified_facts", record.verified_facts, ("fact", "evidence_ref", "confidence"))
    _append_items(lines, "discarded_assumptions", record.discarded_assumptions, ("assumption", "reason", "evidence_ref"))
    return "\n".join(lines).strip()


def _append_items(lines: list[str], title: str, items: list[dict[str, object]], keys: tuple[str, ...]) -> None:
    if not items:
        return
    lines.append(f"{title}:")
    for item in items[:_MAX_ITEMS]:
        parts: list[str] = []
        for key in keys:
            value = item.get(key)
            if value in (None, "", [], {}):
                continue
            parts.append(f"{key}={_compact_value(value)}")
        if parts:
            lines.append("- " + "; ".join(parts))


def _compact_value(value: object) -> str:
    if isinstance(value, list):
        return "[" + ", ".join(_compact_value(item) for item in value[:6]) + "]"
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text[:240].rstrip()


def _upsert_by_key(
    items: list[dict[str, object]],
    new_item: dict[str, object],
    *,
    key: str | Any,
    item_key: str | None = None,
) -> list[dict[str, object]]:
    if callable(key):
        new_key = item_key if item_key is not None else key(new_item)
        filtered = [item for item in items if key(item) != new_key]
    else:
        new_key = new_item.get(key)
        filtered = [item for item in items if item.get(key) != new_key]
    return [new_item, *filtered]


def _trim(items: list[dict[str, object]]) -> list[dict[str, object]]:
    return items[:_MAX_ITEMS]
