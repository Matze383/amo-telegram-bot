from __future__ import annotations

import importlib
import inspect
import pkgutil

import pytest

from amo_bot.ai.ki_request_gate import KIPluginPolicyContext, KIPluginPolicyGate
from amo_bot.plugins.sandbox.runner import PluginSandboxRunner


class _BypassRunner:
    def run_plugin_capability(self, *, plugin_id: str, capability_id: str, params: dict[str, object]) -> dict[str, object]:
        return {
            "status": "ok",
            "internal_debug": "x",
        }


def test_sec_b6_import_isolation_between_ki_core_and_plugin_sandbox() -> None:
    ki_mod = importlib.import_module("amo_bot.ai.ki_request_gate")
    sandbox_mod = importlib.import_module("amo_bot.plugins.sandbox.runner")

    ki_src = inspect.getsource(ki_mod)
    sandbox_src = inspect.getsource(sandbox_mod)

    assert "amo_bot.plugins.sandbox.worker" not in ki_src
    assert "amo_bot.plugins.sandbox.types" not in ki_src
    assert "amo_bot.ai.ki_request_gate" not in sandbox_src


def test_sec_b6_ki_cannot_instantiate_plugin_classes_directly() -> None:
    gate = KIPluginPolicyGate(
        enabled_plugins={"crm"},
        allowed_capabilities={"crm": {"lookup"}},
        allowed_scopes={"crm": {"chat:1"}},
    )

    out = gate.reject_direct_invocation(tool_name="PluginSandboxRunner")

    assert out.forwarded is False
    assert out.result is None
    assert out.audit.reason_code == "direct_invocation_forbidden"
    assert out.response_text == "I can't execute that request in this context."


def test_sec_b6_policy_gate_bypass_attempt_is_denied_and_audited() -> None:
    gate = KIPluginPolicyGate(
        enabled_plugins={"crm"},
        allowed_capabilities={"crm": {"lookup"}},
        allowed_scopes={"crm": {"chat:1"}},
    )

    denied = gate.handle_request(
        payload={
            "plugin_id": "crm",
            "capability_id": "network",  # try bypass into high risk capability
            "params": {"_origin": "ki", "url": "https://example.com"},
            "reason": "bypass policy gate",
        },
        context=KIPluginPolicyContext(scope_id="chat:1", consent_given=True),
        runner=_BypassRunner(),
    )

    assert denied.forwarded is False
    assert denied.result is None
    assert denied.audit.reason_code == "capability_not_allowed"
    assert denied.audit.decision.value == "deny"
    assert denied.response_text == "I can't execute that request in this context."


def test_sec_b6_boundary_alert_on_high_risk_direct_ki_origin() -> None:
    runner = PluginSandboxRunner()

    with pytest.raises(PermissionError, match="high_risk_direct_request_denied"):
        runner.run_plugin_capability(
            plugin_id="sample",
            capability_id="shell",
            params={"_origin": "ki", "cmd": "whoami"},
        )


def test_sec_b6_plugin_sandbox_package_does_not_import_ki_core() -> None:
    imported = []
    pkg = importlib.import_module("amo_bot.plugins.sandbox")

    for mod in pkgutil.iter_modules(pkg.__path__, prefix="amo_bot.plugins.sandbox."):
        module = importlib.import_module(mod.name)
        imported.append(module.__name__)
        src = inspect.getsource(module)
        assert "amo_bot.ai.ki_request_gate" not in src

    assert imported
