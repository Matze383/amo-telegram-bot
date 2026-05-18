from __future__ import annotations

from amo_bot.ai.ki_request_gate import KIPluginPolicyContext, KIPluginPolicyGate


class DummyRunner:
    def run_plugin_capability(self, *, plugin_id: str, capability_id: str, params: dict[str, object]) -> dict[str, object]:
        return {
            "secret_ref": "secrets.crm.api_token",
            "api_token": "SUPER_SECRET_VALUE",
            "nested": {"password": "hidden", "ok": "value"},
            "path": "/tmp/secret.txt",
            "raw_debug": "SHOULD_NOT_LEAK",
        }


def test_ki_request_gate_sanitizes_secret_values_and_paths() -> None:
    gate = KIPluginPolicyGate(
        enabled_plugins={"crm"},
        allowed_capabilities={"crm": {"lookup"}},
        allowed_scopes={"crm": {"chat:1"}},
    )

    outcome = gate.handle_request(
        payload={
            "plugin_id": "crm",
            "capability_id": "lookup",
            "params": {"customer_id": "42"},
            "reason": "answer user",
        },
        context=KIPluginPolicyContext(scope_id="chat:1", consent_given=True),
        runner=DummyRunner(),
    )

    assert outcome.forwarded is True
    assert outcome.result is not None
    assert outcome.result["secret_ref"] == "[redacted]"
    assert outcome.result["api_token"] == "[redacted]"
    assert outcome.result["nested"]["password"] == "[redacted]"
    assert outcome.result["path"] == "[redacted]"
    assert "raw_debug" not in outcome.result


def test_ki_request_gate_redacts_neutral_key_secret_like_value() -> None:
    gate = KIPluginPolicyGate(
        enabled_plugins={"crm"},
        allowed_capabilities={"crm": {"lookup"}},
        allowed_scopes={"crm": {"chat:1"}},
    )

    class Runner:
        def run_plugin_capability(self, *, plugin_id: str, capability_id: str, params: dict[str, object]) -> dict[str, object]:
            return {"note": "A23456789012345678901234", "ok": "short"}

    outcome = gate.handle_request(
        payload={"plugin_id": "crm", "capability_id": "lookup", "params": {}, "reason": "answer"},
        context=KIPluginPolicyContext(scope_id="chat:1", consent_given=True),
        runner=Runner(),
    )

    assert outcome.result is not None
    assert outcome.result["note"] == "[redacted]"
    assert outcome.result["ok"] == "short"


def test_ki_reject_direct_invocation_has_no_sensitive_payload() -> None:
    gate = KIPluginPolicyGate()
    out = gate.reject_direct_invocation(tool_name="shell")

    assert out.forwarded is False
    assert out.result is None
    assert out.audit.reason_code == "direct_invocation_forbidden"
    assert "secret" not in out.response_text.lower()
