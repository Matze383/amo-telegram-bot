from datetime import UTC, datetime

from amo_bot.ai.current_time_context import build_current_time_context
from amo_bot.ai.router import AIRouter


def test_current_time_context_is_deterministic_and_berlin_localized() -> None:
    text = build_current_time_context(
        now=datetime(2026, 6, 3, 16, 1, 2, tzinfo=UTC),
        timezone_name="Europe/Berlin",
    )

    assert "Context:" in text
    assert "Current date: 2026-06-03" in text
    assert "Timezone: Europe/Berlin" in text
    assert "Local timestamp: 2026-06-03T18:01:02+02:00" in text
    assert "UTC timestamp: 2026-06-03T16:01:02Z" in text
    assert "When answering about current events or live facts, prefer available web research over prior knowledge." in text
    assert "system-provided" not in text
    assert "higher priority" not in text
    assert "memory/recent chat" not in text
    assert "model training date" not in text


def test_router_context_includes_current_time_independent_of_memory() -> None:
    router = AIRouter(now_provider=lambda: datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC))

    decision = router.decide(prompt="hello from user", chat_id=None, user_id=123)

    assert "Current date: 2026-01-02" in decision.context.current_time_context_text
    assert "Timezone: Europe/Berlin" in decision.context.current_time_context_text
    assert "hello from user" not in decision.context.current_time_context_text
    assert decision.context.daily_memory_text == ""
    assert decision.context.recent_messages_text == ""
