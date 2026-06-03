from amo_bot.ai import (
    CapabilityActorType,
    CapabilityScopeType,
    CoreCapabilityPolicyRequest,
    FakeWebsearchProvider,
    InMemoryCapabilityAuditSink,
    WebsearchInput,
    execute_websearch_fake_allowed,
    execute_websearch_noop,
    evaluate_core_capability_policy,
    validate_websearch_input,
)
from amo_bot.ai.capability_audit import CapabilityAuditTrail


def test_websearch_query_validation_rejects_empty_and_long() -> None:
    assert validate_websearch_input(WebsearchInput(query="", locale="en", safesearch="moderate")).ok is False
    assert (
        validate_websearch_input(WebsearchInput(query="x" * 257, locale="en", safesearch="moderate")).reason_code
        == "invalid_query"
    )


def test_websearch_query_validation_rejects_invalid_locale_and_safesearch() -> None:
    assert (
        validate_websearch_input(WebsearchInput(query="test", locale="en@us", safesearch="moderate")).reason_code
        == "invalid_locale"
    )
    assert (
        validate_websearch_input(WebsearchInput(query="test", locale="en", safesearch="aggressive")).reason_code
        == "invalid_safesearch"
    )


def test_websearch_policy_default_deny_for_ai_and_userplugin() -> None:
    deny_ai = evaluate_core_capability_policy(
        CoreCapabilityPolicyRequest(
            actor_type=CapabilityActorType.AI,
            capability_name="ki.websearch.query",
            scope_type=CapabilityScopeType.TOPIC,
        )
    )
    assert deny_ai.allowed is False

    deny_userplugin = evaluate_core_capability_policy(
        CoreCapabilityPolicyRequest(
            actor_type=CapabilityActorType.USERPLUGIN,
            capability_name="ki.websearch.query",
            scope_type=CapabilityScopeType.TOPIC,
        )
    )
    assert deny_userplugin.allowed is False


def test_websearch_noop_denies_with_not_enabled() -> None:
    result = execute_websearch_noop(
        request=WebsearchInput(query="python testing", locale="en", safesearch="moderate"),
        provider=FakeWebsearchProvider(),
    )
    assert result.reason_code == "not_enabled"
    assert result.results == ()


def test_websearch_fake_provider_result_cap_is_bounded() -> None:
    result = execute_websearch_fake_allowed(
        request=WebsearchInput(query="python", locale="en", safesearch="moderate"),
        provider=FakeWebsearchProvider(),
        max_results=99,
    )
    assert result.reason_code == "ok"
    assert len(result.results) == 5


def test_websearch_fake_provider_audits_bounded_redacted_metadata() -> None:
    sink = InMemoryCapabilityAuditSink()
    trail = CapabilityAuditTrail(recorder=sink)

    result = execute_websearch_fake_allowed(
        request=WebsearchInput(query="python secret token", locale="EN", safesearch="moderate"),
        provider=FakeWebsearchProvider(),
        max_results=99,
        audit_trail=trail,
    )

    assert result.reason_code == "ok"
    assert len(result.results) == 5
    assert sink.events

    event = sink.events[-1]
    assert event.status == "completed"
    assert event.capability_name == "ki.websearch.query"
    assert event.summary == "execution_completed"
    assert event.reason_code is None
    assert event.details == ()
    assert event.request_id.startswith("websearch_fake_")
    assert "secret" not in event.request_id
