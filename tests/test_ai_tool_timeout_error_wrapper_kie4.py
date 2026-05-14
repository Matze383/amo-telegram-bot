import asyncio

from amo_bot.ai.tool_registry import (
    AIRole,
    AIScopeKind,
    AIToolPolicy,
    AIToolScopeContext,
    invoke_tool_noop,
    validate_tool_invocation_request,
)


def _request() -> object:
    request, error = validate_tool_invocation_request(
        {"tool_name": "weather_lookup", "arguments": {"city": "Berlin"}, "call_id": "k4"}
    )
    assert error is None
    assert request is not None
    return request


def _allow_policy() -> AIToolPolicy:
    return AIToolPolicy(enabled=True, global_allowlist={"weather_lookup"})


def _scope() -> AIToolScopeContext:
    return AIToolScopeContext(scope_kind=AIScopeKind.TOPIC, chat_id=-100, topic_id=42)


def test_kie4_timeout_returns_safe_error_envelope() -> None:
    async def slow_executor(_request):
        await asyncio.sleep(0.05)
        return {"mode": "noop", "executed": False}

    response = asyncio.run(
        invoke_tool_noop(
            request=_request(),
            policy=_allow_policy(),
            role=AIRole.OWNER,
            scope=_scope(),
            timeout_seconds=0.001,
            executor=slow_executor,
        )
    )

    assert response.status.value == "error"
    assert response.tool_name == "weather_lookup"
    assert response.call_id == "k4"
    assert response.error_code == "execution_timeout"
    assert response.reason == "timeout"
    assert response.result is None


def test_kie4_executor_error_returns_safe_error_envelope_without_leak() -> None:
    async def crashing_executor(_request):
        raise RuntimeError("internal stack path /tmp/secret token=abc")

    response = asyncio.run(
        invoke_tool_noop(
            request=_request(),
            policy=_allow_policy(),
            role=AIRole.OWNER,
            scope=_scope(),
            executor=crashing_executor,
        )
    )

    assert response.status.value == "error"
    assert response.tool_name == "weather_lookup"
    assert response.call_id == "k4"
    assert response.error_code == "execution_error"
    assert response.reason == "execution_failed"
    assert response.result is None
    assert "/" not in response.reason
    assert " " not in response.reason
