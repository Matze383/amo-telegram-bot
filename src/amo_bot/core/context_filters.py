from __future__ import annotations

import re

_BOT_AUTHORED_SOURCES = {"assistant", "bot"}

_META_STATUS_PHRASES = (
    "openclaw",
    "subagent task",
    "subagent context",
    "main coordinates",
    "metadata-only",
    "do not commit",
    "do not push",
    "do not restart",
    "no push",
    "local commit",
    "git status",
    "diff --check",
    "diff --stat",
    "pytest",
    "tests pass",
    "test pass",
    "qa pass",
    "qa/pass",
    "restart requested",
    "after restart",
    "branch ahead",
    "ahead ",
    "recall fix",
    "context drift",
    "context pollution",
    "prompt assembly",
    "backend implementation",
    "backend task",
    "run and report",
)

_META_STATUS_PATTERNS = (
    re.compile(r"\bcommit\s+[0-9a-f]{7,40}\b", re.IGNORECASE),
    re.compile(r"\b[a-f0-9]{7,40}\s+fix:\s", re.IGNORECASE),
    re.compile(r"\b(?:pass|fail):\s+(?:tests?|qa|backend|frontend)\b", re.IGNORECASE),
    re.compile(r"\b(?:backend|frontend|dispatcher|router)\s+(?:status|fix|gate|gates|qa|tests?)\b", re.IGNORECASE),
    re.compile(r"\b(?:restart|restarted|deploy|deployed|release|tag|push|pushed)\s+(?:done|pending|blocked|requested|complete|completed)\b", re.IGNORECASE),
)


def is_bot_authored_context_record(row: object) -> bool:
    """Return true for rows authored by this/another bot and unsafe as prompt context."""

    source = str(getattr(row, "source", "") or "").strip().casefold()
    return bool(getattr(row, "telegram_author_is_bot", False)) or source in _BOT_AUTHORED_SOURCES


def is_obvious_meta_status_message(value: str | None) -> bool:
    """Conservative deterministic filter for operational/workflow chatter.

    This is intentionally aimed at obvious status/coordinator lines. It is used
    only for background context/memory digestion, never to suppress the current
    user turn.
    """

    normalized = " ".join((value or "").strip().split())
    if not normalized:
        return False

    lower = normalized.casefold()
    if any(phrase in lower for phrase in _META_STATUS_PHRASES):
        return True

    return any(pattern.search(normalized) for pattern in _META_STATUS_PATTERNS)
