from __future__ import annotations

from dataclasses import replace

from amo_bot.ai import CapabilityDecisionResult, RSSFetchRequest, RSSHTTPResponse, execute_rss_fetch


def _request(url: str) -> RSSFetchRequest:
    return RSSFetchRequest(
        feed_id="feed1",
        url=url,
        allowed_urls=frozenset({url}),
        min_interval_seconds=60,
        timeout_seconds=2.0,
        max_response_bytes=1024 * 1024,
        max_entries=20,
        plugin_id="plugin.test",
    )


def test_fetch_blocks_localhost_name() -> None:
    req = _request("http://localhost/rss.xml")

    def fake_http_get(url: str, timeout_seconds: float) -> RSSHTTPResponse:
        raise AssertionError("must not call network when SSRF target is blocked")

    result = execute_rss_fetch(
        request=req,
        http_get=fake_http_get,
        now_monotonic_seconds=1000.0,
        last_fetch_monotonic_seconds=None,
    )

    assert result.result == CapabilityDecisionResult.DENY
    assert result.reason_code == "ssrf_blocked"
    assert result.audit["blocked_reason"] == "localhost"


def test_fetch_blocks_loopback_ip_literal() -> None:
    req = _request("http://127.0.0.1/rss.xml")

    def fake_http_get(url: str, timeout_seconds: float) -> RSSHTTPResponse:
        raise AssertionError("must not call network when SSRF target is blocked")

    result = execute_rss_fetch(
        request=req,
        http_get=fake_http_get,
        now_monotonic_seconds=1000.0,
        last_fetch_monotonic_seconds=None,
    )

    assert result.result == CapabilityDecisionResult.DENY
    assert result.reason_code == "ssrf_blocked"
    assert result.audit["blocked_reason"] == "ip_literal_blocked"


def test_fetch_blocks_link_local_resolution() -> None:
    req = _request("https://feeds.example/rss.xml")

    def fake_http_get(url: str, timeout_seconds: float) -> RSSHTTPResponse:
        raise AssertionError("must not call network when SSRF target is blocked")

    result = execute_rss_fetch(
        request=req,
        http_get=fake_http_get,
        now_monotonic_seconds=1000.0,
        last_fetch_monotonic_seconds=None,
        resolve_host_ips=lambda host: ("169.254.10.20",),
    )

    assert result.result == CapabilityDecisionResult.DENY
    assert result.reason_code == "ssrf_blocked"
    assert result.audit["blocked_reason"] == "resolved_ip_blocked"


def test_fetch_blocks_private_cidr_resolution() -> None:
    req = _request("https://feeds.example/rss.xml")

    def fake_http_get(url: str, timeout_seconds: float) -> RSSHTTPResponse:
        raise AssertionError("must not call network when SSRF target is blocked")

    result = execute_rss_fetch(
        request=req,
        http_get=fake_http_get,
        now_monotonic_seconds=1000.0,
        last_fetch_monotonic_seconds=None,
        resolve_host_ips=lambda host: ("10.1.2.3",),
    )

    assert result.result == CapabilityDecisionResult.DENY
    assert result.reason_code == "ssrf_blocked"
    assert result.audit["blocked_reason"] == "resolved_ip_blocked"


def test_fetch_blocks_oversized_payload_with_audit_bytes() -> None:
    req = _request("https://feeds.example/rss.xml")
    req = replace(req, max_response_bytes=1024)

    def fake_http_get(url: str, timeout_seconds: float) -> RSSHTTPResponse:
        return RSSHTTPResponse(status_code=200, body=b"x" * 2048)

    result = execute_rss_fetch(
        request=req,
        http_get=fake_http_get,
        now_monotonic_seconds=1000.0,
        last_fetch_monotonic_seconds=None,
        resolve_host_ips=lambda host: ("93.184.216.34",),
    )

    assert result.result == CapabilityDecisionResult.DENY
    assert result.reason_code == "response_too_large"
    assert result.audit["status"] == "deny"
    assert result.audit["bytes"] == 2048
    assert result.audit["blocked_reason"] is None


def test_fetch_denies_when_redirect_limit_exceeded() -> None:
    req = _request("https://feeds.example/rss.xml")
    req = replace(req, max_redirects=1)

    def fake_http_get(url: str, timeout_seconds: float) -> RSSHTTPResponse:
        return RSSHTTPResponse(status_code=200, body=b"<rss><channel/></rss>", redirects=2)

    result = execute_rss_fetch(
        request=req,
        http_get=fake_http_get,
        now_monotonic_seconds=1000.0,
        last_fetch_monotonic_seconds=None,
        resolve_host_ips=lambda host: ("93.184.216.34",),
    )

    assert result.result == CapabilityDecisionResult.DENY
    assert result.reason_code == "redirect_limit_exceeded"
    assert result.audit["blocked_reason"] == "max_redirects"


def test_fetch_audit_schema_snapshot_allow_and_deny() -> None:
    deny_req = _request("https://feeds.example/rss.xml")

    def deny_http_get(url: str, timeout_seconds: float) -> RSSHTTPResponse:
        return RSSHTTPResponse(status_code=500, body=b"boom")

    deny_result = execute_rss_fetch(
        request=deny_req,
        http_get=deny_http_get,
        now_monotonic_seconds=1000.0,
        last_fetch_monotonic_seconds=None,
        resolve_host_ips=lambda host: ("93.184.216.34",),
    )

    assert deny_result.result == CapabilityDecisionResult.DENY
    assert deny_result.reason_code == "http_error"
    assert set(deny_result.audit.keys()) == {
        "plugin_id",
        "url_host",
        "status",
        "reason",
        "duration_ms",
        "bytes",
        "items_count",
        "blocked_reason",
    }
    assert deny_result.audit["plugin_id"] == "plugin.test"
    assert deny_result.audit["url_host"] == "feeds.example"
    assert deny_result.audit["status"] == "deny"
    assert deny_result.audit["reason"] == "http_error"
    assert isinstance(deny_result.audit["duration_ms"], int)
    assert deny_result.audit["bytes"] == 0
    assert deny_result.audit["items_count"] == 0
    assert deny_result.audit["blocked_reason"] is None

    allow_req = _request("https://feeds.example/rss.xml")
    allow_xml = b"""<?xml version='1.0' encoding='UTF-8'?>
    <rss version='2.0'><channel><item><guid>x1</guid><title>T</title><link>https://example.org/x1</link></item></channel></rss>
    """

    def allow_http_get(url: str, timeout_seconds: float) -> RSSHTTPResponse:
        return RSSHTTPResponse(status_code=200, body=allow_xml)

    allow_result = execute_rss_fetch(
        request=allow_req,
        http_get=allow_http_get,
        now_monotonic_seconds=2000.0,
        last_fetch_monotonic_seconds=None,
        resolve_host_ips=lambda host: ("93.184.216.34",),
    )

    assert allow_result.result == CapabilityDecisionResult.ALLOW
    assert allow_result.reason_code == "ok"
    assert set(allow_result.audit.keys()) == {
        "plugin_id",
        "url_host",
        "status",
        "reason",
        "duration_ms",
        "bytes",
        "items_count",
        "blocked_reason",
    }
    assert allow_result.audit["plugin_id"] == "plugin.test"
    assert allow_result.audit["url_host"] == "feeds.example"
    assert allow_result.audit["status"] == "allow"
    assert allow_result.audit["reason"] == "ok"
    assert isinstance(allow_result.audit["duration_ms"], int)
    assert allow_result.audit["bytes"] == len(allow_xml)
    assert allow_result.audit["items_count"] == 1
    assert allow_result.audit["blocked_reason"] is None
