from dataclasses import dataclass

from amo_bot.ai import (
    CapabilityActor,
    CapabilityActorType,
    CapabilityAuditEventStatus,
    CapabilityCallEnvelope,
    CapabilityDecisionResult,
    CapabilityInputSummaryItem,
    CapabilityScope,
    CapabilityScopeType,
    CapabilityAuditTrail,
    InMemoryCapabilityAuditSink,
    validate_capability_call_envelope,
)


def _envelope() -> CapabilityCallEnvelope:
    return CapabilityCallEnvelope(
        actor=CapabilityActor(actor_type=CapabilityActorType.AI, actor_id="ai-router"),
        scope=CapabilityScope(scope_type=CapabilityScopeType.TOPIC, scope_id="topic-42"),
        capability_name="ki.memory.read",
        capability_version="1.0.0",
        input_summary=(
            CapabilityInputSummaryItem(key="query", value_type="text", approx_size=120),
        ),
        request_id="req-123",
        risk_flags=("memory_access",),
    )


def test_cp_a4_audit_records_requested_then_denied() -> None:
    sink = InMemoryCapabilityAuditSink()
    decision = validate_capability_call_envelope(
        _envelope(),
        expected_capability_name="ki.memory.read",
        expected_capability_version="1.0.0",
        audit_recorder=sink,
    )

    assert decision.result == CapabilityDecisionResult.DENY
    assert [event.status for event in sink.events] == [
        CapabilityAuditEventStatus.REQUESTED,
        CapabilityAuditEventStatus.DENIED,
    ]
    assert sink.events[0].summary == "request_received"
    assert sink.events[1].summary == "policy_decision"


def test_cp_a4_audit_records_allowed_with_safe_details() -> None:
    sink = InMemoryCapabilityAuditSink()
    decision = validate_capability_call_envelope(
        _envelope(),
        expected_capability_name="ki.memory.read",
        expected_capability_version="1.0.0",
        policy_result_hint=CapabilityDecisionResult.ALLOW_WITH_REDACTION,
        audit_recorder=sink,
    )

    assert decision.result == CapabilityDecisionResult.ALLOW_WITH_REDACTION
    assert sink.events[-1].status == CapabilityAuditEventStatus.ALLOWED
    detail_keys = {key for key, _ in sink.events[-1].details}
    assert "decision_result" in detail_keys
    assert "reason_code" in detail_keys


def test_cp_a4_execution_completed_and_failed_events() -> None:
    sink = InMemoryCapabilityAuditSink()
    trail = CapabilityAuditTrail(recorder=sink)

    completed = trail.record_completed(
        request_id="req-200",
        capability_name="ki.memory.read",
        capability_version="1.0.0",
    )
    failed = trail.record_failed(
        request_id="req-201",
        capability_name="ki.memory.read",
        capability_version="1.0.0",
        error_code="Timeout Error: remote endpoint",
    )

    assert completed.status == CapabilityAuditEventStatus.COMPLETED
    assert failed.status == CapabilityAuditEventStatus.FAILED
    assert failed.reason_code == "timeouterrorremoteendpoint"
    assert sink.events[-2].status == CapabilityAuditEventStatus.COMPLETED
    assert sink.events[-1].status == CapabilityAuditEventStatus.FAILED


@dataclass(slots=True)
class _RaisingRecorder:
    def record(self, event) -> None:  # type: ignore[no-untyped-def]
        raise RuntimeError("sink down")


def test_cp_a4_policy_decision_survives_audit_recorder_failure() -> None:
    decision = validate_capability_call_envelope(
        _envelope(),
        expected_capability_name="ki.memory.read",
        expected_capability_version="1.0.0",
        policy_result_hint=CapabilityDecisionResult.ALLOW,
        audit_recorder=_RaisingRecorder(),
    )

    assert decision.result == CapabilityDecisionResult.ALLOW
    assert decision.reason_code == "policy_allow"


def test_cp_a4_policy_decision_survives_audit_sink_limit() -> None:
    sink = InMemoryCapabilityAuditSink()
    trail = CapabilityAuditTrail(recorder=sink)
    for i in range(10_000):
        trail.record_requested(
            request_id=f"seed-{i}",
            capability_name="ki.memory.read",
            capability_version="1.0.0",
            actor_type="ai",
            scope_type="topic",
            input_summary_count=0,
            input_summary_approx_bytes=0,
            risk_flags_count=0,
        )

    assert len(sink.events) == 10_000

    decision = validate_capability_call_envelope(
        _envelope(),
        expected_capability_name="ki.memory.read",
        expected_capability_version="9.9.9",
        audit_recorder=sink,
    )

    assert decision.result == CapabilityDecisionResult.DENY
    assert decision.reason_code == "capability_version_mismatch"
    assert len(sink.events) == 10_000


def test_cp_a4_details_and_summary_are_bounded_and_redacted() -> None:
    sink = InMemoryCapabilityAuditSink()
    trail = CapabilityAuditTrail(recorder=sink)

    trail.record_requested(
        request_id="req-500",
        capability_name="ki.memory.read",
        capability_version="1.0.0",
        actor_type="ai",
        scope_type="topic",
        input_summary_count=999,
        input_summary_approx_bytes=10_000,
        risk_flags_count=77,
    )

    event = sink.events[0]
    assert event.summary == "request_received"
    assert len(event.summary) <= 256
    assert all(key != "raw_payload" for key, _ in event.details)
    assert all("cpa4_synthetic_sensitive_payload_do_not_log" not in value.lower() for _, value in event.details)


def test_cp_a4_record_failed_redacts_synthetic_sensitive_error_payload() -> None:
    sink = InMemoryCapabilityAuditSink()
    trail = CapabilityAuditTrail(recorder=sink)

    raw_marker = "CPA4_SYNTHETIC_SENSITIVE_PAYLOAD_DO_NOT_LOG__credential_like_value__sql_payload"
    failed = trail.record_failed(
        request_id="req-sensitive-failed",
        capability_name="ki.memory.read",
        capability_version="1.0.0",
        error_code=raw_marker,
    )

    event = sink.events[-1]
    assert failed.status == CapabilityAuditEventStatus.FAILED
    assert event.summary == "execution_failed"
    assert raw_marker not in failed.reason_code
    assert raw_marker not in (event.reason_code or "")
    assert raw_marker not in event.summary
    assert all(raw_marker not in value for _, value in event.details)
    assert all(
        disallowed_piece not in (event.reason_code or "")
        for disallowed_piece in (" ", "=", "*", "-")
    )


def test_cp_a4_record_decision_rejects_raw_sensitive_reason_code() -> None:
    sink = InMemoryCapabilityAuditSink()
    trail = CapabilityAuditTrail(recorder=sink)

    raw_marker = "CPA4_SYNTHETIC_SENSITIVE_PAYLOAD_DO_NOT_LOG__reason_code_probe__opaque_marker"
    try:
        trail.record_decision(
            request_id="req-sensitive-decision",
            capability_name="ki.memory.read",
            capability_version="1.0.0",
            decision_result="deny",
            reason_code=raw_marker,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("record_decision must reject unsafe raw reason_code payload")

    assert sink.events == []
