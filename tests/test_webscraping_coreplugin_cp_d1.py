from amo_bot.ai import CapabilityDecisionResult
from amo_bot.ai.webscraping_coreplugin import WebscrapingInput, execute_webscraping_noop, validate_webscraping_input


def test_cp_d1_rejects_non_http_schemes() -> None:
    result = validate_webscraping_input(WebscrapingInput(url="file:///tmp/example.txt"))
    assert result.ok is False
    assert result.reason_code == "scheme_not_allowed"


def test_cp_d1_rejects_localhost_and_private_ip_by_default() -> None:
    localhost = validate_webscraping_input(WebscrapingInput(url="http://localhost:8080/health"))
    private_ip = validate_webscraping_input(WebscrapingInput(url="http://10.0.0.7/status"))

    assert localhost.ok is False
    assert localhost.reason_code == "host_not_allowed"
    assert private_ip.ok is False
    assert private_ip.reason_code == "host_not_allowed"


def test_cp_d1_allows_safe_https_host() -> None:
    result = validate_webscraping_input(WebscrapingInput(url="https://example.org/docs"))
    assert result.ok is True
    assert result.reason_code == "ok"


def test_cp_d1_runtime_default_deny_even_if_input_is_valid() -> None:
    result = execute_webscraping_noop(
        request=WebscrapingInput(url="https://example.org/docs"),
        policy_allow_webscraping=False,
    )
    assert result.result == CapabilityDecisionResult.DENY
    assert result.reason_code == "not_enabled"


def test_cp_d1_runtime_can_allow_local_only_with_explicit_policy_toggle() -> None:
    denied = execute_webscraping_noop(
        request=WebscrapingInput(url="http://127.0.0.1/admin"),
        policy_allow_webscraping=True,
        policy_allow_local_hosts=False,
    )
    allowed_for_validation = execute_webscraping_noop(
        request=WebscrapingInput(url="http://127.0.0.1/admin"),
        policy_allow_webscraping=True,
        policy_allow_local_hosts=True,
    )

    assert denied.reason_code == "host_not_allowed"
    assert denied.audit_payload["host"] == "127.0.0.1"

    assert allowed_for_validation.result == CapabilityDecisionResult.DENY
    assert allowed_for_validation.reason_code == "not_implemented"


def test_cp_d1_audit_payload_does_not_leak_full_url() -> None:
    result = execute_webscraping_noop(
        request=WebscrapingInput(url="https://example.org/segment-alpha?view=compact"),
        policy_allow_webscraping=False,
    )
    payload = str(result.audit_payload)
    assert "segment-alpha" not in payload
    assert "view=compact" not in payload
    assert result.audit_payload["host"] == "example.org"
