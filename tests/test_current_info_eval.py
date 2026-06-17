from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from amo_bot.current_info.eval import (
    load_current_info_eval_cases,
    run_current_info_eval_cases,
    run_current_info_eval_fixture,
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "current_info_eval_cases.json"


class _DeterministicClock:
    def __init__(self, *, step_seconds: float = 0.012) -> None:
        self._value = 100.0
        self._step_seconds = step_seconds

    def __call__(self) -> float:
        self._value += self._step_seconds
        return self._value


def test_current_info_eval_fixture_runs_news_release_and_local_event_cases() -> None:
    report = run_current_info_eval_fixture(FIXTURE_PATH, clock=_DeterministicClock())

    assert report.mode == "local"
    assert report.total == 3
    assert report.passed == 3
    assert report.failed == 0
    assert report.by_query_type == {
        "local_event": {"total": 1, "passed": 1, "failed": 0},
        "news": {"total": 1, "passed": 1, "failed": 0},
        "release": {"total": 1, "passed": 1, "failed": 0},
    }

    results = {result.case_id: result for result in report.results}
    assert results["news_two_fresh_sources"].metrics.source_count == 2
    assert results["news_two_fresh_sources"].metrics.fetched_source_count == 2
    assert results["news_two_fresh_sources"].metrics.freshness == "fresh"
    assert results["news_two_fresh_sources"].metrics.evidence_coverage == 1.0
    assert results["local_event_provider_error_regression"].metrics.provider_error_count == 1
    assert [result.case_id for result in report.results] == sorted(results)


def test_current_info_eval_detects_provider_error_regression() -> None:
    cases = list(load_current_info_eval_cases(FIXTURE_PATH))
    provider_error_case = next(case for case in cases if case.case_id == "local_event_provider_error_regression")
    strict_case = replace(provider_error_case, max_provider_errors=0)
    cases[cases.index(provider_error_case)] = strict_case

    report = run_current_info_eval_cases(cases, clock=_DeterministicClock())

    result = next(item for item in report.results if item.case_id == strict_case.case_id)
    assert result.passed is False
    assert result.failed_checks == ("provider_errors",)
    assert report.failed == 1


def test_current_info_eval_detects_missing_evidence_coverage() -> None:
    cases = list(load_current_info_eval_cases(FIXTURE_PATH))
    release_case = next(case for case in cases if case.case_id == "release_official_source")
    stricter_case = replace(
        release_case,
        required_evidence_terms=("latest AMO release", "not in fixture"),
        min_evidence_coverage=1.0,
    )
    cases[cases.index(release_case)] = stricter_case

    report = run_current_info_eval_cases(cases, clock=_DeterministicClock())

    result = next(item for item in report.results if item.case_id == stricter_case.case_id)
    assert result.metrics.evidence_coverage == 0.5
    assert result.failed_checks == ("evidence_coverage",)


def test_current_info_eval_live_mode_is_explicitly_separate() -> None:
    cases = load_current_info_eval_cases(FIXTURE_PATH)

    with pytest.raises(ValueError, match="live current-info evals"):
        run_current_info_eval_cases(cases, mode="live")


def test_current_info_eval_cli_emits_comparable_json_and_jsonl() -> None:
    json_run = subprocess.run(
        [sys.executable, "-m", "amo_bot.current_info.eval", str(FIXTURE_PATH), "--json"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert json_run.returncode == 0, json_run.stderr
    payload = json.loads(json_run.stdout)
    assert payload["mode"] == "local"
    assert payload["passed"] == 3
    assert [item["case_id"] for item in payload["results"]] == [
        "local_event_provider_error_regression",
        "news_two_fresh_sources",
        "release_official_source",
    ]

    jsonl_run = subprocess.run(
        [sys.executable, "-m", "amo_bot.current_info.eval", str(FIXTURE_PATH), "--jsonl"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert jsonl_run.returncode == 0, jsonl_run.stderr
    lines = [json.loads(line) for line in jsonl_run.stdout.splitlines()]
    assert len(lines) == 3
    assert lines[0]["case_id"] == "local_event_provider_error_regression"

    local_only_run = subprocess.run(
        [sys.executable, "-m", "amo_bot.current_info.eval", str(FIXTURE_PATH), "--local-only"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert local_only_run.returncode == 0, local_only_run.stderr
    assert "Current-Info eval: 3/3 passed" in local_only_run.stdout


def test_current_info_eval_cli_rejects_live_mode() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "amo_bot.current_info.eval", str(FIXTURE_PATH), "--mode", "live"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "live evals are intentionally separated" in result.stderr
