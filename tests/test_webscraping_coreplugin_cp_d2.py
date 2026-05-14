from amo_bot.ai import CapabilityDecisionResult
from amo_bot.ai.webscraping_coreplugin import (
    WebscrapingHTTPResponse,
    WebscrapingInput,
    WebscrapingPolicyConfig,
    execute_webscraping_static_html,
)


def test_cp_d2_extracts_static_html_text_with_output_cap() -> None:
    html = b"<html><body><h1>Hello</h1><script>ignore()</script><p>World</p></body></html>"

    def fake_http_get(url: str, timeout_seconds: float) -> WebscrapingHTTPResponse:
        return WebscrapingHTTPResponse(status_code=200, headers={"content-type": "text/html; charset=utf-8"}, body=html)

    result = execute_webscraping_static_html(
        request=WebscrapingInput(url="https://example.org/docs"),
        policy=WebscrapingPolicyConfig(
            enabled=True,
            allowlist_hosts=frozenset({"example.org"}),
            max_output_chars=8,
            enforce_robots=False,
        ),
        http_get=fake_http_get,
    )

    assert result.result == CapabilityDecisionResult.ALLOW
    assert result.reason_code == "ok"
    assert result.extracted_text == "Hello Wo"
    assert result.audit_payload["path"] == "/..."


def test_cp_d2_rejects_binary_mime_type() -> None:
    def fake_http_get(url: str, timeout_seconds: float) -> WebscrapingHTTPResponse:
        return WebscrapingHTTPResponse(
            status_code=200,
            headers={"content-type": "application/octet-stream"},
            body=b"\x00\x01\x02",
        )

    result = execute_webscraping_static_html(
        request=WebscrapingInput(url="https://example.org/file.bin"),
        policy=WebscrapingPolicyConfig(
            enabled=True,
            allowlist_hosts=frozenset({"example.org"}),
            enforce_robots=False,
        ),
        http_get=fake_http_get,
    )

    assert result.result == CapabilityDecisionResult.DENY
    assert result.reason_code == "mime_not_allowed"


def test_cp_d2_rejects_oversized_response() -> None:
    body = b"<html>" + (b"A" * 150) + b"</html>"

    def fake_http_get(url: str, timeout_seconds: float) -> WebscrapingHTTPResponse:
        return WebscrapingHTTPResponse(status_code=200, headers={"content-type": "text/html"}, body=body)

    result = execute_webscraping_static_html(
        request=WebscrapingInput(url="https://example.org/big"),
        policy=WebscrapingPolicyConfig(
            enabled=True,
            allowlist_hosts=frozenset({"example.org"}),
            max_response_bytes=64,
            enforce_robots=False,
        ),
        http_get=fake_http_get,
    )

    assert result.result == CapabilityDecisionResult.DENY
    assert result.reason_code == "response_too_large"


def test_cp_d2_rejects_fetch_timeout() -> None:
    def fake_http_get(url: str, timeout_seconds: float) -> WebscrapingHTTPResponse:
        raise TimeoutError("timed out")

    result = execute_webscraping_static_html(
        request=WebscrapingInput(url="https://example.org/slow"),
        policy=WebscrapingPolicyConfig(
            enabled=True,
            allowlist_hosts=frozenset({"example.org"}),
            enforce_robots=False,
        ),
        http_get=fake_http_get,
    )

    assert result.result == CapabilityDecisionResult.DENY
    assert result.reason_code == "fetch_timeout"


def test_cp_d2_rejects_by_robots_policy() -> None:
    def fake_http_get(url: str, timeout_seconds: float) -> WebscrapingHTTPResponse:
        raise AssertionError("must not fetch when robots policy denies")

    result = execute_webscraping_static_html(
        request=WebscrapingInput(url="https://example.org/private/page"),
        policy=WebscrapingPolicyConfig(
            enabled=True,
            allowlist_hosts=frozenset({"example.org"}),
            enforce_robots=True,
            robots_disallow_prefixes=("/private",),
        ),
        http_get=fake_http_get,
    )

    assert result.result == CapabilityDecisionResult.DENY
    assert result.reason_code == "robots_disallowed"


def test_cp_d2_rejects_root_url_by_default_robots_disallow() -> None:
    def fake_http_get(url: str, timeout_seconds: float) -> WebscrapingHTTPResponse:
        raise AssertionError("must not fetch when default robots policy denies root")

    result = execute_webscraping_static_html(
        request=WebscrapingInput(url="https://example.org"),
        policy=WebscrapingPolicyConfig(
            enabled=True,
            allowlist_hosts=frozenset({"example.org"}),
            enforce_robots=True,
        ),
        http_get=fake_http_get,
    )

    assert result.result == CapabilityDecisionResult.DENY
    assert result.reason_code == "robots_disallowed"


def test_cp_d2_default_deny_when_host_not_allowlisted() -> None:
    def fake_http_get(url: str, timeout_seconds: float) -> WebscrapingHTTPResponse:
        raise AssertionError("must not fetch when host is not allowlisted")

    result = execute_webscraping_static_html(
        request=WebscrapingInput(url="https://example.org/docs"),
        policy=WebscrapingPolicyConfig(enabled=True, allowlist_hosts=frozenset({"other.org"}), enforce_robots=False),
        http_get=fake_http_get,
    )

    assert result.result == CapabilityDecisionResult.DENY
    assert result.reason_code == "url_not_allowlisted"
