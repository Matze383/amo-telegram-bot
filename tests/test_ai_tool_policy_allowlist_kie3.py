import asyncio

from amo_bot.ai.tool_registry import (
    AIRole,
    AIScopeKind,
    AIToolPolicy,
    AIToolScopeContext,
    invoke_tool_noop,
    validate_tool_invocation_request,
)


def _request(tool_name: str = "weather_lookup"):
    request, error = validate_tool_invocation_request({"tool_name": tool_name, "arguments": {"city": "Berlin"}})
    assert error is None
    assert request is not None
    return request


def test_policy_default_is_disabled_and_denies_with_safe_reason_code() -> None:
    policy = AIToolPolicy()

    decision = policy.evaluate(
        tool_name="weather_lookup",
        role=AIRole.OWNER,
        scope=AIToolScopeContext(scope_kind=AIScopeKind.TOPIC, chat_id=-100, topic_id=42),
    )

    assert decision.allowed is False
    assert decision.reason_code == "tools_disabled"


def test_policy_allows_tool_from_global_allowlist_when_enabled() -> None:
    policy = AIToolPolicy(enabled=True, global_allowlist={"weather_lookup"}, min_role=AIRole.ADMIN)

    owner_decision = policy.evaluate(
        tool_name="weather_lookup",
        role=AIRole.OWNER,
        scope=AIToolScopeContext(scope_kind=AIScopeKind.TOPIC, chat_id=-100, topic_id=42),
    )
    admin_decision = policy.evaluate(
        tool_name="weather_lookup",
        role=AIRole.ADMIN,
        scope=AIToolScopeContext(scope_kind=AIScopeKind.PRIVATE, chat_id=123, topic_id=None),
    )

    assert owner_decision.allowed is True
    assert owner_decision.reason_code == "allowed_global"
    assert admin_decision.allowed is True
    assert admin_decision.reason_code == "allowed_global"


def test_policy_scope_allowlist_matrix_topic_and_private() -> None:
    policy = AIToolPolicy(
        enabled=True,
        topic_allowlist={(-100, 42): {"weather_lookup"}},
        private_allowlist={123: {"weather_lookup"}},
    )

    topic_allowed = policy.evaluate(
        tool_name="weather_lookup",
        role=AIRole.OWNER,
        scope=AIToolScopeContext(scope_kind=AIScopeKind.TOPIC, chat_id=-100, topic_id=42),
    )
    topic_denied = policy.evaluate(
        tool_name="weather_lookup",
        role=AIRole.OWNER,
        scope=AIToolScopeContext(scope_kind=AIScopeKind.TOPIC, chat_id=-100, topic_id=99),
    )
    private_allowed = policy.evaluate(
        tool_name="weather_lookup",
        role=AIRole.OWNER,
        scope=AIToolScopeContext(scope_kind=AIScopeKind.PRIVATE, chat_id=123, topic_id=None),
    )
    private_denied = policy.evaluate(
        tool_name="weather_lookup",
        role=AIRole.OWNER,
        scope=AIToolScopeContext(scope_kind=AIScopeKind.PRIVATE, chat_id=999, topic_id=None),
    )

    assert topic_allowed.allowed is True
    assert topic_allowed.reason_code == "allowed_scope"
    assert topic_denied.allowed is False
    assert topic_denied.reason_code == "not_in_scope_allowlist"
    assert private_allowed.allowed is True
    assert private_allowed.reason_code == "allowed_scope"
    assert private_denied.allowed is False
    assert private_denied.reason_code == "not_in_scope_allowlist"


def test_policy_role_gate_respected_before_allowlist_grant() -> None:
    policy = AIToolPolicy(
        enabled=True,
        global_allowlist={"weather_lookup"},
        min_role=AIRole.ADMIN,
    )

    denied = policy.evaluate(
        tool_name="weather_lookup",
        role=AIRole.VIP,
        scope=AIToolScopeContext(scope_kind=AIScopeKind.TOPIC, chat_id=-100, topic_id=42),
    )
    allowed = policy.evaluate(
        tool_name="weather_lookup",
        role=AIRole.ADMIN,
        scope=AIToolScopeContext(scope_kind=AIScopeKind.TOPIC, chat_id=-100, topic_id=42),
    )

    assert denied.allowed is False
    assert denied.reason_code == "role_denied"
    assert allowed.allowed is True
    assert allowed.reason_code == "allowed_global"


def test_invoke_tool_noop_uses_policy_evaluator_reason_codes() -> None:
    request = _request()

    policy_disabled = AIToolPolicy()
    denied_disabled = asyncio.run(
        invoke_tool_noop(
            request=request,
            policy=policy_disabled,
            role=AIRole.OWNER,
            scope=AIToolScopeContext(scope_kind=AIScopeKind.TOPIC, chat_id=-100, topic_id=42),
        )
    )

    policy_scope = AIToolPolicy(
        enabled=True,
        topic_allowlist={(-100, 42): {"weather_lookup"}},
    )
    denied_scope = asyncio.run(
        invoke_tool_noop(
            request=request,
            policy=policy_scope,
            role=AIRole.OWNER,
            scope=AIToolScopeContext(scope_kind=AIScopeKind.TOPIC, chat_id=-100, topic_id=99),
        )
    )
    allowed_scope = asyncio.run(
        invoke_tool_noop(
            request=request,
            policy=policy_scope,
            role=AIRole.OWNER,
            scope=AIToolScopeContext(scope_kind=AIScopeKind.TOPIC, chat_id=-100, topic_id=42),
        )
    )

    assert denied_disabled.error_code == "policy_denied"
    assert denied_disabled.reason == "tools_disabled"

    assert denied_scope.error_code == "policy_denied"
    assert denied_scope.reason == "not_in_scope_allowlist"

    assert allowed_scope.status.value == "success"
    assert allowed_scope.reason is None
