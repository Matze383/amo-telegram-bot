from __future__ import annotations

from datetime import UTC, datetime

from amo_bot.telegram.webtool_auto_research import decide_auto_research


def test_auto_research_triggers_on_current_question():
    d = decide_auto_research("Wie ist der aktuelle BTC Kurs heute?")
    assert d.enabled is True
    assert d.capability == "websearch"


def test_auto_research_triggers_on_crypto_current_price_de_and_en():
    d_de = decide_auto_research("Was ist der aktuelle Bitcoin Preis in USD?")
    d_en = decide_auto_research("What's the current Bitcoin price right now?")
    assert d_de.enabled is True and d_de.capability == "websearch"
    assert d_en.enabled is True and d_en.capability == "websearch"


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


def test_auto_research_triggers_on_german_sports_tournament_current_prompts():
    prompts = [
        "Wie läuft die WM-Vorrunde?",
        "Was ist der aktuelle Spielplan der WM Vorrunde?",
        "Wie steht Deutschland in der Gruppenphase?",
        "Bundesliga Tabelle aktuell",
    ]
    for prompt in prompts:
        d = decide_auto_research(prompt)
        assert d.enabled is True, prompt
        assert d.capability == "websearch"


def test_auto_research_does_not_trigger_for_general_sports_chat_without_current_detail():
    timeless = decide_auto_research("Ich mag Fußball und alte WM Geschichten")
    assert timeless.enabled is False


def test_auto_research_empty_disabled():
    d = decide_auto_research("   ", now=datetime(2026, 1, 1, tzinfo=UTC))
    assert d.enabled is False
