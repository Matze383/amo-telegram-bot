from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import select

from amo_bot.ai.learning_feedback import LearningFeedbackScope, LearningFeedbackService
from amo_bot.ai.webtool_subagent import WebtoolOperationType, WebtoolSubagentRequest, create_webtool_subagent_service
from amo_bot.auth.roles import Role
from amo_bot.db.base import create_session_factory
from amo_bot.db.init_db import init_db
from amo_bot.db.models import ResearchEvalCase, ResearchSourceObservation
from amo_bot.db.repositories import (
    ResearchEvalCaseRepository,
    ResearchSourceObservationRepository,
    RetrievableMemoryRepository,
    WebToolRoleQuotaRepository,
)
from amo_bot.telegram.research_eval_harness import build_research_eval_report, run_research_eval_cases
from amo_bot.telegram.webtool_evidence import DomainEvidenceResult, EvidenceSource
from amo_bot.telegram.webtool_research_orchestrator import (
    _assess_chain_source_quality,
    _format_news_insufficient_sources_response,
)


class _WeatherProvider:
    def __init__(self, result: DomainEvidenceResult):
        self.result = result

    def get_weather(self, *, query: str, locale: str) -> DomainEvidenceResult:
        return self.result


class _FailingWeatherProvider:
    def get_weather(self, *, query: str, locale: str) -> DomainEvidenceResult:
        raise RuntimeError("contains secret token=abc123")


class _JsPlaceholderScrapeProvider:
    def fetch(self, *, url: str, timeout_seconds: float):
        return {
            "url": url,
            "status_code": 200,
            "headers": {"content-type": "text/html"},
            "text": "Please enable JavaScript to view this app. Loading...",
        }


def _session_factory(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'research_observations.sqlite3'}"
    init_db(database_url)
    return create_session_factory(database_url)


def _request(*, operation_type: str = WebtoolOperationType.WEATHER_EVIDENCE) -> WebtoolSubagentRequest:
    return WebtoolSubagentRequest(
        operation_type=operation_type,
        user_id=42,
        role=Role.OWNER,
        chat_id=-100,
        topic_id=7,
        day="2026-06-11",
        query="weather in Berlin token=abc123",
        url="https://leaky.example/path?token=abc123",
        locale="en",
    )


def test_observation_repository_stores_metadata_only_and_reduces_urls_to_hosts(tmp_path):
    session_factory = _session_factory(tmp_path)

    with session_factory() as session:
        ResearchSourceObservationRepository(session).record_observation(
            provider_name="provider://bad value",
            source_name="Source Name",
            domain="weather",
            outcome="confirmed",
            confidence=1.2,
            source_urls=("https://api.example.com/path?q=raw-query&token=abc123",),
            source_hosts=("www.source.example",),
            warning_codes=("market prices are volatile!",),
            error_class="RuntimeError: token=abc123",
            timing_ms=12,
            metadata={"query": "raw query", "url": "https://leak.example/path", "custom_status": "ok"},
        )
        row = session.scalar(select(ResearchSourceObservation))

    assert row is not None
    assert row.provider_name == "unknown_provider"
    assert row.confidence == 1.0
    payload = json.loads(row.metadata_json or "{}")
    assert payload["source_count"] == 2
    assert payload["warning_count"] == 1
    assert payload["source_hosts"] == ["source.example", "api.example.com"]
    stored = f"{row.warning_codes_json}\n{row.metadata_json}"
    assert "raw query" not in stored
    assert "https://api.example.com/path" not in stored
    assert "token=abc123" not in stored
    assert "custom_status" in stored


def test_recent_source_observations_mark_conflicting_news_host_as_unusable(tmp_path):
    session_factory = _session_factory(tmp_path)
    with session_factory() as session:
        repo = ResearchSourceObservationRepository(session)
        repo.record_observation(
            provider_name="webtool_chain",
            domain="news",
            outcome="confirmed",
            source_hosts=("reliable.example",),
            warning_codes=(),
        )
        repo.record_observation(
            provider_name="webtool_chain",
            domain="news",
            outcome="confirmed",
            source_hosts=("conflict.example",),
            warning_codes=("source_conflict",),
        )

        class _Reader:
            def assess_hosts(self, *, domain: str, hosts: tuple[str, ...]):
                return repo.assess_recent_hosts(domain=domain, source_hosts=hosts)

        source_quality = _assess_chain_source_quality(
            domain="news",
            extracts=(
                ("webscraping", "reliable.example", "confirmed news text"),
                ("webscraping", "conflict.example", "conflicting news text"),
            ),
            reader=_Reader(),
        )

    assert source_quality is not None
    assert source_quality["usable_hosts"] == ("reliable.example",)
    assert source_quality["conflict_hosts"] == ("conflict.example",)
    response = _format_news_insufficient_sources_response(
        request_text="latest news about Python",
        extract_hosts=("reliable.example", "conflict.example"),
        locale="en",
        source_quality=source_quality,
    )
    assert "cannot reliably confirm" in response
    assert "conflicting recent evidence" in response


def test_observation_repository_redacts_secret_url_and_rawish_metadata_inputs(tmp_path):
    session_factory = _session_factory(tmp_path)

    with session_factory() as session:
        ResearchSourceObservationRepository(session).record_observation(
            provider_name="https://token_abc123_https.secret.example/provider",
            source_name="Bearer abc123secret",
            domain="https://secret.example/weather?q=berlin",
            outcome="RuntimeError:_token_abc123",
            confidence=0.5,
            source_urls=("https://safe.example/path?token=abc123",),
            warning_codes=("RuntimeError:_token_abc123", "normal_warning"),
            error_class="RuntimeError:_token_abc123 https://secret.example/path",
            metadata={
                "non_blocked_metadata": "token_abc123_https://secret.example/path",
                "custom_status": "weather in Berlin raw user message",
                "safe_code": "quota_exceeded",
                "count": 1,
                "other": "https://secret.example/path",
                "Authorization": "Bearer abc123secret",
            },
        )
        row = session.scalar(select(ResearchSourceObservation))

    assert row is not None
    assert row.provider_name == "unknown_provider"
    assert row.source_name is None
    assert row.domain == "generic"
    assert row.outcome == "unknown"
    payload = json.loads(row.metadata_json or "{}")
    assert payload["error_class"] == "RuntimeError"
    assert payload["non_blocked_metadata"] == "redacted"
    assert payload["safe_code"] == "quota_exceeded"
    assert payload["count"] == 1
    assert "Authorization" not in payload
    stored = f"{row.provider_name}\n{row.source_name}\n{row.domain}\n{row.outcome}\n{row.warning_codes_json}\n{row.metadata_json}"
    for forbidden in (
        "token_abc123",
        "abc123secret",
        "https://",
        "secret.example",
        "weather in Berlin",
        "raw user message",
    ):
        assert forbidden not in stored


def test_eval_case_repository_redacts_secret_and_url_like_inputs(tmp_path):
    session_factory = _session_factory(tmp_path)

    with session_factory() as session:
        ResearchEvalCaseRepository(session).create_from_negative_feedback(
            sanitized_summary="Bitte prüfe https://secret.example/path token=abc123 und raw message.",
            failure_label="source_quality_feedback",
            domain="https://secret.example/weather",
            locale="de",
            evidence_status="RuntimeError:_token_abc123",
            source_hosts=("https://safe.example/path?api_key=abc123",),
            expected_behavior="Do not leak Bearer abc123secret or https://secret.example/path.",
        )
        row = session.scalar(select(ResearchEvalCase))

    assert row is not None
    assert row.domain == "generic"
    assert row.expected_status is None or "token_abc123" not in row.expected_status
    stored = f"{row.case_key}\n{row.domain}\n{row.sanitized_prompt}\n{row.expected_status}\n{row.expected_metadata_json}"
    for forbidden in ("https://", "secret.example", "token=abc123", "abc123secret", "api_key=abc123"):
        assert forbidden not in stored


def test_webtool_evidence_success_writes_source_observation(tmp_path):
    session_factory = _session_factory(tmp_path)
    fetched_at = datetime.now(UTC).replace(microsecond=0).isoformat()
    evidence = DomainEvidenceResult(
        domain="weather",
        status="confirmed",
        confidence=0.91,
        text=f"Ort: Berlin. Stand: {fetched_at}. Temperatur 21 C.",
        sources=(EvidenceSource("Open-Meteo", "https://api.open-meteo.com/v1/forecast?secret=abc", fetched_at),),
        warnings=("weather_code_requires_interpretation",),
    )

    with session_factory() as session:
        service = create_webtool_subagent_service(
            quota_repo=WebToolRoleQuotaRepository(session),
            weather_evidence_provider=_WeatherProvider(evidence),
            observation_writer=ResearchSourceObservationRepository(session),
        )
        result = service.execute(_request())
        row = session.scalar(select(ResearchSourceObservation))

    assert result.allowed is True
    assert row is not None
    assert row.provider_name == "weather_evidence_provider"
    assert row.source_name == "Open-Meteo"
    assert row.domain == "weather"
    assert row.outcome == "confirmed"
    assert row.confidence == 0.91
    payload = json.loads(row.metadata_json or "{}")
    assert payload["source_hosts"] == ["api.open-meteo.com"]
    stored = f"{row.warning_codes_json}\n{row.metadata_json}"
    assert "weather in Berlin" not in stored
    assert "https://api.open-meteo.com" not in stored
    assert "secret=abc" not in stored


def test_webtool_evidence_unconfirmed_and_error_write_fail_closed_observations(tmp_path):
    session_factory = _session_factory(tmp_path)
    unconfirmed = DomainEvidenceResult(
        domain="weather",
        status="unavailable",
        confidence=0.0,
        text="",
        warnings=("weather_location_not_found",),
    )

    with session_factory() as session:
        service = create_webtool_subagent_service(
            quota_repo=WebToolRoleQuotaRepository(session),
            weather_evidence_provider=_WeatherProvider(unconfirmed),
            observation_writer=ResearchSourceObservationRepository(session),
        )
        unconfirmed_result = service.execute(_request())

        error_service = create_webtool_subagent_service(
            quota_repo=WebToolRoleQuotaRepository(session),
            weather_evidence_provider=_FailingWeatherProvider(),
            observation_writer=ResearchSourceObservationRepository(session),
        )
        error_result = error_service.execute(_request())
        rows = session.scalars(select(ResearchSourceObservation).order_by(ResearchSourceObservation.id.asc())).all()

    assert unconfirmed_result.allowed is False
    assert error_result.allowed is False
    assert [row.outcome for row in rows] == ["unavailable", "execution_error"]
    error_payload = json.loads(rows[1].metadata_json or "{}")
    assert error_payload["error_class"] == "RuntimeError"
    stored = "\n".join(f"{row.warning_codes_json}\n{row.metadata_json}" for row in rows)
    assert "weather in Berlin" not in stored
    assert "leaky.example/path" not in stored
    assert "token=abc123" not in stored


def test_webtool_provider_unavailable_writes_fail_closed_observation(tmp_path):
    session_factory = _session_factory(tmp_path)

    with session_factory() as session:
        service = create_webtool_subagent_service(
            quota_repo=WebToolRoleQuotaRepository(session),
            observation_writer=ResearchSourceObservationRepository(session),
        )
        result = service.execute(_request())
        row = session.scalar(select(ResearchSourceObservation))

    assert result.allowed is False
    assert result.decision == "provider_unavailable"
    assert row is not None
    assert row.outcome == "weather_provider_not_configured"
    assert row.domain == "weather"
    assert json.loads(row.metadata_json or "{}")["source_count"] == 0


def test_unusable_scrape_writes_extraction_quality_warning_observation(tmp_path):
    session_factory = _session_factory(tmp_path)

    with session_factory() as session:
        service = create_webtool_subagent_service(
            quota_repo=WebToolRoleQuotaRepository(session),
            scrape_provider=_JsPlaceholderScrapeProvider(),
            observation_writer=ResearchSourceObservationRepository(session),
        )
        result = service.execute(_request(operation_type=WebtoolOperationType.WEBSCRAPING))
        row = session.scalar(select(ResearchSourceObservation))

    assert result.allowed is True
    assert "extraction_js_placeholder" in result.metadata["warning_codes"]
    assert row is not None
    assert row.provider_name == "webscrape_provider"
    assert row.outcome == "scrape_completed"
    warnings = json.loads(row.warning_codes_json or "[]")
    assert "extraction_js_placeholder" in warnings
    payload = json.loads(row.metadata_json or "{}")
    assert payload["source_hosts"] == ["leaky.example"]
    stored = f"{row.warning_codes_json}\n{row.metadata_json}"
    assert "Please enable JavaScript" not in stored
    assert "leaky.example/path" not in stored
    assert "token=abc123" not in stored


def test_webtool_role_disabled_and_quota_denied_write_dispatcher_observations(tmp_path):
    session_factory = _session_factory(tmp_path)

    with session_factory() as session:
        quota_repo = WebToolRoleQuotaRepository(session)
        quota_repo.upsert_role_quota(role=Role.NORMAL, mode="limited", daily_limit=1)
        service = create_webtool_subagent_service(
            quota_repo=quota_repo,
            use_fake_providers=True,
            observation_writer=ResearchSourceObservationRepository(session),
        )
        disabled_result = service.execute(WebtoolSubagentRequest(
            operation_type=WebtoolOperationType.WEBSEARCH,
            user_id=42,
            role=Role.IGNORE,
            chat_id=-100,
            topic_id=7,
            day="2026-06-11",
            query="secret raw query token=abc123",
        ))
        allowed_result = service.execute(WebtoolSubagentRequest(
            operation_type=WebtoolOperationType.WEBSEARCH,
            user_id=43,
            role=Role.NORMAL,
            chat_id=-100,
            topic_id=7,
            day="2026-06-11",
            query="quota warmup",
        ))
        quota_result = service.execute(WebtoolSubagentRequest(
            operation_type=WebtoolOperationType.WEBSEARCH,
            user_id=43,
            role=Role.NORMAL,
            chat_id=-100,
            topic_id=7,
            day="2026-06-11",
            query="another secret raw query token=abc123",
        ))
        rows = session.scalars(select(ResearchSourceObservation).order_by(ResearchSourceObservation.id.asc())).all()

    assert disabled_result.allowed is False
    assert allowed_result.allowed is True
    assert quota_result.allowed is False
    denied_rows = [row for row in rows if row.provider_name == "webtool_dispatcher"]
    assert [row.domain for row in denied_rows] == ["webtool_dispatcher", "webtool_dispatcher"]
    assert [row.outcome for row in denied_rows] == ["denied", "denied"]
    payloads = [json.loads(row.metadata_json or "{}") for row in denied_rows]
    assert payloads[0]["reason"] == "role_disabled"
    assert payloads[1]["decision"] == "quota_exceeded"
    stored = "\n".join(f"{row.warning_codes_json}\n{row.metadata_json}" for row in denied_rows)
    assert "secret raw query" not in stored
    assert "token=abc123" not in stored


def test_negative_learning_feedback_creates_sanitized_research_eval_case(tmp_path):
    session_factory = _session_factory(tmp_path)
    raw_feedback = "Quelle https://bad.example/path ist schlecht, das war falsch."

    with session_factory() as session:
        result = LearningFeedbackService(
            RetrievableMemoryRepository(session),
            eval_case_writer=ResearchEvalCaseRepository(session),
        ).process_text_feedback(
            text=raw_feedback,
            scope=LearningFeedbackScope(chat_id=-100, message_thread_id=7, user_id=42),
            user_id=42,
        )
        row = session.scalar(select(ResearchEvalCase))

    assert result.stored is True
    assert row is not None
    assert row.domain == "source_quality"
    assert row.case_key.startswith("feedback:source_quality:")
    assert row.expected_status == "needs_improvement"
    stored = f"{row.sanitized_prompt}\n{row.expected_metadata_json}"
    assert "bad.example/path" not in stored
    assert "das war falsch" not in stored
    metadata = json.loads(row.expected_metadata_json or "{}")
    assert metadata["failure_label"] == "source_quality_feedback"
    assert metadata["source_hosts"] == []


def test_enabled_research_eval_cases_run_through_local_harness(tmp_path):
    session_factory = _session_factory(tmp_path)
    with session_factory() as session:
        repo = ResearchEvalCaseRepository(session)
        case = repo.create_from_negative_feedback(
            sanitized_summary="Aktuelle News zu Python waren aus nur einer Quelle.",
            failure_label="source_quality_feedback",
            domain="news",
            locale="de",
            evidence_status="needs_multi_source_web",
            source_hosts=("news.example",),
        )
        results = run_research_eval_cases(repo)

    assert len(results) == 1
    assert results[0].case_key == case.case_key
    assert results[0].domain == "news"
    assert results[0].prompt_domain == "news"
    assert results[0].auto_research_enabled is True
    assert results[0].would_chain is True
    assert results[0].passed is True


def test_research_eval_case_marks_weak_initial_source_as_followup_planned(tmp_path):
    session_factory = _session_factory(tmp_path)
    with session_factory() as session:
        repo = ResearchEvalCaseRepository(session)
        case = repo.create_from_negative_feedback(
            sanitized_summary="Latest News zu Python wurden nur aus einer Quelle beantwortet.",
            failure_label="source_quality_feedback",
            domain="news",
            locale="de",
            evidence_status="low_quality",
            source_hosts=("single-source.example",),
        )
        results = run_research_eval_cases(repo)

    assert len(results) == 1
    assert results[0].case_key == case.case_key
    assert results[0].domain == "news"
    assert results[0].would_chain is True
    assert results[0].would_followup_on_weak_initial_evidence is True
    assert results[0].passed is True
    assert results[0].reason == "weak_initial_evidence_would_plan_followup"


def test_positive_source_preference_does_not_create_research_eval_case(tmp_path):
    session_factory = _session_factory(tmp_path)

    with session_factory() as session:
        result = LearningFeedbackService(
            RetrievableMemoryRepository(session),
            eval_case_writer=ResearchEvalCaseRepository(session),
        ).process_text_feedback(
            text="Quelle example.org ist besser, nimm die künftig.",
            scope=LearningFeedbackScope(chat_id=-100, message_thread_id=7, user_id=42),
            user_id=42,
        )
        row = session.scalar(select(ResearchEvalCase))

    assert result.stored is True
    assert row is None


def test_research_eval_case_classifies_domain_and_failure_without_raw_leaks(tmp_path):
    session_factory = _session_factory(tmp_path)

    with session_factory() as session:
        repo = ResearchEvalCaseRepository(session)
        case = repo.create_from_negative_feedback(
            sanitized_summary=(
                "BTC Kurs war falsch und nur eine Quelle; "
                "https://bad.example/path?token=abc123 wurde kopiert."
            ),
            failure_label="negative_answer_feedback",
            domain="generic",
            evidence_status="needs_improvement",
            source_hosts=("https://bad.example/path?token=abc123", "good.example"),
        )
        row = session.scalar(select(ResearchEvalCase))

    assert row is not None
    assert case.domain == "crypto"
    metadata = json.loads(row.expected_metadata_json or "{}")
    assert metadata["failure_class"] == "source_quality"
    stored = f"{row.sanitized_prompt}\n{row.expected_metadata_json}"
    assert "bad.example/path" not in stored
    assert "token=abc123" not in stored
    assert "https://bad.example" not in stored
    assert metadata["source_hosts"] == ["bad.example", "good.example"]


def test_research_eval_cases_deduplicate_by_sanitized_failure_metadata(tmp_path):
    session_factory = _session_factory(tmp_path)

    with session_factory() as session:
        repo = ResearchEvalCaseRepository(session)
        first = repo.create_from_negative_feedback(
            sanitized_summary="Wetter Berlin war veraltet und nicht aktuell.",
            failure_label="negative_answer_feedback",
            domain="generic",
            evidence_status="needs_improvement",
        )
        second = repo.create_from_negative_feedback(
            sanitized_summary="Wetter Berlin war veraltet und nicht aktuell.",
            failure_label="negative_answer_feedback",
            domain="generic",
            evidence_status="needs_improvement",
        )
        rows = session.scalars(select(ResearchEvalCase)).all()

    assert first.case_key == second.case_key
    assert len(rows) == 1
    assert rows[0].domain == "weather"
    metadata = json.loads(rows[0].expected_metadata_json or "{}")
    assert metadata["failure_class"] == "stale_data"


def test_research_eval_report_splits_routing_source_quality_and_answer_risk(tmp_path):
    session_factory = _session_factory(tmp_path)

    with session_factory() as session:
        repo = ResearchEvalCaseRepository(session)
        repo.create_from_negative_feedback(
            sanitized_summary="Latest News zu Python wurden nur aus einer Quelle beantwortet.",
            failure_label="source_quality_feedback",
            domain="news",
            evidence_status="low_quality",
            source_hosts=("single-source.example",),
        )
        repo.create_from_negative_feedback(
            sanitized_summary="Das war falsch und nicht korrekt, aber ohne Recherche-Bezug.",
            failure_label="negative_answer_feedback",
            domain="generic",
            evidence_status="needs_improvement",
        )
        report = build_research_eval_report(repo)

    assert report.total == 2
    assert report.source_quality_pass == 1
    assert report.source_quality_fail == 0
    assert report.answer_quality_risk == 1
    assert report.routing_fail == 1
    assert {result.failure_class for result in report.results} == {"source_quality", "incorrect_answer"}
