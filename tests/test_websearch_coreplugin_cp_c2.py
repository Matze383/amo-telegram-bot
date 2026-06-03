from amo_bot.ai import (
    CapabilityQuotaRule,
    CoreCapabilityQuotaLimiter,
    FakeWebsearchProvider,
    InMemoryCapabilityAuditSink,
    WebsearchInput,
    WebsearchProviderConfig,
    execute_websearch_provider_mvp,
)
from amo_bot.ai.websearch_coreplugin import WebsearchProviderResult
from amo_bot.ai.capability_audit import CapabilityAuditTrail


def _quota(limit: int = 10) -> CoreCapabilityQuotaLimiter:
    return CoreCapabilityQuotaLimiter(rules={"ki.websearch.query": CapabilityQuotaRule(limit=limit)})


class FailingWebsearchProvider:
    """Deterministic provider used only in tests."""

    def __init__(self, *, mode: str = "error") -> None:
        self.mode = mode

    def search(self, *, query: str, locale: str, safesearch: str, max_results: int) -> tuple[WebsearchProviderResult, ...]:
        _ = (query, locale, safesearch, max_results)
        if self.mode == "timeout":
            raise TimeoutError("provider timeout")
        raise RuntimeError("provider failure")


def test_cp_c2_provider_config_allowlist_runtime_deny() -> None:
    result = execute_websearch_provider_mvp(
        request=WebsearchInput(query="python", locale="en", safesearch="moderate"),
        provider=FakeWebsearchProvider(),
        provider_config=WebsearchProviderConfig(provider_name="fake", provider_allowlist=frozenset({"other"})),
        quota_limiter=_quota(),
    )
    assert result.reason_code == "provider_not_allowed"
    assert result.results == ()


def test_cp_c2_provider_timeout_and_failure_safe_with_retries_and_redacted_audit() -> None:
    sink = InMemoryCapabilityAuditSink()
    trail = CapabilityAuditTrail(recorder=sink)

    timeout_result = execute_websearch_provider_mvp(
        request=WebsearchInput(query="python", locale="en", safesearch="moderate"),
        provider=FailingWebsearchProvider(mode="timeout"),
        provider_config=WebsearchProviderConfig(
            provider_name="fake", provider_allowlist=frozenset({"fake"}), timeout_seconds=0.5, retry_count=1
        ),
        quota_limiter=_quota(),
        audit_trail=trail,
    )
    assert timeout_result.reason_code == "provider_timeout"
    assert timeout_result.results == ()

    error_result = execute_websearch_provider_mvp(
        request=WebsearchInput(query="python", locale="en", safesearch="moderate"),
        provider=FailingWebsearchProvider(mode="error"),
        provider_config=WebsearchProviderConfig(
            provider_name="fake", provider_allowlist=frozenset({"fake"}), timeout_seconds=0.5, retry_count=2
        ),
        quota_limiter=_quota(),
        audit_trail=trail,
    )
    assert error_result.reason_code == "provider_error"

    failed_events = [event for event in sink.events if event.status == "failed"]
    assert failed_events
    for event in failed_events:
        assert event.reason_code in {"provider_timeout", "provider_error"}
        assert event.request_id.startswith("websearch_provider_")


def test_cp_c2_quota_enforced_before_provider_execution() -> None:
    limiter = _quota(limit=1)

    first = execute_websearch_provider_mvp(
        request=WebsearchInput(query="python", locale="en", safesearch="moderate"),
        provider=FakeWebsearchProvider(),
        provider_config=WebsearchProviderConfig(provider_name="fake", provider_allowlist=frozenset({"fake"})),
        quota_limiter=limiter,
    )
    second = execute_websearch_provider_mvp(
        request=WebsearchInput(query="python", locale="en", safesearch="moderate"),
        provider=FakeWebsearchProvider(),
        provider_config=WebsearchProviderConfig(provider_name="fake", provider_allowlist=frozenset({"fake"})),
        quota_limiter=limiter,
    )

    assert first.reason_code == "ok"
    assert second.reason_code == "quota_exceeded"


def test_cp_c2_result_normalization_caps_fields_and_count() -> None:
    result = execute_websearch_provider_mvp(
        request=WebsearchInput(query="python", locale="en", safesearch="moderate"),
        provider=FakeWebsearchProvider(),
        provider_config=WebsearchProviderConfig(provider_name="fake", provider_allowlist=frozenset({"fake"})),
        quota_limiter=_quota(),
        max_results=99,
    )

    assert len(result.results) == 5
    for item in result.results:
        assert len(item.title) <= 200
        assert len(item.url) <= 2048
        assert len(item.snippet) <= 400
