from collections.abc import Callable

from amo_bot.ai.webtool_provider_adapter import (
    _CorepluginSearchProviderAdapter,
    _normalize_ddg_locale,
    _normalize_market_price_query,
    _resolve_searxng_config,
    _SearxngConfig,
    _search_searxng_json,
    _validate_search_endpoint_base_url,
)


class _DummyResponse:
    def __init__(self, text: str, status_code: int = 200, json_payload: dict | None = None) -> None:
        self.text = text
        self.status_code = status_code
        self._json_payload = json_payload or {}

    def json(self):
        return self._json_payload


class _DummyClient:
    def __init__(
        self,
        responses: dict[str, _DummyResponse],
        headers: dict[str, str] | None = None,
        on_get: Callable[[str, dict[str, str], dict[str, str]], None] | None = None,
        on_post: Callable[[str, dict[str, str]], None] | None = None,
    ) -> None:
        self._responses = responses
        self.headers = headers or {}
        self._on_get = on_get
        self._on_post = on_post

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, url: str, params: dict[str, str], headers: dict[str, str] | None = None):
        assert url.startswith(("http://", "https://"))
        assert "q" in params
        if self._on_get is not None:
            self._on_get(url, params, headers or {})
        return self._responses[url]

    def post(self, url: str, data: dict[str, str]):
        assert url.startswith("https://")
        assert "q" in data
        if self._on_post is not None:
            self._on_post(url, data)
        return self._responses[url]


DDG_HTML = '''
<html><body>
<a class="result__a" href="https://example.com/a">Title A</a>
<div class="result__snippet">Snippet A</div>
<a class="result__a" href="https://example.com/b">Title B</a>
<a class="result__snippet" href="#">Snippet B</a>
<a class="result__a" href="https://example.com/c">Title C</a>
</body></html>
'''


def test_normalize_ddg_locale_maps_en_and_de_to_safe_values():
    assert _normalize_ddg_locale("en") == "en-us"
    assert _normalize_ddg_locale("de") == "de-de"


def test_normalize_market_price_query_rewrites_ambiguous_current_usd():
    assert _normalize_market_price_query(query="current bitcoin price usd", locale="en") == "bitcoin price USD BTC"


def test_normalize_market_price_query_rewrites_aktueller_kurs_german_locale():
    assert _normalize_market_price_query(query="aktueller bitcoin kurs", locale="de") == "bitcoin kurs"


def test_normalize_market_price_query_keeps_generic_query_unchanged():
    assert _normalize_market_price_query(query="best hiking boots", locale="en") == "best hiking boots"


def test_normalize_market_price_query_removes_ambiguous_current_for_non_electrical_subjects():
    assert _normalize_market_price_query(query="current python version", locale="en") == "python version"


def test_normalize_market_price_query_keeps_electrical_current_queries_intact():
    assert _normalize_market_price_query(query="current in dc circuit", locale="en") == "current in dc circuit"


def test_normalize_market_price_query_latest_news_stays_topic_specific():
    assert _normalize_market_price_query(query="latest openai news", locale="en") == "openai news"


def test_normalize_market_price_query_latest_updates_stays_topic_specific():
    assert _normalize_market_price_query(query="latest openai updates", locale="en") == "openai updates"


def test_normalize_market_price_query_topic_latest_news_stays_topic_specific():
    assert _normalize_market_price_query(query="openai latest news", locale="en") == "openai news"


def test_real_adapter_sends_normalized_market_query_to_provider(monkeypatch):
    import amo_bot.ai.webtool_provider_adapter as m

    sent_query: dict[str, str] = {}

    def _fake_execute(**kwargs):
        sent_query["query"] = kwargs["request"].query

        class _Result:
            class _Decision:
                value = "allow"

            result = _Decision()
            results = ()

        return _Result()

    monkeypatch.setattr(m, "execute_websearch_provider_mvp", _fake_execute)

    adapter = m.RealWebsearchProviderAdapter(quota_limiter=object())
    adapter.search(query="current bitcoin price usd", locale="en", max_results=3)

    assert sent_query["query"] == "bitcoin price USD BTC"


def test_real_adapter_sends_german_current_market_query_to_provider(monkeypatch):
    import amo_bot.ai.webtool_provider_adapter as m

    sent_query: dict[str, str] = {}

    def _fake_execute(**kwargs):
        sent_query["query"] = kwargs["request"].query

        class _Result:
            class _Decision:
                value = "allow"

            result = _Decision()
            results = ()

        return _Result()

    monkeypatch.setattr(m, "execute_websearch_provider_mvp", _fake_execute)

    adapter = m.RealWebsearchProviderAdapter(quota_limiter=object())
    adapter.search(query="aktueller bitcoin kurs", locale="de", max_results=3)

    assert sent_query["query"] == "bitcoin kurs"


def test_real_adapter_sends_normalized_current_fact_query_to_provider(monkeypatch):
    import amo_bot.ai.webtool_provider_adapter as m

    sent_query: dict[str, str] = {}

    def _fake_execute(**kwargs):
        sent_query["query"] = kwargs["request"].query

        class _Result:
            class _Decision:
                value = "allow"

            result = _Decision()
            results = ()

        return _Result()

    monkeypatch.setattr(m, "execute_websearch_provider_mvp", _fake_execute)

    adapter = m.RealWebsearchProviderAdapter(quota_limiter=object())
    adapter.search(query="current python version", locale="en", max_results=3)

    assert sent_query["query"] == "python version"


def test_validate_search_endpoint_base_url_public_requires_https():
    assert _validate_search_endpoint_base_url("https://searx.example.org") == "https://searx.example.org"


def test_validate_search_endpoint_base_url_private_allows_http():
    assert _validate_search_endpoint_base_url("http://127.0.0.1:8888") == "http://127.0.0.1:8888"


def test_validate_search_endpoint_base_url_rejects_http_public():
    import pytest

    with pytest.raises(ValueError):
        _validate_search_endpoint_base_url("http://searx.example.org")


def test_resolve_searxng_config_prefers_primary_env(monkeypatch):
    monkeypatch.setenv("SEARXNG_BASE_URL", "https://searx.example.org")
    monkeypatch.setenv("AMO_WEBSEARCH_SEARXNG_BASE_URL", "https://ignored.example.org")
    cfg = _resolve_searxng_config(locale="en", max_results=3)
    assert cfg is not None
    assert cfg.base_url == "https://searx.example.org"


def test_resolve_searxng_config_accepts_amo_fallback_env(monkeypatch):
    monkeypatch.delenv("SEARXNG_BASE_URL", raising=False)
    monkeypatch.delenv("AMO_SEARXNG_URL", raising=False)
    monkeypatch.setenv("AMO_WEBSEARCH_SEARXNG_BASE_URL", "https://searx.internal")
    cfg = _resolve_searxng_config(locale="de", max_results=3)
    assert cfg is not None
    assert cfg.base_url == "https://searx.internal"
    assert cfg.language == "de-de"


def test_resolve_searxng_config_accepts_current_info_env(monkeypatch):
    monkeypatch.delenv("SEARXNG_BASE_URL", raising=False)
    monkeypatch.delenv("AMO_WEBSEARCH_SEARXNG_BASE_URL", raising=False)
    monkeypatch.setenv("AMO_SEARXNG_URL", "https://current-info-searx.example")
    monkeypatch.setenv("AMO_SEARXNG_TIMEOUT_SECONDS", "9")
    monkeypatch.setenv("AMO_SEARCH_MAX_RESULTS", "4")

    cfg = _resolve_searxng_config(locale="en", max_results=3)

    assert cfg is not None
    assert cfg.base_url == "https://current-info-searx.example"
    assert cfg.timeout_seconds == 9
    assert cfg.max_results == 4


def test_resolve_searxng_config_none_when_unset(monkeypatch):
    monkeypatch.delenv("SEARXNG_BASE_URL", raising=False)
    monkeypatch.delenv("AMO_WEBSEARCH_SEARXNG_BASE_URL", raising=False)
    monkeypatch.delenv("AMO_SEARXNG_URL", raising=False)
    assert _resolve_searxng_config(locale="en", max_results=3) is None




def test_coreplugin_adapter_configured_searxng_empty_never_calls_fallbacks(monkeypatch):
    import amo_bot.ai.webtool_provider_adapter as m

    monkeypatch.setenv("SEARXNG_BASE_URL", "https://searx.example.org")
    monkeypatch.delenv("AMO_WEBSEARCH_SEARXNG_BASE_URL", raising=False)
    monkeypatch.delenv("AMO_SEARXNG_URL", raising=False)

    def _fake_searxng_json(*, query: str, config):
        return ()

    def _raise_if_called(*_args, **_kwargs):
        raise AssertionError("fallback/public endpoint should not be called when SearXNG is configured")

    monkeypatch.setattr(m, "_search_searxng_json", _fake_searxng_json)
    monkeypatch.setattr(m, "_parse_ddg_lite_results", _raise_if_called)
    monkeypatch.setattr(m, "_parse_ddg_html_results", _raise_if_called)
    monkeypatch.setattr(m, "_parse_bing_html_results", _raise_if_called)
    monkeypatch.setattr(m, "_parse_mojeek_html_results", _raise_if_called)

    def _raising_client(**_kwargs):
        raise AssertionError("httpx.Client should not be called when SearXNG is configured")

    monkeypatch.setattr(m.httpx, "Client", _raising_client)

    adapter = _CorepluginSearchProviderAdapter()
    assert adapter.search(query="python", locale="en", safesearch="moderate", max_results=3) == ()
def test_coreplugin_adapter_returns_empty_without_searxng_and_never_calls_httpx(monkeypatch):
    import amo_bot.ai.webtool_provider_adapter as m

    monkeypatch.delenv("SEARXNG_BASE_URL", raising=False)
    monkeypatch.delenv("AMO_WEBSEARCH_SEARXNG_BASE_URL", raising=False)
    monkeypatch.delenv("AMO_SEARXNG_URL", raising=False)

    def _raising_client(**_kwargs):
        raise AssertionError("httpx.Client should not be called")

    monkeypatch.setattr(m.httpx, "Client", _raising_client)

    adapter = _CorepluginSearchProviderAdapter()
    assert adapter.search(query="python", locale="en", safesearch="moderate", max_results=3) == ()


def test_search_searxng_json_parses_results(monkeypatch):
    import amo_bot.ai.webtool_provider_adapter as m

    monkeypatch.setattr(
        m.httpx,
        "Client",
        lambda **kwargs: _DummyClient(
            {
                "https://searx.example.org/search": _DummyResponse(
                    "",
                    status_code=200,
                    json_payload={
                        "results": [
                            {"title": "Bitcoin Price Live", "url": "https://example.com/btc", "content": "BTC USD now"},
                            {"title": "Python Releases", "url": "https://www.python.org/downloads/", "snippet": "Latest Python"},
                        ]
                    },
                )
            }
        ),
    )

    cfg = _SearxngConfig(base_url="https://searx.example.org", timeout_seconds=2.0, max_results=5, language="en-us")

    results = _search_searxng_json(query="bitcoin", config=cfg)
    assert len(results) == 2
    assert results[0].title == "Bitcoin Price Live"
    assert results[0].url == "https://example.com/btc"


def test_coreplugin_adapter_prefers_searxng_when_configured(monkeypatch):
    import amo_bot.ai.webtool_provider_adapter as m

    monkeypatch.setenv("SEARXNG_BASE_URL", "https://searx.example.org")

    monkeypatch.setattr(
        m.httpx,
        "Client",
        lambda **kwargs: _DummyClient(
            {
                "https://searx.example.org/search": _DummyResponse(
                    "",
                    status_code=200,
                    json_payload={"results": [{"title": "OpenAI News", "url": "https://openai.com/news", "content": "latest"}]},
                ),
                "https://lite.duckduckgo.com/lite/": _DummyResponse("", status_code=403),
                "https://html.duckduckgo.com/html/": _DummyResponse("", status_code=403),
                "https://www.bing.com/search": _DummyResponse("", status_code=403),
                "https://www.mojeek.com/search": _DummyResponse("", status_code=403),
            }
        ),
    )

    adapter = _CorepluginSearchProviderAdapter()
    results = adapter.search(query="latest openai news", locale="en", safesearch="moderate", max_results=3)
    assert len(results) == 1
    assert results[0].url == "https://openai.com/news"
