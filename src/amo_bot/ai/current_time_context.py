from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_AI_PROMPT_TIMEZONE = "Europe/Berlin"


def build_current_time_context(
    *,
    now: datetime | None = None,
    timezone_name: str = DEFAULT_AI_PROMPT_TIMEZONE,
) -> str:
    """Build a compact, deterministic current-time prompt block.

    The block intentionally contains only time metadata and a short instruction.
    It must not include user text, memory contents, prompts, secrets, or other
    private context so it remains safe for metadata-only logging boundaries.
    """
    tz_name = (timezone_name or DEFAULT_AI_PROMPT_TIMEZONE).strip() or DEFAULT_AI_PROMPT_TIMEZONE
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        tz_name = DEFAULT_AI_PROMPT_TIMEZONE
        tz = ZoneInfo(DEFAULT_AI_PROMPT_TIMEZONE)

    base_now = now or datetime.now(UTC)
    if base_now.tzinfo is None:
        base_now = base_now.replace(tzinfo=UTC)

    local_now = base_now.astimezone(tz)
    utc_now = base_now.astimezone(UTC)
    utc_iso = utc_now.isoformat(timespec="seconds").replace("+00:00", "Z")

    return "\n".join(
        (
            "Current time context (system-provided, higher priority than memory/recent chat):",
            f"Current date: {local_now.date().isoformat()}",
            f"Timezone: {tz_name}",
            f"Local timestamp: {local_now.isoformat(timespec='seconds')}",
            f"UTC timestamp: {utc_iso}",
            "Use this as the current date/time. For live/current external facts, use web research when available; do not infer from model training date.",
        )
    )
