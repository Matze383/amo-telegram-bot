from __future__ import annotations

from amo_bot.ai import CapabilityDecisionResult, RSSFetchRequest, RSSHTTPResponse, execute_rss_fetch


RSS_XML = b"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<rss version=\"2.0\">
  <channel>
    <title>Example Feed</title>
    <item>
      <guid>id-1</guid>
      <title>Title 1</title>
      <link>https://example.org/1</link>
      <pubDate>Thu, 14 May 2026 12:00:00 GMT</pubDate>
      <description>Summary 1</description>
    </item>
    <item>
      <guid>id-1</guid>
      <title>Title 1 duplicate</title>
      <link>https://example.org/1</link>
      <pubDate>Thu, 14 May 2026 12:00:00 GMT</pubDate>
      <description>Summary duplicate</description>
    </item>
  </channel>
</rss>
"""


def _request(url: str = "https://example.org/rss.xml") -> RSSFetchRequest:
    return RSSFetchRequest(
        feed_id="feed1",
        url=url,
        allowed_urls=frozenset({"https://example.org/rss.xml"}),
        min_interval_seconds=60,
        timeout_seconds=2.0,
        max_response_bytes=1024 * 1024,
        max_entries=20,
    )


def test_fetch_denied_when_url_not_allowlisted() -> None:
    req = _request(url="https://not-allowed.example/rss.xml")

    def fake_http_get(url: str, timeout_seconds: float) -> RSSHTTPResponse:
        raise AssertionError("must not call network when not allowlisted")

    result = execute_rss_fetch(
        request=req,
        http_get=fake_http_get,
        now_monotonic_seconds=1000.0,
        last_fetch_monotonic_seconds=None,
    )
    assert result.result == CapabilityDecisionResult.DENY
    assert result.reason_code == "url_not_allowlisted"
    assert result.entries == ()


def test_fetch_rate_limited_by_min_interval() -> None:
    req = _request()

    def fake_http_get(url: str, timeout_seconds: float) -> RSSHTTPResponse:
        raise AssertionError("must not call network while rate-limited")

    result = execute_rss_fetch(
        request=req,
        http_get=fake_http_get,
        now_monotonic_seconds=1050.0,
        last_fetch_monotonic_seconds=1000.0,
    )
    assert result.result == CapabilityDecisionResult.DENY
    assert result.reason_code == "rate_limited"


def test_fetch_timeout_maps_to_safe_deny() -> None:
    req = _request()

    def fake_http_get(url: str, timeout_seconds: float) -> RSSHTTPResponse:
        raise TimeoutError("timed out")

    result = execute_rss_fetch(
        request=req,
        http_get=fake_http_get,
        now_monotonic_seconds=2000.0,
        last_fetch_monotonic_seconds=None,
    )
    assert result.result == CapabilityDecisionResult.DENY
    assert result.reason_code == "fetch_timeout"


def test_fetch_rejects_response_too_large() -> None:
    req = _request()

    def fake_http_get(url: str, timeout_seconds: float) -> RSSHTTPResponse:
        return RSSHTTPResponse(status_code=200, body=b"x" * (req.max_response_bytes + 1))

    result = execute_rss_fetch(
        request=req,
        http_get=fake_http_get,
        now_monotonic_seconds=2000.0,
        last_fetch_monotonic_seconds=None,
    )
    assert result.result == CapabilityDecisionResult.DENY
    assert result.reason_code == "response_too_large"


def test_fetch_rejects_malformed_xml() -> None:
    req = _request()

    def fake_http_get(url: str, timeout_seconds: float) -> RSSHTTPResponse:
        return RSSHTTPResponse(status_code=200, body=b"<rss><channel><item>")

    result = execute_rss_fetch(
        request=req,
        http_get=fake_http_get,
        now_monotonic_seconds=2000.0,
        last_fetch_monotonic_seconds=None,
    )
    assert result.result == CapabilityDecisionResult.DENY
    assert result.reason_code == "malformed_xml"


def test_fetch_parses_and_dedupes_entries() -> None:
    req = _request()

    def fake_http_get(url: str, timeout_seconds: float) -> RSSHTTPResponse:
        return RSSHTTPResponse(status_code=200, body=RSS_XML)

    result = execute_rss_fetch(
        request=req,
        http_get=fake_http_get,
        now_monotonic_seconds=2000.0,
        last_fetch_monotonic_seconds=None,
    )
    assert result.result == CapabilityDecisionResult.ALLOW
    assert result.reason_code == "ok"
    assert len(result.entries) == 1
    entry = result.entries[0]
    assert entry.id == "id-1"
    assert entry.title == "Title 1"
    assert entry.link == "https://example.org/1"


ATOM_XML = b"""<?xml version=\"1.0\" encoding=\"utf-8\"?>
<feed xmlns=\"http://www.w3.org/2005/Atom\">
  <title>Atom Example</title>
  <entry>
    <id>tag:example.org,2026:1</id>
    <title>Atom Entry 1</title>
    <updated>2026-05-14T12:00:00Z</updated>
    <link rel=\"alternate\" href=\"https://example.org/atom/1\" />
    <summary>Atom Summary 1</summary>
  </entry>
  <entry>
    <id>tag:example.org,2026:1</id>
    <title>Atom Entry 1 duplicate</title>
    <updated>2026-05-14T12:00:00Z</updated>
    <link rel=\"alternate\" href=\"https://example.org/atom/1\" />
    <summary>Atom Summary duplicate</summary>
  </entry>
</feed>
"""


def test_fetch_parses_atom_entries_and_dedupes() -> None:
    req = _request()

    def fake_http_get(url: str, timeout_seconds: float) -> RSSHTTPResponse:
        return RSSHTTPResponse(status_code=200, body=ATOM_XML)

    result = execute_rss_fetch(
        request=req,
        http_get=fake_http_get,
        now_monotonic_seconds=2000.0,
        last_fetch_monotonic_seconds=None,
    )
    assert result.result == CapabilityDecisionResult.ALLOW
    assert result.reason_code == "ok"
    assert len(result.entries) == 1
    entry = result.entries[0]
    assert entry.id == "tag:example.org,2026:1"
    assert entry.title == "Atom Entry 1"
    assert entry.link == "https://example.org/atom/1"
