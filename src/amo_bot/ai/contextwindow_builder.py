from __future__ import annotations

from dataclasses import dataclass

_SAFE_METADATA_STRING_VALUES: dict[str, frozenset[str]] = {
    "class": frozenset({"profile", "project", "task", "ops", "public", "system"}),
    "scope": frozenset({"global", "project", "session", "local"}),
    "kind": frozenset({"input", "context", "evidence", "constraint", "policy", "signal"}),
    "tag": frozenset({"seed", "carry", "checkpoint", "sanitized", "derived", "normal"}),
    "topic": frozenset({"engineering", "product", "ops", "planning", "quality", "security"}),
    "reason_code": frozenset(
        {
            "included",
            "budget_exceeded",
            "empty_text",
            "sensitive_excluded_default",
            "sensitive_source_type_excluded",
            "source_type_not_allowed",
        }
    ),
    "priority_hint": frozenset({"low", "normal", "high", "critical"}),
    "risk": frozenset({"low", "medium", "high", "critical"}),
}

_SAFE_METADATA_INT_BOUNDS: dict[str, tuple[int, int]] = {
    "count": (0, 1_000_000),
}

_SAFE_METADATA_BOOL_KEYS: frozenset[str] = frozenset({"flag"})

_ALLOWED_SOURCE_TYPES: frozenset[str] = frozenset(
    {
        "soul",
        "identity",
        "agents",
        "policy",
        "task",
        "context",
        "summary",
        "tool_result",
        "plugin",
        "user",
    }
)

_SENSITIVE_SOURCE_TYPES: frozenset[str] = frozenset(
    {
        "memory",
        "private_memory",
        "daily_memory",
        "long_memory",
        "diary",
    }
)


@dataclass(frozen=True, slots=True)
class ContextWindowSource:
    source_id: str
    source_type: str
    text: str
    priority: int = 100
    sensitive: bool = False
    allow_sensitive: bool = False
    metadata: dict[str, str | int | bool] | None = None


@dataclass(frozen=True, slots=True)
class ContextWindowAuditEntry:
    source_id: str
    source_type: str
    reason: str
    metadata: dict[str, str | int | bool]


@dataclass(frozen=True, slots=True)
class ContextWindowBuildResult:
    context_text: str
    included: tuple[ContextWindowAuditEntry, ...]
    excluded: tuple[ContextWindowAuditEntry, ...]
    used_tokens: int
    token_budget: int


def _estimate_tokens(text: str) -> int:
    safe = text.strip()
    if not safe:
        return 0
    return max(1, (len(safe) + 3) // 4)


def _normalize_source_type(source_type: str) -> str:
    return source_type.strip().lower().replace("-", "_")


def _safe_meta(meta: dict[str, str | int | bool] | None) -> dict[str, str | int | bool]:
    if not meta:
        return {}

    redacted: dict[str, str | int | bool] = {}
    for key, value in meta.items():
        safe_key = str(key)

        if safe_key in _SAFE_METADATA_STRING_VALUES and isinstance(value, str):
            safe_value = value.strip().lower()
            if safe_value in _SAFE_METADATA_STRING_VALUES[safe_key]:
                redacted[safe_key] = safe_value
            continue

        if safe_key in _SAFE_METADATA_INT_BOUNDS and isinstance(value, int) and not isinstance(value, bool):
            lower, upper = _SAFE_METADATA_INT_BOUNDS[safe_key]
            if lower <= value <= upper:
                redacted[safe_key] = value
            continue

        if safe_key in _SAFE_METADATA_BOOL_KEYS and isinstance(value, bool):
            redacted[safe_key] = value

    return redacted


def build_contextwindow_v1(
    *,
    sources: list[ContextWindowSource],
    token_budget: int,
) -> ContextWindowBuildResult:
    if token_budget < 0:
        raise ValueError("token_budget must be >= 0")

    ordered = sorted(
        sources,
        key=lambda s: (s.priority, s.source_type, s.source_id),
    )

    included: list[ContextWindowAuditEntry] = []
    excluded: list[ContextWindowAuditEntry] = []
    chunks: list[str] = []
    used = 0

    for source in ordered:
        meta = _safe_meta(source.metadata)
        normalized_source_type = _normalize_source_type(source.source_type)

        if normalized_source_type in _SENSITIVE_SOURCE_TYPES:
            excluded.append(
                ContextWindowAuditEntry(
                    source_id=source.source_id,
                    source_type=source.source_type,
                    reason="sensitive_source_type_excluded",
                    metadata={**meta, "priority": source.priority},
                )
            )
            continue

        if normalized_source_type not in _ALLOWED_SOURCE_TYPES:
            excluded.append(
                ContextWindowAuditEntry(
                    source_id=source.source_id,
                    source_type=source.source_type,
                    reason="source_type_not_allowed",
                    metadata={**meta, "priority": source.priority},
                )
            )
            continue

        if source.sensitive:
            excluded.append(
                ContextWindowAuditEntry(
                    source_id=source.source_id,
                    source_type=source.source_type,
                    reason="sensitive_excluded_default",
                    metadata={**meta, "priority": source.priority, "sensitive": True},
                )
            )
            continue

        text = source.text.strip()
        needed = _estimate_tokens(text)
        if needed == 0:
            excluded.append(
                ContextWindowAuditEntry(
                    source_id=source.source_id,
                    source_type=source.source_type,
                    reason="empty_text",
                    metadata={**meta, "priority": source.priority},
                )
            )
            continue

        if used + needed > token_budget:
            excluded.append(
                ContextWindowAuditEntry(
                    source_id=source.source_id,
                    source_type=source.source_type,
                    reason="budget_exceeded",
                    metadata={**meta, "priority": source.priority, "estimated_tokens": needed},
                )
            )
            continue

        chunks.append(text)
        used += needed
        included.append(
            ContextWindowAuditEntry(
                source_id=source.source_id,
                source_type=source.source_type,
                reason="included",
                metadata={**meta, "priority": source.priority, "estimated_tokens": needed},
            )
        )

    return ContextWindowBuildResult(
        context_text="\n\n".join(chunks),
        included=tuple(included),
        excluded=tuple(excluded),
        used_tokens=used,
        token_budget=token_budget,
    )
