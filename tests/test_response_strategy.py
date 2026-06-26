from __future__ import annotations

from amo_bot.ai.response_strategy import classify_response_strategy, draft_self_limitation_requires_research


def test_response_strategy_direct_answer_matrix() -> None:
    prompts = [
        "Erklär mir das Konzept einer Vorrunde.",
        "Schreib einen kurzen freundlichen Geburtstagsgruß.",
        "Wie aktiviere ich die Bot-Bedienung mit einer Erwähnung?",
    ]

    for prompt in prompts:
        strategy = classify_response_strategy(prompt)
        assert strategy.label == "direct_answer", prompt


def test_response_strategy_research_needed_matrix_for_mutable_external_facts() -> None:
    prompts = [
        "Welche Partner hat Acme Robotics und wie ist die Bewertung am Finanzmarkt?",
        "Wer ist CEO von OpenAI?",
        "Was gibt es Neues bei der WHO?",
        "Welche Änderungen gab es in der Stripe API?",
        "Was kostet das iPhone 16 aktuell?",
        "Finde raus, welche Lieferanten Contoso Ltd hat.",
        "Welche Ratings hat Volkswagen AG?",
        "Ist die OpenAI API Statuspage degraded?",
    ]

    for prompt in prompts:
        strategy = classify_response_strategy(prompt)
        assert strategy.label == "research_needed", prompt
        assert strategy.signals


def test_response_strategy_clarify_only_for_vague_references() -> None:
    assert classify_response_strategy("").label == "clarify"
    assert classify_response_strategy("Erklär das").label == "clarify"
    assert classify_response_strategy("Erklär mir Trainingsdaten").label == "direct_answer"


def test_draft_self_limitation_guard_requires_external_fact_context() -> None:
    assert (
        draft_self_limitation_requires_research(
            message="Was kostet das iPhone 16 aktuell?",
            draft="Ich habe keine Live-Daten und kann den Preis nicht aktuell abrufen.",
        )
        is True
    )
    assert (
        draft_self_limitation_requires_research(
            message="Wer ist CEO von OpenAI?",
            draft="Nach meinem Wissensstand ist das so, aber meine Trainingsdaten koennen veraltet sein.",
        )
        is True
    )


def test_draft_self_limitation_guard_negative_cases() -> None:
    prompts = [
        "Erkläre den Begriff Trainingsdaten.",
        "Was bedeutet Live-Daten in einer Softwarearchitektur?",
    ]

    for prompt in prompts:
        assert (
            draft_self_limitation_requires_research(
                message=prompt,
                draft="Trainingsdaten und Live-Daten sind unterschiedliche Datenquellen.",
            )
            is False
        )
