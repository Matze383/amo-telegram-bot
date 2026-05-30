from collections.abc import Callable

from amo_bot.ai.webtool_provider_adapter import _CorepluginSearchProviderAdapter, _normalize_ddg_locale


class _DummyResponse:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code


class _DummyClient:
    def __init__(
        self,
        responses: dict[str, _DummyResponse],
        headers: dict[str, str] | None = None,
        on_get: Callable[[str, dict[str, str]], None] | None = None,
    ) -> None:
        self._responses = responses
        self.headers = headers or {}
        self._on_get = on_get

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, url: str, params: dict[str, str]):
        assert url.startswith("https://")
        assert "q" in params
        if self._on_get is not None:
            self._on_get(url, params)
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


def test_ddg_adapter_parses_and_bounds_results(monkeypatch):
    import amo_bot.ai.webtool_provider_adapter as m

    monkeypatch.setattr(
        m.httpx,
        "Client",
        lambda **kwargs: _DummyClient(
            {
                "https://lite.duckduckgo.com/lite/": _DummyResponse("", status_code=403),
                "https://html.duckduckgo.com/html/": _DummyResponse(DDG_HTML),
            }
        ),
    )

    adapter = _CorepluginSearchProviderAdapter()
    results = adapter.search(query="bitcoin price now", locale="en", safesearch="moderate", max_results=2)

    assert len(results) == 2
    assert results[0].url == "https://example.com/a"
    assert results[0].title == "Title A"
    assert "Snippet" in results[0].snippet


def test_ddg_adapter_handles_4xx_as_empty(monkeypatch):
    import amo_bot.ai.webtool_provider_adapter as m

    monkeypatch.setattr(
        m.httpx,
        "Client",
        lambda **kwargs: _DummyClient(
            {
                "https://lite.duckduckgo.com/lite/": _DummyResponse("", status_code=403),
                "https://html.duckduckgo.com/html/": _DummyResponse("", status_code=403),
            }
        ),
    )

    adapter = _CorepluginSearchProviderAdapter()
    assert adapter.search(query="x", locale="en", safesearch="moderate", max_results=3) == ()


def test_ddg_adapter_uses_browserlike_user_agent(monkeypatch):
    import amo_bot.ai.webtool_provider_adapter as m

    captured: dict[str, str] = {}

    def _client_factory(**kwargs):
        captured.update(kwargs.get("headers") or {})
        return _DummyClient(
            {
                "https://lite.duckduckgo.com/lite/": _DummyResponse("", status_code=403),
                "https://html.duckduckgo.com/html/": _DummyResponse("", status_code=403),
            },
            headers=kwargs.get("headers"),
        )

    monkeypatch.setattr(m.httpx, "Client", _client_factory)

    adapter = _CorepluginSearchProviderAdapter()
    adapter.search(query="x", locale="en", safesearch="moderate", max_results=3)

    ua = captured.get("User-Agent", "")
    assert "Mozilla/5.0" in ua
    assert "amo-bot-websearch" not in ua


def test_normalize_ddg_locale_maps_en_and_de_to_safe_values():
    assert _normalize_ddg_locale("en") == "en-us"
    assert _normalize_ddg_locale("de") == "de-de"


def test_ddg_adapter_never_sends_unsafe_en_en_locale(monkeypatch):
    import amo_bot.ai.webtool_provider_adapter as m

    observed_kl: list[str] = []

    def _on_get(_url: str, params: dict[str, str]) -> None:
        observed_kl.append(params.get("kl", ""))

    monkeypatch.setattr(
        m.httpx,
        "Client",
        lambda **kwargs: _DummyClient(
            {
                "https://lite.duckduckgo.com/lite/": _DummyResponse(
                    '<html><body><a class="result-link" href="https://example.org/news">Example News</a></body></html>',
                    status_code=200,
                ),
                "https://html.duckduckgo.com/html/": _DummyResponse("", status_code=200),
            },
            on_get=_on_get,
        ),
    )

    adapter = _CorepluginSearchProviderAdapter()
    results = adapter.search(query="bitcoin", locale="en", safesearch="moderate", max_results=3)

    assert results
    assert observed_kl
    assert all(value != "en-en" for value in observed_kl)
    assert "en-us" in observed_kl


def test_ddg_lite_adapter_parses_result_links(monkeypatch):
    import amo_bot.ai.webtool_provider_adapter as m

    lite_html = '''
    <html><body>
      <a class="result-link" href="https://example.org/news">Example News</a>
      <td class="result-snippet">Fresh update from lite index.</td>
    </body></html>
    '''

    monkeypatch.setattr(
        m.httpx,
        "Client",
        lambda **kwargs: _DummyClient(
            {
                "https://lite.duckduckgo.com/lite/": _DummyResponse(lite_html, status_code=200),
                "https://html.duckduckgo.com/html/": _DummyResponse("", status_code=200),
            }
        ),
    )

    adapter = _CorepluginSearchProviderAdapter()
    results = adapter.search(query="bitcoin", locale="en", safesearch="moderate", max_results=3)

    assert len(results) == 1
    assert results[0].url == "https://example.org/news"
    assert results[0].title == "Example News"
    assert "Fresh update" in results[0].snippet
