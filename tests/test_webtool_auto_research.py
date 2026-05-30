from __future__ import annotations

from datetime import UTC, datetime

from amo_bot.telegram.webtool_auto_research import decide_auto_research


def test_auto_research_triggers_on_current_question():
    d = decide_auto_research("Wie ist der aktuelle BTC Kurs heute?")
    assert d.enabled is True
    assert d.capability == "websearch"


def test_auto_research_triggers_on_url():
    d = decide_auto_research("Bitte prüfe https://example.com/news")
    assert d.enabled is True
    assert d.capability in {"browser", "webscraping"}
    assert d.url.startswith("https://")


def test_auto_research_not_triggered_for_smalltalk():
    d = decide_auto_research("Hallo, wie gehts?")
    assert d.enabled is False


def test_auto_research_triggers_on_year_date_reference():
    d = decide_auto_research("Stand 2026, was ist neu bei Python?")
    assert d.enabled is True
    assert d.capability == "websearch"


def test_auto_research_empty_disabled():
    d = decide_auto_research("   ", now=datetime(2026, 1, 1, tzinfo=UTC))
    assert d.enabled is False
