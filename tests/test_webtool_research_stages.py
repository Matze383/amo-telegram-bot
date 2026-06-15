from __future__ import annotations

from types import SimpleNamespace

from amo_bot.telegram.webtool_research_orchestrator import (
    SearchExecutionStageOutput,
    build_extraction_browser_stage,
    build_query_planner_stage,
    build_source_selection_stage,
    synthesize_research_answer,
    validate_research_evidence,
)


def _search_result(
    *,
    text: str,
    sources: tuple[str, ...] = (),
    hosts: tuple[str, ...] = (),
):
    return SimpleNamespace(
        allowed=True,
        decision="allow",
        reason="search_completed",
        text=text,
        sources=sources,
        hosts=hosts,
        error=None,
    )


def test_query_planner_contract_detects_current_sports_result_intent() -> None:
    stage = build_query_planner_stage(
        request_text="Gegen wen hat Deutschland in der Gruppenphase der EM 2024 gespielt und wie war das Ergebnis?"
    )

    assert stage.enabled is True
    assert stage.domain == "sports"
    assert stage.capability == "websearch"
    assert stage.query
    assert stage.url == ""
    assert stage.is_followup_research is False


def test_source_selection_contract_plans_sports_followup_for_partial_result_snippet() -> None:
    search_stage = SearchExecutionStageOutput(
        result=_search_result(
            text=(
                "Germany Euro 2024 group stage result snippet: Germany drew 1-1, "
                "but this search preview does not name the opponent."
            ),
            sources=("https://sports.example/euro-2024/germany",),
            hosts=("sports.example",),
        ),
        capability="websearch",
        reason="sports_current_info_signal",
    )

    stage = build_source_selection_stage(
        request_text="Gegen wen hat Deutschland in der Gruppenphase der EM 2024 gespielt und wie war das Ergebnis?",
        search_execution=search_stage,
    )

    assert stage.plan.domain == "sports"
    assert stage.plan.evidence_status == "weak_initial_evidence"
    assert "sports_result_opponent_score_missing" in stage.plan.warning_codes
    assert stage.plan.should_followup_search is True
    assert stage.selected_urls == ("https://sports.example/euro-2024/germany",)


def test_evidence_validator_rejects_snippet_only_news_as_answer_evidence() -> None:
    search_stage = SearchExecutionStageOutput(
        result=_search_result(
            text="2026-06-15 Acme announced a merger, according to a short search snippet.",
            sources=("https://news.example/acme-merger",),
            hosts=("news.example",),
        ),
        capability="websearch",
        reason="current_info_signal",
    )
    extraction_stage = build_extraction_browser_stage(
        request_text="neueste News zu Acme heute",
        capability="websearch",
        reason="current_info_signal",
        search_text=search_stage.result.text,
        source_hosts=search_stage.result.hosts,
        source_urls=search_stage.result.sources,
        extracts=(),
    )

    validation = validate_research_evidence(
        request_text="neueste News zu Acme heute",
        search_execution=search_stage,
        extraction=extraction_stage,
    )
    synthesis = synthesize_research_answer(validation=validation, capability="websearch", locale="de")

    assert validation.can_synthesize is False
    assert validation.status == "source_check_inconclusive"
    assert "source_check_inconclusive" in validation.warnings
    assert synthesis.auto_note == ""
    assert "aktuellen Nachrichten" in synthesis.user_response
    assert "nicht belastbar bestätigen" in synthesis.user_response
    assert "Acme announced a merger" not in synthesis.user_response
    assert "Such-Snippets" in synthesis.user_response


def test_evidence_validator_allows_checked_sports_source_evidence_for_synthesis() -> None:
    search_stage = SearchExecutionStageOutput(
        result=_search_result(
            text="Germany beat Scotland 5-1 at Euro 2024.",
            sources=("https://scores.example/euro-2024/germany-scotland",),
            hosts=("scores.example",),
        ),
        capability="websearch",
        reason="sports_current_info_signal",
    )
    extraction_stage = build_extraction_browser_stage(
        request_text="Germany Euro 2024 group stage result?",
        capability="websearch",
        reason="sports_current_info_signal",
        search_text=search_stage.result.text,
        source_hosts=search_stage.result.hosts,
        source_urls=search_stage.result.sources,
        extracts=(
            (
                "webscraping",
                "scores.example",
                "Germany beat Scotland 5-1 at Euro 2024 in the tournament opener.",
            ),
        ),
    )

    validation = validate_research_evidence(
        request_text="Germany Euro 2024 group stage result?",
        search_execution=search_stage,
        extraction=extraction_stage,
    )
    synthesis = synthesize_research_answer(validation=validation, capability="websearch", locale="de")

    assert validation.can_synthesize is True
    assert validation.status == "checked_evidence_available"
    assert validation.checked_extracts
    assert synthesis.user_response == ""
    assert "AUTO-RESEARCH (LIVE WEB + SOURCE CHECK)" in synthesis.auto_note
    assert "Ziel-Antwortsprache: Deutsch" in synthesis.auto_note
    assert "Checked source evidence:" in synthesis.auto_note
    assert "Germany beat Scotland 5-1" in synthesis.auto_note
