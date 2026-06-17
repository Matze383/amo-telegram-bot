from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from amo_bot.current_info.models import (
    CurrentInfoAnswer,
    CurrentInfoRequest,
    EvidenceChunk,
    FetchedDocument,
    SearchProviderMetric,
    SearchProviderResponse,
    SearchResult,
)
from amo_bot.current_info.service import CurrentInfoService


JsonDict = dict[str, Any]
Clock = Callable[[], float]


@dataclass(frozen=True, slots=True)
class CurrentInfoEvalCase:
    case_id: str
    query_type: str
    request: CurrentInfoRequest
    search_results: tuple[SearchResult, ...] = ()
    search_metrics: tuple[SearchProviderMetric, ...] = ()
    documents: tuple[FetchedDocument, ...] = ()
    chunks: tuple[EvidenceChunk, ...] = ()
    expected_statuses: tuple[str, ...] = ("answered",)
    min_sources: int = 1
    freshness: tuple[str, ...] = ("fresh", "fetched_unknown_age")
    required_evidence_terms: tuple[str, ...] = ()
    min_evidence_coverage: float = 1.0
    max_latency_ms: float | None = None
    max_provider_errors: int = 0
    metadata: JsonDict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: JsonDict) -> CurrentInfoEvalCase:
        expected = dict(payload.get("expected") or {})
        request = CurrentInfoRequest.from_dict(dict(payload.get("request") or {}))
        return cls(
            case_id=str(payload.get("case_id", "") or ""),
            query_type=str(payload.get("query_type", "") or ""),
            request=request,
            search_results=tuple(SearchResult.from_dict(dict(item)) for item in payload.get("search_results") or ()),
            search_metrics=tuple(
                SearchProviderMetric.from_dict(dict(item)) for item in payload.get("search_metrics") or ()
            ),
            documents=tuple(FetchedDocument.from_dict(dict(item)) for item in payload.get("documents") or ()),
            chunks=tuple(EvidenceChunk.from_dict(dict(item)) for item in payload.get("chunks") or ()),
            expected_statuses=_str_tuple(expected.get("statuses"), default=("answered",)),
            min_sources=_non_negative_int(expected.get("min_sources"), default=1),
            freshness=_str_tuple(expected.get("freshness"), default=("fresh", "fetched_unknown_age")),
            required_evidence_terms=_str_tuple(expected.get("required_evidence_terms"), default=()),
            min_evidence_coverage=_bounded_float(expected.get("min_evidence_coverage"), default=1.0),
            max_latency_ms=_optional_positive_float(expected.get("max_latency_ms")),
            max_provider_errors=_non_negative_int(expected.get("max_provider_errors"), default=0),
            metadata=dict(payload.get("metadata") or {}),
        )

    def to_dict(self) -> JsonDict:
        expected: JsonDict = {
            "statuses": list(self.expected_statuses),
            "min_sources": self.min_sources,
            "freshness": list(self.freshness),
            "required_evidence_terms": list(self.required_evidence_terms),
            "min_evidence_coverage": self.min_evidence_coverage,
            "max_provider_errors": self.max_provider_errors,
        }
        if self.max_latency_ms is not None:
            expected["max_latency_ms"] = self.max_latency_ms
        return {
            "case_id": self.case_id,
            "query_type": self.query_type,
            "request": self.request.to_dict(),
            "search_results": [item.to_dict() for item in self.search_results],
            "search_metrics": [item.to_dict() for item in self.search_metrics],
            "documents": [item.to_dict() for item in self.documents],
            "chunks": [item.to_dict() for item in self.chunks],
            "expected": expected,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class CurrentInfoEvalMetrics:
    status: str
    source_count: int
    fetched_source_count: int
    freshness: str
    evidence_coverage: float
    latency_ms: float
    provider_error_count: int
    warning_count: int
    confidence: float

    def to_dict(self) -> JsonDict:
        return {
            "status": self.status,
            "source_count": self.source_count,
            "fetched_source_count": self.fetched_source_count,
            "freshness": self.freshness,
            "evidence_coverage": round(self.evidence_coverage, 3),
            "latency_ms": round(self.latency_ms, 3),
            "provider_error_count": self.provider_error_count,
            "warning_count": self.warning_count,
            "confidence": round(self.confidence, 3),
        }


@dataclass(frozen=True, slots=True)
class CurrentInfoEvalResult:
    case_id: str
    query_type: str
    passed: bool
    metrics: CurrentInfoEvalMetrics
    failed_checks: tuple[str, ...] = ()

    def to_dict(self) -> JsonDict:
        return {
            "case_id": self.case_id,
            "query_type": self.query_type,
            "passed": self.passed,
            "metrics": self.metrics.to_dict(),
            "failed_checks": list(self.failed_checks),
        }


@dataclass(frozen=True, slots=True)
class CurrentInfoEvalReport:
    mode: str
    total: int
    passed: int
    failed: int
    by_query_type: JsonDict
    results: tuple[CurrentInfoEvalResult, ...]

    def to_dict(self) -> JsonDict:
        return {
            "mode": self.mode,
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "by_query_type": self.by_query_type,
            "results": [item.to_dict() for item in self.results],
        }


def load_current_info_eval_cases(path: str | Path) -> tuple[CurrentInfoEvalCase, ...]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = payload.get("cases") if isinstance(payload, dict) else payload
    if not isinstance(cases, list):
        raise ValueError("current-info eval fixture must contain a list or a {'cases': [...]} object")
    parsed = tuple(CurrentInfoEvalCase.from_dict(dict(item)) for item in cases)
    empty_ids = [case.case_id for case in parsed if not case.case_id.strip()]
    if empty_ids:
        raise ValueError("current-info eval cases require non-empty case_id values")
    duplicate_ids = _duplicates(case.case_id for case in parsed)
    if duplicate_ids:
        raise ValueError(f"duplicate current-info eval case_id values: {', '.join(duplicate_ids)}")
    return parsed


def run_current_info_eval_cases(
    cases: Sequence[CurrentInfoEvalCase],
    *,
    mode: str = "local",
    clock: Clock | None = None,
) -> CurrentInfoEvalReport:
    if mode != "local":
        raise ValueError("live current-info evals must use a separate explicit runner; local fixtures never call live providers")

    timer = clock or _StableLocalEvalClock()
    results = tuple(_run_case(case, clock=timer) for case in sorted(cases, key=lambda item: item.case_id))
    passed = sum(1 for result in results if result.passed)
    return CurrentInfoEvalReport(
        mode=mode,
        total=len(results),
        passed=passed,
        failed=len(results) - passed,
        by_query_type=_query_type_summary(results),
        results=results,
    )


def run_current_info_eval_fixture(
    path: str | Path,
    *,
    mode: str = "local",
    clock: Clock | None = None,
) -> CurrentInfoEvalReport:
    return run_current_info_eval_cases(load_current_info_eval_cases(path), mode=mode, clock=clock)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Current-Info answer-quality evals.")
    parser.add_argument("fixture", type=Path, help="JSON fixture with deterministic local eval cases.")
    parser.add_argument("--mode", choices=("local", "live"), default="local", help="Eval mode. Local is deterministic.")
    parser.add_argument("--json", action="store_true", help="Print a stable JSON report.")
    parser.add_argument("--jsonl", action="store_true", help="Print one stable JSON object per case.")
    args = parser.parse_args(argv)

    if args.mode == "live":
        parser.error("live evals are intentionally separated from deterministic local fixture evals")

    report = run_current_info_eval_fixture(args.fixture, mode=args.mode)
    if args.jsonl:
        for result in report.results:
            print(json.dumps(result.to_dict(), sort_keys=True, separators=(",", ":")))
    elif args.json:
        print(json.dumps(report.to_dict(), sort_keys=True, indent=2))
    else:
        print(f"Current-Info eval: {report.passed}/{report.total} passed ({report.failed} failed)")
        for result in report.results:
            state = "PASS" if result.passed else "FAIL"
            checks = "" if result.passed else f" failed_checks={','.join(result.failed_checks)}"
            print(f"{state} {result.case_id} [{result.query_type}]{checks}")
    return 0 if report.failed == 0 else 1


class _StableLocalEvalClock:
    """Deterministic timer for comparable local fixture reports."""

    def __init__(self, *, step_seconds: float = 0.001) -> None:
        self._value = 0.0
        self._step_seconds = step_seconds

    def __call__(self) -> float:
        self._value += self._step_seconds
        return self._value


class _FixtureSearchProvider:
    def __init__(self, case: CurrentInfoEvalCase) -> None:
        self._case = case

    def search(self, *, query: str, locale: str, max_results: int) -> SearchProviderResponse:
        del query, locale
        return SearchProviderResponse(
            results=self._case.search_results[:max_results],
            metrics=self._case.search_metrics,
        )


class _FixtureFetchProvider:
    def __init__(self, documents: tuple[FetchedDocument, ...]) -> None:
        self._documents = {document.url: document for document in documents}

    def fetch(self, *, url: str, locale: str) -> FetchedDocument | None:
        del locale
        return self._documents.get(url)


class _FixtureRetrievalProvider:
    def __init__(self, chunks: tuple[EvidenceChunk, ...]) -> None:
        self._chunks = chunks

    def retrieve(
        self,
        *,
        request: CurrentInfoRequest,
        documents: tuple[FetchedDocument, ...],
        search_results: tuple[SearchResult, ...],
    ) -> tuple[EvidenceChunk, ...]:
        del request, documents, search_results
        return self._chunks


def _run_case(case: CurrentInfoEvalCase, *, clock: Clock) -> CurrentInfoEvalResult:
    service = CurrentInfoService(
        search_provider=_FixtureSearchProvider(case),
        fetch_provider=_FixtureFetchProvider(case.documents),
        retrieval_provider=_FixtureRetrievalProvider(case.chunks),
    )
    start = clock()
    answer = service.answer(case.request)
    latency_ms = max(0.0, (clock() - start) * 1000.0)
    metrics = _measure(case=case, answer=answer, latency_ms=latency_ms)
    failed_checks = _failed_checks(case=case, metrics=metrics)
    return CurrentInfoEvalResult(
        case_id=case.case_id,
        query_type=case.query_type,
        passed=not failed_checks,
        metrics=metrics,
        failed_checks=tuple(failed_checks),
    )


def _measure(*, case: CurrentInfoEvalCase, answer: CurrentInfoAnswer, latency_ms: float) -> CurrentInfoEvalMetrics:
    evidence = answer.evidence
    evidence_text = " ".join(
        (
            answer.answer_text,
            " ".join(chunk.text for chunk in evidence.chunks) if evidence is not None else "",
        )
    )
    fetched_sources = tuple(source for source in (evidence.sources if evidence is not None else ()) if source.fetched)
    source_count = len(tuple(dict.fromkeys(answer.sources)))
    if evidence is not None and evidence.sources:
        source_count = max(source_count, len(tuple(dict.fromkeys(source.url for source in evidence.sources if source.url))))
    metrics = answer.search_bundle.metrics if answer.search_bundle is not None else case.search_metrics
    return CurrentInfoEvalMetrics(
        status=answer.status,
        source_count=source_count,
        fetched_source_count=len(fetched_sources),
        freshness=evidence.freshness if evidence is not None else "unknown",
        evidence_coverage=_evidence_coverage(evidence_text, case.required_evidence_terms),
        latency_ms=latency_ms,
        provider_error_count=sum(1 for metric in metrics if metric.error_class),
        warning_count=len(answer.warnings),
        confidence=answer.confidence,
    )


def _failed_checks(*, case: CurrentInfoEvalCase, metrics: CurrentInfoEvalMetrics) -> list[str]:
    failures: list[str] = []
    if metrics.status not in case.expected_statuses:
        failures.append("status")
    if metrics.source_count < case.min_sources:
        failures.append("sources_present")
    if metrics.freshness not in case.freshness:
        failures.append("freshness")
    if metrics.evidence_coverage < case.min_evidence_coverage:
        failures.append("evidence_coverage")
    if case.max_latency_ms is not None and metrics.latency_ms > case.max_latency_ms:
        failures.append("latency")
    if metrics.provider_error_count > case.max_provider_errors:
        failures.append("provider_errors")
    return failures


def _evidence_coverage(text: str, required_terms: tuple[str, ...]) -> float:
    if not required_terms:
        return 1.0
    normalized = text.casefold()
    covered = sum(1 for term in required_terms if term.casefold() in normalized)
    return covered / len(required_terms)


def _query_type_summary(results: tuple[CurrentInfoEvalResult, ...]) -> JsonDict:
    summary: JsonDict = {}
    for result in results:
        item = summary.setdefault(result.query_type, {"total": 0, "passed": 0, "failed": 0})
        item["total"] += 1
        if result.passed:
            item["passed"] += 1
        else:
            item["failed"] += 1
    return {key: summary[key] for key in sorted(summary)}


def _duplicates(values: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for value in values:
        if value in seen and value not in duplicates:
            duplicates.append(value)
        seen.add(value)
    return tuple(duplicates)


def _str_tuple(value: Any, *, default: tuple[str, ...]) -> tuple[str, ...]:
    if value is None:
        return default
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value if item is not None)


def _non_negative_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, parsed)


def _bounded_float(value: Any, *, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(parsed, 1.0))


def _optional_positive_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, parsed)


if __name__ == "__main__":
    sys.exit(main())
