from __future__ import annotations

import pytest

from amo_bot.plugins.sandbox.runner import PluginSandboxRunner


def test_network_policy_outside_allowlist_denied() -> None:
    runner = PluginSandboxRunner()

    allowed = runner.enforce_network_policy(
        url="https://example.org/api",
        policy={"allowed_domains": ["example.com"]},
        counters={},
    )

    assert allowed["allowed"] is False
    assert allowed["reason_code"] == "network_domain_not_allowlisted"


def test_network_policy_rate_limit_and_response_cap() -> None:
    runner = PluginSandboxRunner()
    counters = {}

    first = runner.enforce_network_policy(
        url="https://example.com/a",
        policy={"allowed_domains": ["example.com"], "max_calls": 1, "max_response_bytes": 128},
        counters=counters,
        response_size_bytes=64,
    )
    assert first["allowed"] is True

    capped = runner.enforce_network_policy(
        url="https://example.com/b",
        policy={"allowed_domains": ["example.com"], "max_calls": 5, "max_response_bytes": 32},
        counters=counters,
        response_size_bytes=64,
    )
    assert capped["allowed"] is False
    assert capped["reason_code"] == "network_response_cap_exceeded"

    second = runner.enforce_network_policy(
        url="https://example.com/c",
        policy={"allowed_domains": ["example.com"], "max_calls": 1},
        counters=counters,
    )
    assert second["allowed"] is False
    assert second["reason_code"] == "network_rate_limited"


def test_network_policy_timeout_bounds_enforced() -> None:
    runner = PluginSandboxRunner(base_timeout_ms=3000)

    denied = runner.enforce_network_policy(
        url="https://example.com",
        policy={"allowed_domains": ["example.com"], "timeout_ms": 5000},
        counters={},
    )

    assert denied["allowed"] is False
    assert denied["reason_code"] == "network_timeout_exceeded"


def test_git_policy_read_only_enforced() -> None:
    runner = PluginSandboxRunner()

    assert runner.enforce_git_policy(command="git status", policy={"enabled": True, "read_only": True})["allowed"] is True
    assert runner.enforce_git_policy(command="git log -n 1", policy={"enabled": True, "read_only": True})["allowed"] is True

    denied_push = runner.enforce_git_policy(command="git push origin main", policy={"enabled": True, "read_only": True})
    assert denied_push["allowed"] is False
    assert denied_push["reason_code"] == "git_write_operation_denied"

    denied_merge = runner.enforce_git_policy(command="git merge feature", policy={"enabled": True, "read_only": True})
    assert denied_merge["allowed"] is False
    assert denied_merge["reason_code"] == "git_write_operation_denied"

    denied_with_config = runner.enforce_git_policy(command="git -c http.sslVerify=false status", policy={"enabled": True, "read_only": True})
    assert denied_with_config["allowed"] is False
    assert denied_with_config["reason_code"] == "git_read_only_command_not_allowlisted"


def test_shell_policy_default_deny_and_allowlist() -> None:
    runner = PluginSandboxRunner(base_timeout_ms=3000)

    default_denied = runner.enforce_shell_policy(command="echo hi", policy={"enabled": False})
    assert default_denied["allowed"] is False
    assert default_denied["reason_code"] == "shell_disabled"

    deny_non_allowlisted = runner.enforce_shell_policy(
        command="cat /etc/passwd",
        policy={"enabled": True, "allowlist": ["echo", "ls"], "timeout_ms": 500},
    )
    assert deny_non_allowlisted["allowed"] is False
    assert deny_non_allowlisted["reason_code"] == "shell_command_not_allowlisted"

    deny_meta = runner.enforce_shell_policy(
        command="echo hello && whoami",
        policy={"enabled": True, "allowlist": ["echo", "ls", "whoami"], "timeout_ms": 500},
    )
    assert deny_meta["allowed"] is False
    assert deny_meta["reason_code"] == "shell_meta_syntax_denied"

    allow = runner.enforce_shell_policy(
        command="echo hello",
        policy={"enabled": True, "allowlist": ["echo", "ls"], "timeout_ms": 500},
    )
    assert allow["allowed"] is True


def test_shell_policy_timeout_bound_enforced() -> None:
    runner = PluginSandboxRunner(base_timeout_ms=1000)
    denied = runner.enforce_shell_policy(
        command="echo hi",
        policy={"enabled": True, "allowlist": ["echo"], "timeout_ms": 2000},
    )
    assert denied["allowed"] is False
    assert denied["reason_code"] == "shell_timeout_exceeded"


def test_secrets_reference_only_no_value_exposure() -> None:
    runner = PluginSandboxRunner()

    safe = runner.enforce_secrets_reference_only(
        {
            "secret_ref": "secrets.crm.api_token",
            "token": "raw-token-value",
            "session": "A23456789012345678901234",
            "nested": {"password": "abc123"},
        }
    )

    assert safe["secret_ref"] == "secrets.crm.api_token"
    assert safe["token"] == "[redacted]"
    assert safe["session"] == "[redacted]"
    assert safe["nested"]["password"] == "[redacted]"


def test_run_plugin_capability_blocks_high_risk_direct_for_ki_context() -> None:
    runner = PluginSandboxRunner()

    with pytest.raises(PermissionError, match="high_risk_direct_request_denied"):
        runner.run_plugin_capability(
            plugin_id="sample",
            capability_id="network",
            params={"url": "https://example.com", "_origin": "ki"},
        )
