from __future__ import annotations

from amo_bot.ai.current_data_classifier import HeuristicCurrentDataClassifier, classify_current_data


def test_classifier_requires_current_data_for_category_prompts():
    prompts = [
        "Wer ist gerade Tabellenführer?",
        "Was kostet das iPhone 16 aktuell?",
        "Ist die neue Version von Python schon draußen?",
        "Wann spielt Deutschland das nächste Mal?",
        "Wie stehen die Gruppen der Fußball WM?",
        "Wie ist das Wetter morgen in Berlin?",
        "Gibt es heute Störungen bei Vodafone?",
        "Was sagen die neuesten Umfragen?",
        "Ist der Dienst gerade down?",
        "Welche Filme laufen heute im Kino?",
        "Ist die OpenAI API Statuspage degraded?",
        "Was ist die aktuelle stabile Django Version laut offiziellen Docs?",
        "Ist die Playstation Portal gerade bei MediaMarkt lieferbar?",
        "Welche Termine gibt es heute im Kino in Berlin?",
        "Welche Relevanz hat die Robert Bosch GmbH am Finanzmarkt, welche Partner hat sie und wie ist die Rating-/Anleihe-Situation?",
        "Welche Änderungen gab es in der Telegram Bot API?",
        "Welche Lieferanten hat Apple Inc?",
        "Welche Ratings hat Volkswagen AG?",
        "Welche Änderungen gab es in der Discord API?",
        "What changed in the Stripe API?",
        "What is the CEO of OpenAI?",
        "Was ist die Umsatzentwicklung von SAP SE?",
    ]
    for prompt in prompts:
        decision = classify_current_data(prompt)
        assert decision.should_research is True, prompt
        assert decision.reason in {
            "semantic_current_data_required",
            "semantic_uncertain_external_lookup",
        }
        assert decision.signals


def test_classifier_requires_current_data_for_real_world_entity_mutable_facts_without_explicit_current_word():
    prompts = [
        "Welche Relevanz hat die Robert Bosch GmbH am Finanzmarkt, welche Partner hat sie und wie ist die Rating-/Anleihe-Situation?",
        "Welche Änderungen gab es in der Telegram Bot API?",
        "Welche Lieferanten hat Apple Inc?",
        "Welche Ratings hat Volkswagen AG?",
        "Welche Änderungen gab es in der Discord API?",
        "What changed in the Stripe API?",
        "What is the CEO of OpenAI?",
        "Was ist die Umsatzentwicklung von SAP SE?",
    ]
    for prompt in prompts:
        decision = classify_current_data(prompt)
        assert decision.should_research is True, prompt
        assert decision.label == "requires_current_data"
        assert "question_intent" in decision.signals


def test_classifier_requires_current_data_for_lookup_intent_named_entity_mutable_facts():
    prompts = [
        "@TsubasaOzora_bot finde Infos zu Bosch und ihren Partnern, und zur Bewertung am Finanzmarkt",
        "finde Infos zu Novo Nordisk Partnern und Bewertung",
        "show info about AcmeCloud API releases",
        "look up Contoso supplier partnerships and credit ratings",
        "zeige infos zu Claude API version",
        "gib mir infos zu Microsoft News",
        "finde Infos zu PlayStation 6 Preis",
        "zeige mir Infos zu Tesla Produkten und Preisen",
        "find info about iPhone availability",
    ]
    for prompt in prompts:
        decision = classify_current_data(prompt)
        assert decision.should_research is True, prompt
        assert decision.label == "requires_current_data"
        assert decision.reason == "semantic_current_data_required"
        assert "lookup_intent" in decision.signals


def test_classifier_does_not_require_current_data_for_timeless_prompts():
    prompts = [
        "Erklär mir was eine Vorrunde ist",
        "Warum mögen Menschen Fußball?",
        "Schreib mir eine Geschichte über eine WM",
        "Was ist eine Programmiersprache?",
        "Wie kann ich besser schlafen?",
        "Schreib mir eine Dokumentation für meine Beispiel-API",
        "Was bedeutet local im Python Scope?",
        "Schreib mir einen kurzen freundlichen Geburtstagsgruß",
        "Erkläre mir was ein Bond ist",
        "Was ist ein Partner in einer Beziehung?",
        "finde Infos zu einem Bond",
        "finde Infos zu Partnern in Beziehungen",
        "gib mir Infos zu Preisgestaltung im Allgemeinen",
        "show info about product management basics",
    ]
    for prompt in prompts:
        decision = classify_current_data(prompt)
        assert decision.should_research is False, prompt
        assert decision.label == "does_not_require_current_data"


def test_classifier_protocol_seam_can_be_injected():
    class _ProviderClassifier:
        def classify(self, text: str, *, metadata: dict[str, object] | None = None):
            return HeuristicCurrentDataClassifier().classify("Ist der Dienst gerade down?")

    decision = classify_current_data("neutral", classifier=_ProviderClassifier())
    assert decision.should_research is True
    assert decision.reason == "semantic_current_data_required"
