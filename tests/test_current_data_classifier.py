from __future__ import annotations

from amo_bot.ai.current_data_classifier import HeuristicCurrentDataClassifier, classify_current_data


def test_classifier_requires_current_data_for_category_prompts():
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
    ]
    for prompt in prompts:
        decision = classify_current_data(prompt)
        assert decision.should_research is True, prompt
        assert decision.reason in {
            "semantic_current_data_required",
            "semantic_uncertain_external_lookup",
        }
        assert decision.signals


def test_classifier_does_not_require_current_data_for_timeless_prompts():
    prompts = [
        "Erklär mir was eine Vorrunde ist",
        "Warum mögen Menschen Fußball?",
        "Schreib mir eine Geschichte über eine WM",
        "Was ist eine Programmiersprache?",
        "Wie kann ich besser schlafen?",
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
