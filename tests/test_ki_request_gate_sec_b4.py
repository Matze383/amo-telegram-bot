from __future__ import annotations

from dataclasses import dataclass

from amo_bot.ai import KIPluginPolicyContext, KIPluginPolicyGate, KIPluginRequestDecision


@dataclass
class FakeRunner:
    calls: list[tuple[str, str, dict]]
    result: dict

    def run_plugin_capability(self, *, plugin_id: str, capability_id: str, params: dict) -> dict:
        self.calls.append((plugin_id, capability_id, params))
        return self.result


def _gate() -> KIPluginPolicyGate:
    return KIPluginPolicyGate(
        enabled_plugins={"ki_core"},
        allowed_capabilities={"ki_core": {"respond", "analyze_image", "suggest_memory"}},
        allowed_scopes={"ki_core": {"chat:-100/topic:7"}},
        max_result_chars=120,
        max_result_items=4,
    )


def test_sec_b4_only_minimal_capabilities_are_allowed() -> None:
    gate = _gate()
    runner = FakeRunner(calls=[], result={"status": "ok"})

    allowed = gate.handle_request(
        payload={
            "plugin_id": "ki_core",
            "capability_id": "respond",
            "params": {"text": "reply in topic scope"},
            "reason": "user asked a question",
        },
        context=KIPluginPolicyContext(scope_id="chat:-100/topic:7", consent_given=True),
        runner=runner,
    )

    denied = gate.handle_request(
        payload={
            "plugin_id": "ki_core",
            "capability_id": "delete_message",
            "params": {"message_id": 1},
            "reason": "attempt high-risk action",
        },
        context=KIPluginPolicyContext(scope_id="chat:-100/topic:7", consent_given=True),
        runner=runner,
    )

    assert allowed.forwarded is True
    assert allowed.audit.decision is KIPluginRequestDecision.ALLOW
    assert denied.forwarded is False
    assert denied.audit.reason_code == "capability_not_allowed"


def test_sec_b4_default_deny_blocks_userplugin_and_scope_escalation() -> None:
    gate = _gate()
    runner = FakeRunner(calls=[], result={"status": "ok"})

    user_plugin_attempt = gate.handle_request(
        payload={
            "plugin_id": "user_plugin",
            "capability_id": "respond",
            "params": {},
            "reason": "forward to plugin",
        },
        context=KIPluginPolicyContext(scope_id="chat:-100/topic:7", consent_given=True),
        runner=runner,
    )
    wrong_scope_attempt = gate.handle_request(
        payload={
            "plugin_id": "ki_core",
            "capability_id": "respond",
            "params": {},
            "reason": "cross scope",
        },
        context=KIPluginPolicyContext(scope_id="chat:-100/topic:8", consent_given=True),
        runner=runner,
    )

    assert user_plugin_attempt.forwarded is False
    assert user_plugin_attempt.audit.reason_code == "plugin_not_enabled"
    assert wrong_scope_attempt.forwarded is False
    assert wrong_scope_attempt.audit.reason_code == "scope_not_permitted"
    assert runner.calls == []


def test_sec_b4_analyze_image_requires_explicit_consent_and_redacts_metadata() -> None:
    gate = _gate()
    runner = FakeRunner(
        calls=[],
        result={
            "decision": "accepted",
            "image_ref": "media://abc123",
            "raw_image_bytes": "A" * 80,
            "private_path": "/home/user/private/photo.jpg",
        },
    )

    no_consent = gate.handle_request(
        payload={
            "plugin_id": "ki_core",
            "capability_id": "analyze_image",
            "params": {"image_ref": "media://abc123", "consent_token": "missing"},
            "reason": "analyze attachment",
        },
        context=KIPluginPolicyContext(scope_id="chat:-100/topic:7", consent_given=False),
        runner=runner,
    )

    with_consent = gate.handle_request(
        payload={
            "plugin_id": "ki_core",
            "capability_id": "analyze_image",
            "params": {"image_ref": "media://abc123", "consent_token": "ok"},
            "reason": "analyze attachment",
        },
        context=KIPluginPolicyContext(scope_id="chat:-100/topic:7", consent_given=True),
        runner=runner,
    )

    assert no_consent.forwarded is False
    assert no_consent.audit.reason_code == "consent_required"

    assert with_consent.forwarded is True
    assert with_consent.result is not None
    assert "raw_image_bytes" not in with_consent.result
    assert with_consent.result["private_path"] == "[redacted]"


def test_sec_b4_suggest_memory_is_proposal_only_not_auto_write() -> None:
    gate = _gate()
    runner = FakeRunner(calls=[], result={"proposal_id": "p1", "preview": "remember this detail"})

    out = gate.handle_request(
        payload={
            "plugin_id": "ki_core",
            "capability_id": "suggest_memory",
            "params": {"action": "write_now", "text": "private note"},
            "reason": "save memory",
        },
        context=KIPluginPolicyContext(scope_id="chat:-100/topic:7", consent_given=True),
        runner=runner,
    )

    assert out.forwarded is True
    assert runner.calls == [
        (
            "ki_core",
            "suggest_memory",
            {"action": "write_now", "text": "private note"},
        )
    ]
    assert out.result is not None
    assert out.result["proposal_id"] == "p1"


def test_sec_b4_denied_attempts_use_non_revealing_reason_codes() -> None:
    gate = _gate()

    out = gate.reject_direct_invocation(tool_name="admin_tunnel")

    assert out.forwarded is False
    assert out.audit.reason_code == "direct_invocation_forbidden"
    assert ":" not in out.audit.reason_code
