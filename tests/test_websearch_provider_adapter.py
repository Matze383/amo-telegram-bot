from amo_bot.ai.webtool_provider_adapter import _CorepluginSearchProviderAdapter


class _DummyResponse:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code


class _DummyClient:
    def __init__(self, response: _DummyResponse) -> None:
        self._response = response

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, url: str, params: dict[str, str]):
        assert url.startswith("https://")
        assert "q" in params
        return self._response


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

    monkeypatch.setattr(m.httpx, "Client", lambda **kwargs: _DummyClient(_DummyResponse(DDG_HTML)))

    adapter = _CorepluginSearchProviderAdapter()
    results = adapter.search(query="bitcoin price now", locale="en", safesearch="moderate", max_results=2)

    assert len(results) == 2
    assert results[0].url == "https://example.com/a"
    assert results[0].title == "Title A"
    assert "Snippet" in results[0].snippet


def test_ddg_adapter_handles_4xx_as_empty(monkeypatch):
    import amo_bot.ai.webtool_provider_adapter as m

    monkeypatch.setattr(m.httpx, "Client", lambda **kwargs: _DummyClient(_DummyResponse("", status_code=403)))

    adapter = _CorepluginSearchProviderAdapter()
    assert adapter.search(query="x", locale="en", safesearch="moderate", max_results=3) == ()
