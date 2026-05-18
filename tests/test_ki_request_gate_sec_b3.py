from __future__ import annotations

from dataclasses import dataclass

from amo_bot.ai import (
    KIPluginPolicyContext,
    KIPluginPolicyGate,
    KIPluginRequestDecision,
)


@dataclass
class FakeRunner:
    calls: list[tuple[str, str, dict]]
    result: dict

    def run_plugin_capability(self, *, plugin_id: str, capability_id: str, params: dict) -> dict:
        self.calls.append((plugin_id, capability_id, params))
        return self.result


def _gate() -> KIPluginPolicyGate:
    return KIPluginPolicyGate(
        enabled_plugins={"weather"},
        allowed_capabilities={"weather": {"forecast"}},
        allowed_scopes={"weather": {"chat:1/topic:2"}},
        max_result_chars=40,
        max_result_items=3,
    )


def test_sec_b3_request_allow_forward_and_sanitize() -> None:
    gate = _gate()
    runner = FakeRunner(
        calls=[],
        result={
            "summary": "Sunny and warm",
            "raw_prompt": "secret internal prompt",
            "token": "abcdef0123456789abcdef0123456789",
            "items": [1, 2, 3, 4, 5],
            "private": {"x": 1},
        },
    )

    out = gate.handle_request(
        payload={
            "plugin_id": "weather",
            "capability_id": "forecast",
            "params": {"city": "Berlin"},
            "reason": "user asked for weather",
        },
        context=KIPluginPolicyContext(scope_id="chat:1/topic:2", consent_given=True),
        runner=runner,
    )

    assert out.forwarded is True
    assert out.audit.decision is KIPluginRequestDecision.ALLOW
    assert out.audit.reason_code == "policy_allow"
    assert runner.calls == [("weather", "forecast", {"city": "Berlin"})]
    assert out.result is not None
    assert "raw_prompt" not in out.result
    assert "private" not in out.result
    assert out.result["token"] == "[redacted]"
    assert out.result["items"][-1] == "[truncated]"


def test_sec_b3_policy_deny_matrix_returns_safe_fallback() -> None:
    gate = _gate()
    runner = FakeRunner(calls=[], result={"ok": True})

    deny_plugin = gate.handle_request(
        payload={"plugin_id": "other", "capability_id": "forecast", "params": {}, "reason": "x"},
        context=KIPluginPolicyContext(scope_id="chat:1/topic:2", consent_given=True),
        runner=runner,
    )
    deny_cap = gate.handle_request(
        payload={"plugin_id": "weather", "capability_id": "admin", "params": {}, "reason": "x"},
        context=KIPluginPolicyContext(scope_id="chat:1/topic:2", consent_given=True),
        runner=runner,
    )
    deny_scope = gate.handle_request(
        payload={"plugin_id": "weather", "capability_id": "forecast", "params": {}, "reason": "x"},
        context=KIPluginPolicyContext(scope_id="chat:9/topic:9", consent_given=True),
        runner=runner,
    )
    deny_consent = gate.handle_request(
        payload={"plugin_id": "weather", "capability_id": "forecast", "params": {}, "reason": "x"},
        context=KIPluginPolicyContext(scope_id="chat:1/topic:2", consent_given=False),
        runner=runner,
    )

    assert runner.calls == []
    assert deny_plugin.forwarded is False and deny_plugin.audit.reason_code == "plugin_not_enabled"
    assert deny_cap.forwarded is False and deny_cap.audit.reason_code == "capability_not_allowed"
    assert deny_scope.forwarded is False and deny_scope.audit.reason_code == "scope_not_permitted"
    assert deny_consent.forwarded is False and deny_consent.audit.reason_code == "consent_required"
    assert deny_plugin.response_text == "I can't execute that request in this context."


def test_sec_b3_direct_invocation_attempt_must_fail() -> None:
    gate = _gate()

    out = gate.reject_direct_invocation(tool_name="run_plugin_direct")

    assert out.forwarded is False
    assert out.audit.reason_code == "direct_invocation_forbidden"
    assert out.audit.capability_id == "direct_invoke"


def test_sec_b3_invalid_request_shape_denied_and_audited() -> None:
    gate = _gate()
    runner = FakeRunner(calls=[], result={"ok": True})

    out = gate.handle_request(
        payload={"plugin_id": "weather", "capability_id": "forecast", "params": []},
        context=KIPluginPolicyContext(scope_id="chat:1/topic:2", consent_given=True),
        runner=runner,
    )

    assert out.forwarded is False
    assert out.audit.reason_code == "invalid_request"
    assert out.audit.plugin_id == "unknown"
    assert runner.calls == []
