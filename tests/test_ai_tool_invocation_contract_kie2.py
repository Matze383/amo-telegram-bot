from amo_bot.ai import (
    AIToolCapability,
    AIToolDescriptor,
    AIToolInvocationStatus,
    AIToolPolicy,
    AIToolRegistry,
    build_tool_invocation_error,
    build_tool_invocation_rejection,
    invoke_tool_noop,
    validate_tool_invocation_request,
)


def test_validate_tool_invocation_request_accepts_minimal_valid_payload() -> None:
    request, error = validate_tool_invocation_request(
        {"tool_name": " Weather_Lookup ", "arguments": {"city": "Berlin"}, "call_id": " c1 "}
    )

    assert error is None
    assert request is not None
    assert request.tool_name == "weather_lookup"
    assert request.arguments == {"city": "Berlin"}
    assert request.call_id == "c1"


def test_validate_tool_invocation_request_rejects_invalid_payload_shapes() -> None:
    _, error1 = validate_tool_invocation_request({"tool_name": "", "arguments": {}})
    _, error2 = validate_tool_invocation_request({"tool_name": "x", "arguments": []})
    _, error3 = validate_tool_invocation_request({"tool_name": "x", "arguments": {}, "call_id": "   "})

    assert error1 == "invalid_tool_name"
    assert error2 == "invalid_arguments"
    assert error3 == "invalid_call_id"


def test_rejection_and_error_envelopes_are_safe_and_structured() -> None:
    denied = build_tool_invocation_rejection(reason="invalid_request")
    errored = build_tool_invocation_error(tool_name="  TOOL ", error_code=" INTERNAL_ERROR ", reason=" failed ")

    assert denied.status == AIToolInvocationStatus.DENIED
    assert denied.tool_name == "unknown"
    assert denied.error_code == "request_rejected"
    assert denied.reason == "invalid_request"
    assert denied.result is None

    assert errored.status == AIToolInvocationStatus.ERROR
    assert errored.tool_name == "tool"
    assert errored.error_code == "internal_error"
    assert errored.reason == "failed"
    assert errored.result is None


def test_rejection_and_error_envelopes_do_not_echo_unsafe_tokens() -> None:
    denied = build_tool_invocation_rejection(
        reason="  /path/to/local/workspace SECRET_KEY=abc123  "
    )
    errored = build_tool_invocation_error(
        tool_name="tool",
        error_code="  upstream failed: token=abc xyz /tmp/secret.txt  ",
        reason="  DB password leaked at /var/lib/app/config.yml  ",
    )

    assert denied.reason == "request_rejected"
    assert errored.error_code == "internal_error"
    assert errored.reason == "execution_failed"
    assert "/" not in denied.reason
    assert " " not in denied.reason
    assert "/" not in errored.error_code
    assert " " not in errored.error_code
    assert "/" not in errored.reason
    assert " " not in errored.reason


def test_invoke_tool_noop_denies_by_default_policy_without_execution() -> None:
    registry = AIToolRegistry()
    registry.register(
        AIToolDescriptor(name="weather_lookup", capability=AIToolCapability.QUERY, description="Lookup weather")
    )
    policy = AIToolPolicy()

    request, error = validate_tool_invocation_request(
        {"tool_name": "weather_lookup", "arguments": {"city": "Berlin"}}
    )
    assert error is None
    assert request is not None

    response = invoke_tool_noop(request=request, policy=policy)

    assert response.status == AIToolInvocationStatus.DENIED
    assert response.tool_name == "weather_lookup"
    assert response.error_code == "policy_denied"
    assert response.reason == "tool_not_allowed"
    assert response.result is None
