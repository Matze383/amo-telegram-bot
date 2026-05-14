from amo_bot.ai import CapabilityDecisionResult, execute_rss_noop, validate_rss_input


def test_rss_input_validation_accepts_safe_shape() -> None:
    result = validate_rss_input(feed_id="news_feed-1", url="https://example.org/rss.xml")
    assert result.ok is True
    assert result.reason_code == "ok"


def test_rss_input_validation_rejects_invalid_feed_id() -> None:
    result = validate_rss_input(feed_id="../../etc/passwd", url="https://example.org/rss.xml")
    assert result.ok is False
    assert result.reason_code == "invalid_feed_id"


def test_rss_input_validation_rejects_invalid_url_scheme() -> None:
    result = validate_rss_input(feed_id="feed1", url="file:///tmp/feed.xml")
    assert result.ok is False
    assert result.reason_code == "invalid_url"


def test_rss_noop_execution_denies_when_disabled() -> None:
    result = execute_rss_noop(feed_id="feed1", url="https://example.org/rss.xml")
    assert result.result == CapabilityDecisionResult.DENY
    assert result.reason_code == "not_enabled"


def test_rss_noop_execution_denies_invalid_input() -> None:
    result = execute_rss_noop(feed_id="", url="https://example.org/rss.xml")
    assert result.result == CapabilityDecisionResult.DENY
    assert result.reason_code == "invalid_feed_id"
