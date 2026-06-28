from __future__ import annotations

from datetime import UTC, datetime

from amo_bot.telegram.webtool_auto_research import decide_auto_research


def test_auto_research_triggers_on_current_question():
    d = decide_auto_research("Wie ist der aktuelle BTC Kurs heute?")
    assert d.enabled is True
    assert d.capability == "websearch"
    assert d.research_report_type == "research_report"


def test_auto_research_triggers_on_crypto_current_price_de_and_en():
    d_de = decide_auto_research("Was ist der aktuelle Bitcoin Preis in USD?")
    d_en = decide_auto_research("What's the current Bitcoin price right now?")
    assert d_de.enabled is True and d_de.capability == "websearch"
    assert d_en.enabled is True and d_en.capability == "websearch"


def test_auto_research_triggers_on_broad_crypto_names_symbols_and_unknown_coin():
    prompts = [
        "Was macht Solana?",
        "XRP price now",
        "Wie steht Dogecoin aktuell?",
        "Was ist BlorpCoin?",
        "Was ist FooToken?",
        "BlorpCoin token price now",
    ]
    for prompt in prompts:
        d = decide_auto_research(prompt)
        assert d.enabled is True, prompt
        assert d.capability == "websearch"
        assert d.reason in {"market_current_info_signal", "current_info_signal"}


def test_auto_research_does_not_treat_standalone_coin_or_token_as_crypto_current_signal():
    prompts = [
        "Was ist ein Coin Toss?",
        "coin collector",
        "token bucket",
        "Wie funktioniert ein Token Bucket Algorithmus?",
    ]
    for prompt in prompts:
        d = decide_auto_research(prompt)
        assert d.enabled is False, prompt


def test_auto_research_allows_common_crypto_nouns_with_market_context():
    prompts = [
        "coin price now",
        "token market aktuell",
        "Was ist ein Blockchain token?",
    ]
    for prompt in prompts:
        d = decide_auto_research(prompt)
        assert d.enabled is True, prompt
        assert d.capability == "websearch"


def test_auto_research_triggers_on_generic_company_listing_and_derivative_prompts():
    prompts = [
        "Ist SpaceX an der Börse?",
        "Ist Anthropic an der Börse?",
        "Ist Siemens an der Börse?",
        "Ist Adidas börsennotiert?",
        "Ist Quarvex Labs an der Börse?",
        "Ist AcmeBlubBla an der Börse?",
        "Ist FooBarBaz AG an der Börse?",
        "Was ist SPCXUSDT auf Bybit?",
        "Kann man SpaceX Aktien kaufen?",
        "Kann man Anthropic Aktien kaufen?",
        "Kann man Siemens Aktien kaufen?",
        "Kann man Adidas Aktien kaufen?",
        "Kann man Quarvex Labs Aktien kaufen?",
        "Kann man AcmeBlubBla Aktien kaufen?",
        "Kann man FooBarBaz AG Aktien kaufen?",
        "Was ist OPENAIUSDT auf Bybit?",
        "Gibt es Neuralink tokenized exposure auf Bybit?",
        "Nasdaq Anthropic",
        "Nasdaq Quarvex Labs",
        "Nasdaq AcmeBlubBla",
        "NYSE Anthropic",
        "NYSE FooBarBaz AG",
    ]
    for prompt in prompts:
        d = decide_auto_research(prompt)
        assert d.enabled is True, prompt
        assert d.capability == "websearch"
        assert d.reason == "market_current_info_signal"


def test_auto_research_triggers_on_url():
    d = decide_auto_research("Bitte prüfe https://example.com/news")
    assert d.enabled is True
    assert d.capability in {"browser", "webscraping"}
    assert d.url.startswith("https://")


def test_auto_research_keeps_full_long_url():
    url = "https://example.com/research/" + "very-long-path-segment-" * 18 + "final"

    d = decide_auto_research(f"Bitte prüfe {url}")

    assert d.enabled is True
    assert d.url == url


def test_auto_research_routes_finance_listing_url_to_current_info():
    url = (
        "https://www.reutersconnect.com/item/"
        "spacexs-initial-public-offering-ipo-at-the-nasdaq-marketsite-in-new-york-city/"
        "dGFnOnJldXRlcnMuY29tLDIwMjY6bmV3c21sX1JDMktTTEFSWE05Vw"
    )

    d = decide_auto_research(f"Ist SpaceX an der Börse? Quelle: {url}")

    assert d.enabled is True
    assert d.capability == "websearch"
    assert d.reason == "market_current_info_signal"
    assert d.query.startswith("Ist SpaceX an der Börse?")
    assert d.url == url


def test_auto_research_keeps_full_url_when_finance_listing_prompt_is_long():
    url = (
        "https://www.reutersconnect.com/item/"
        "spacexs-initial-public-offering-ipo-at-the-nasdaq-marketsite-in-new-york-city/"
        "dGFnOnJldXRlcnMuY29tLDIwMjY6bmV3c21sX1JDMktTTEFSWE05Vw"
    )
    filler = " ".join(["bitte sehr genau prüfen"] * 20)

    d = decide_auto_research(f"Ist SpaceX an der Börse? {filler} Quelle: {url}")

    assert d.enabled is True
    assert d.capability == "websearch"
    assert d.reason == "market_current_info_signal"
    assert len(d.query) < len(f"Ist SpaceX an der Börse? {filler} Quelle: {url}")
    assert d.url == url


def test_auto_research_not_triggered_for_smalltalk():
    d = decide_auto_research("Hallo, wie gehts?")
    assert d.enabled is False


def test_auto_research_triggers_on_year_date_reference():
    d = decide_auto_research("Stand 2026, was ist neu bei Python?")
    assert d.enabled is True
    assert d.capability == "websearch"


def test_auto_research_routes_complex_research_prompts_to_webresearch():
    prompts = [
        "Recherchiere die aktuelle Lage zu SearxNG und Brave Search mit Quellen.",
        "Analysiere Vor- und Nachteile von GPT Researcher im Vergleich zu einfacher Websuche.",
        "Was spricht dafür und dagegen, pgvector für Research-Kontexte zu nutzen?",
        "Latest developments on local LLM research tools with sources",
        "erstelle mir einen ausführlichen aktuellen Bericht zu ExampleTech Partner Pläne Finanzen",
    ]
    for prompt in prompts:
        d = decide_auto_research(prompt)
        assert d.enabled is True, prompt
        assert d.capability == "webresearch"
        assert d.reason in {"complex_research_signal", "semantic_current_data_required"}
        assert d.research_report_type == "deep_research"


def test_auto_research_does_not_route_generic_summaries_and_simple_comparisons_to_deep_research():
    prompts = [
        "Summarize this text",
        "Summarize last chat",
        "Summarize the last chat",
        "Fasse den letzten Chat zusammen",
        "Fasse bitte den letzten Chat zusammen",
        "Bitte vergleiche kurz Python und Go",
    ]
    for prompt in prompts:
        d = decide_auto_research(prompt)
        assert d.capability != "webresearch", prompt
        assert d.research_report_type != "deep_research", prompt


def test_auto_research_keeps_current_external_comparison_as_deep_research():
    d = decide_auto_research("Vergleiche die aktuellen Python und Go Releases mit Quellen.")

    assert d.enabled is True
    assert d.capability == "webresearch"
    assert d.reason == "complex_research_signal"
    assert d.research_report_type == "deep_research"


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
        assert d.research_report_type == "research_report"


def test_auto_research_keeps_short_weather_sport_price_and_simple_fact_out_of_deep_research():
    prompts = [
        "Wie ist das Wetter morgen in Berlin?",
        "Bundesliga Tabelle aktuell",
        "Was ist der aktuelle Bitcoin Preis in USD?",
        "What is the CEO of OpenAI?",
    ]
    for prompt in prompts:
        d = decide_auto_research(prompt)
        assert d.enabled is True, prompt
        assert d.capability == "websearch"
        assert d.research_report_type == "research_report"


def test_auto_research_does_not_trigger_for_general_sports_chat_without_current_detail():
    timeless = decide_auto_research("Ich mag Fußball und alte WM Geschichten")
    assert timeless.enabled is False


def test_auto_research_uses_generic_current_data_classifier_for_category_prompts():
    prompts = [
        "Wer ist gerade Tabellenführer?",
        "Was kostet das iPhone 16 aktuell?",
        "Ist die neue Version von Python schon draußen?",
        "Wann spielt Deutschland das nächste Mal?",
        "Wie ist das Wetter morgen in Berlin?",
        "Gibt es heute Störungen bei Vodafone?",
        "Was sagen die neuesten Umfragen?",
        "Ist der Dienst gerade down?",
        "Welche Filme laufen heute im Kino?",
        "Ist GitHub Actions gerade down?",
        "Ist die aktuelle FastAPI Version laut offiziellen Release Notes draußen?",
        "Ist die Playstation Portal heute bei Saturn lieferbar?",
        "Welche Bürgeramt Termine gibt es heute in Berlin?",
        "What is the current OpenAI API status?",
        "Welche Änderungen gab es in der Telegram Bot API?",
        "Welche Lieferanten hat Apple Inc?",
        "Welche Ratings hat Volkswagen AG?",
        "Welche Änderungen gab es in der Discord API?",
        "What changed in the Stripe API?",
        "What is the CEO of OpenAI?",
        "Was ist die Umsatzentwicklung von SAP SE?",
    ]
    for prompt in prompts:
        d = decide_auto_research(prompt)
        assert d.enabled is True, prompt
        assert d.capability == "websearch"
        assert d.reason in {
            "current_info_signal",
            "semantic_current_data_required",
            "semantic_uncertain_external_lookup",
        }


def test_auto_research_routes_multi_facet_company_research_to_webresearch():
    d = decide_auto_research(
        "Welche Relevanz hat die Robert Bosch GmbH am Finanzmarkt, "
        "welche Partner hat sie und wie ist die Rating-/Anleihe-Situation?"
    )

    assert d.enabled is True
    assert d.capability == "webresearch"
    assert d.reason == "semantic_current_data_required"
    assert d.research_report_type == "deep_research"


def test_auto_research_triggers_on_lookup_intent_named_entity_mutable_facts():
    prompts = [
        "show info about AcmeCloud API releases",
        "gib mir infos zu Microsoft News",
        "zeige mir Infos zu Tesla Produkten und Preisen",
    ]
    for prompt in prompts:
        d = decide_auto_research(prompt)
        assert d.enabled is True, prompt
        assert d.capability == "websearch"
        assert d.reason in {"semantic_current_data_required", "current_info_signal"}


def test_auto_research_routes_broad_company_lookup_to_webresearch():
    prompts = [
        "@TsubasaOzora_bot finde Infos zu Bosch und ihren Partnern, und zur Bewertung am Finanzmarkt",
        "finde Infos zu Siemens und ihren Partnern, und zur Bewertung am Finanzmarkt",
        "finde Infos zu Novo Nordisk Partnern und Bewertung",
        "look up Contoso supplier partnerships and credit ratings",
    ]
    for prompt in prompts:
        d = decide_auto_research(prompt)
        assert d.enabled is True, prompt
        assert d.capability == "webresearch"
        assert d.reason == "semantic_current_data_required"
        assert d.research_report_type == "deep_research"


def test_auto_research_classifier_does_not_trigger_timeless_prompts():
    prompts = [
        "Erklär mir was eine Vorrunde ist",
        "Warum mögen Menschen Fußball?",
        "Schreib mir eine Geschichte über eine WM",
        "Was ist eine Programmiersprache?",
        "Wie kann ich besser schlafen?",
        "Schreib mir eine API-Dokumentation für mein Hobbyprojekt",
        "Was bedeutet local variable scope in Python?",
        "Erkläre mir was ein Bond ist",
        "Was ist ein Partner in einer Beziehung?",
        "finde Infos zu einem Bond",
        "finde Infos zu Partnern in Beziehungen",
        "gib mir Infos zu Kostenplanung im Allgemeinen",
        "show info about product management basics",
    ]
    for prompt in prompts:
        d = decide_auto_research(prompt)
        assert d.enabled is False, prompt


def test_auto_research_empty_disabled():
    d = decide_auto_research("   ", now=datetime(2026, 1, 1, tzinfo=UTC))
    assert d.enabled is False
