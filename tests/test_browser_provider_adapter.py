from __future__ import annotations

import types

import pytest

from amo_bot.ai.webtool_provider_adapter import (
    RealBrowserProviderAdapter,
    RealWebscrapeProviderAdapter,
    _PlaywrightDeps,
    _detect_system_chromium_executable,
    _install_bounded_browser_routes,
)
from amo_bot.ai.webscraping_coreplugin import WebscrapingHTTPResponse, WebscrapingPolicyConfig


def _public_dns(_host="example.com", _port=443, *args, **kwargs):
    return [(None, None, None, "", ("93.184.216.34", _port))]


def _deps_for_pages(pages, launch_calls=None):
    launch_calls = launch_calls if launch_calls is not None else []

    class _FakeContext:
        def __init__(self):
            self._pages = list(pages)

        def new_page(self):
            return self._pages.pop(0)

        def close(self):
            return None

    class _FakeBrowser:
        def new_context(self, ignore_https_errors):
            return _FakeContext()

        def close(self):
            return None

    class _FakeChromium:
        def launch(self, **kwargs):
            launch_calls.append(kwargs)
            return _FakeBrowser()

    class _FakePW:
        chromium = _FakeChromium()

    class _PWCtx:
        def __enter__(self):
            return _FakePW()

        def __exit__(self, exc_type, exc, tb):
            return False

    return _PlaywrightDeps(sync_playwright=lambda: _PWCtx(), timeout_error_cls=TimeoutError)


class _FakeResponse:
    status = 200


class _FakeLocator:
    def __init__(self, text: str):
        self._text = text

    def inner_text(self, timeout):
        return self._text


class _FakePage:
    def __init__(self, text: str, *, title: str = "Live page"):
        self._text = text
        self._title = title
        self.routes = []
        self.init_scripts = []
        self.goto_calls = []

    def route(self, pattern, handler):
        self.routes.append((pattern, handler))

    def add_init_script(self, script):
        self.init_scripts.append(script)

    def goto(self, url, wait_until, timeout):
        self.goto_calls.append((url, wait_until, timeout))
        return _FakeResponse()

    def locator(self, selector):
        assert selector == "body"
        return _FakeLocator(self._text)

    def title(self):
        return self._title


def test_browser_adapter_allows_http_and_returns_evidence(monkeypatch):
    monkeypatch.setattr("amo_bot.ai.webtool_provider_adapter.socket.getaddrinfo", _public_dns)
    monkeypatch.setattr("amo_bot.ai.webtool_provider_adapter.shutil.which", lambda _name: None)
    page = _FakePage("Live 14:03 goal confirmed. Table updated after JavaScript rendered.", title="Match liveblog")
    adapter = RealBrowserProviderAdapter(deps=_deps_for_pages([page]))

    out = adapter.render(url="http://example.com/live", timeout_seconds=1.0)

    assert out["status_code"] == 200
    assert out["url"] == "http://example.com/live"
    assert out["title"] == "Match liveblog"
    assert out["headers"] == {}
    assert out["page_count"] == 1
    assert out["text"].startswith("1. Match liveblog (")
    assert "Live 14:03 goal confirmed." in out["text"]
    assert tuple(out["snippets"]) == ("Live 14:03 goal confirmed.", "Table updated after JavaScript rendered.")


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "data:text/plain,hello",
        "javascript:alert(1)",
        "chrome://settings",
        "https://user:pass@example.com",
        "http://localhost",
        "https://localhost",
        "http://127.0.0.1",
        "https://127.0.0.1",
        "https://[::1]",
    ],
)
def test_browser_adapter_blocks_disallowed_targets(url: str):
    adapter = RealBrowserProviderAdapter(deps=None)
    with pytest.raises(ValueError):
        adapter.render(url=url, timeout_seconds=1.0)


def test_browser_adapter_blocks_private_dns_resolution(monkeypatch):
    monkeypatch.setattr(
        "amo_bot.ai.webtool_provider_adapter.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, "", ("10.0.0.5", 443))],
    )
    adapter = RealBrowserProviderAdapter(deps=None)
    with pytest.raises(ValueError):
        adapter.render(url="https://internal.example", timeout_seconds=1.0)


def test_browser_route_blocks_private_network_and_form_submits(monkeypatch):
    monkeypatch.setattr("amo_bot.ai.webtool_provider_adapter.socket.getaddrinfo", _public_dns)
    page = _FakePage("ok")
    _install_bounded_browser_routes(page)
    assert len(page.routes) == 1
    assert len(page.init_scripts) == 1
    assert "addEventListener('submit'" in page.init_scripts[0]
    _pattern, handler = page.routes[0]

    class _Route:
        def __init__(self):
            self.action = ""

        def abort(self):
            self.action = "abort"

        def continue_(self):
            self.action = "continue"

    class _Request:
        def __init__(self, url: str, method: str):
            self.url = url
            self.method = method

    private_route = _Route()
    handler(private_route, _Request("http://127.0.0.1/admin", "GET"))
    assert private_route.action == "abort"

    post_route = _Route()
    handler(post_route, _Request("https://example.com/form", "POST"))
    assert post_route.action == "abort"

    get_route = _Route()
    handler(get_route, _Request("https://example.com/script.js", "GET"))
    assert get_route.action == "continue"


def test_browser_adapter_caps_pages_per_request(monkeypatch):
    monkeypatch.setattr("amo_bot.ai.webtool_provider_adapter.socket.getaddrinfo", _public_dns)
    pages = [_FakePage(f"Event {index}.", title=f"Page {index}") for index in range(1, 4)]
    adapter = RealBrowserProviderAdapter(max_pages=2, deps=_deps_for_pages(pages))

    out = adapter.render_pages(
        urls=["https://example.com/one", "https://example.com/two", "https://example.com/three"],
        timeout_seconds=5.0,
    )

    assert out["page_count"] == 2
    assert out["max_pages"] == 2
    assert "Page 1" in out["text"]
    assert "Page 2" in out["text"]
    assert "Page 3" not in out["text"]


def test_browser_adapter_stops_when_time_budget_expires(monkeypatch):
    monkeypatch.setattr("amo_bot.ai.webtool_provider_adapter.socket.getaddrinfo", _public_dns)
    times = iter([0.0, 0.0, 1.0])
    monkeypatch.setattr("amo_bot.ai.webtool_provider_adapter.time.monotonic", lambda: next(times, 1.0))
    pages = [_FakePage("First event.", title="First"), _FakePage("Second event.", title="Second")]
    adapter = RealBrowserProviderAdapter(max_pages=2, time_budget_seconds=0.5, deps=_deps_for_pages(pages))

    out = adapter.render_pages(urls=["https://example.com/one", "https://example.com/two"], timeout_seconds=5.0)

    assert out["page_count"] == 1
    assert "First event." in out["text"]
    assert "Second event." not in out["text"]


def test_browser_adapter_fail_closed_without_playwright(monkeypatch):
    monkeypatch.setattr("amo_bot.ai.webtool_provider_adapter._load_playwright_deps", lambda: None)
    adapter = RealBrowserProviderAdapter()
    with pytest.raises(RuntimeError):
        adapter.render(url="https://example.com", timeout_seconds=1.0)


def test_detect_system_chromium_executable_returns_first_available(monkeypatch):
    mapping = {
        "chromium": None,
        "chromium-browser": "/usr/bin/chromium-browser",
        "google-chrome": "/usr/bin/google-chrome",
        "google-chrome-stable": "/usr/bin/google-chrome-stable",
    }
    monkeypatch.setattr("amo_bot.ai.webtool_provider_adapter.shutil.which", lambda name: mapping.get(name))
    assert _detect_system_chromium_executable() == "/usr/bin/chromium-browser"


def test_browser_adapter_output_is_limited(monkeypatch):
    monkeypatch.setattr("amo_bot.ai.webtool_provider_adapter.shutil.which", lambda name: "/usr/bin/chromium-browser" if name == "chromium-browser" else None)
    long_text = "x" * 10_000

    class _FakeLocator:
        def inner_text(self, timeout):
            return long_text

    class _FakeResponse:
        status = 200

    class _FakePage:
        def goto(self, url, wait_until, timeout):
            return _FakeResponse()

        def locator(self, selector):
            assert selector == "body"
            return _FakeLocator()

    class _FakeContext:
        closed = False

        def new_page(self):
            return _FakePage()

        def close(self):
            self.closed = True

    class _FakeBrowser:
        closed = False

        def new_context(self, ignore_https_errors):
            return _FakeContext()

        def close(self):
            self.closed = True

    launch_calls: list[dict[str, object]] = []

    class _FakeChromium:
        def launch(self, **kwargs):
            launch_calls.append(kwargs)
            return _FakeBrowser()

    class _FakePW:
        chromium = _FakeChromium()

    class _PWCtx:
        def __enter__(self):
            return _FakePW()

        def __exit__(self, exc_type, exc, tb):
            return False

    deps = _PlaywrightDeps(sync_playwright=lambda: _PWCtx(), timeout_error_cls=TimeoutError)
    adapter = RealBrowserProviderAdapter(max_output_chars=1234, deps=deps)
    out = adapter.render(url="https://example.com", timeout_seconds=1.0)
    assert out["status_code"] == 200
    assert len(out["text"]) <= 1234
    assert len(tuple(out["snippets"])[0]) == 500
    assert launch_calls == [{"headless": True, "executable_path": "/usr/bin/chromium-browser"}]


def test_browser_adapter_uses_playwright_default_if_no_system_chromium(monkeypatch):
    monkeypatch.setattr("amo_bot.ai.webtool_provider_adapter.shutil.which", lambda _name: None)

    launch_calls: list[dict[str, object]] = []

    class _FakeLocator:
        def inner_text(self, timeout):
            return "ok"

    class _FakeResponse:
        status = 200

    class _FakePage:
        def goto(self, url, wait_until, timeout):
            return _FakeResponse()

        def locator(self, selector):
            assert selector == "body"
            return _FakeLocator()

    class _FakeContext:
        def new_page(self):
            return _FakePage()

        def close(self):
            return None

    class _FakeBrowser:
        def new_context(self, ignore_https_errors):
            return _FakeContext()

        def close(self):
            return None

    class _FakeChromium:
        def launch(self, **kwargs):
            launch_calls.append(kwargs)
            return _FakeBrowser()

    class _FakePW:
        chromium = _FakeChromium()

    class _PWCtx:
        def __enter__(self):
            return _FakePW()

        def __exit__(self, exc_type, exc, tb):
            return False

    deps = _PlaywrightDeps(sync_playwright=lambda: _PWCtx(), timeout_error_cls=TimeoutError)
    adapter = RealBrowserProviderAdapter(deps=deps)
    out = adapter.render(url="https://example.com", timeout_seconds=1.0)
    assert out["status_code"] == 200
    assert launch_calls == [{"headless": True}]


def test_static_scrape_adapter_default_fetch_uses_generic_browser_headers(monkeypatch):
    captured: dict[str, object] = {}

    class _FakeResponse:
        status_code = 200
        headers = {"content-type": "text/html; charset=utf-8"}
        content = b"<html><body><h1>Static headline</h1><p>Visible page text.</p></body></html>"

    class _FakeClient:
        def __init__(self, *, timeout, follow_redirects, headers):
            captured["timeout"] = timeout
            captured["follow_redirects"] = follow_redirects
            captured["headers"] = headers

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url):
            captured["url"] = url
            return _FakeResponse()

    monkeypatch.setattr("amo_bot.ai.webtool_provider_adapter.httpx.Client", _FakeClient)

    adapter = RealWebscrapeProviderAdapter()
    out = adapter.fetch(url="https://93.184.216.34/page", timeout_seconds=1.5)

    assert out["status_code"] == 200
    assert "Static headline" in str(out["text"])
    assert captured["timeout"] == 1.5
    assert captured["follow_redirects"] is True
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers["User-Agent"].startswith("Mozilla/5.0")
    assert "text/html" in headers["Accept"]
    assert "en-US" in headers["Accept-Language"]


def test_static_scrape_adapter_preserves_http_status_reason():
    def fake_http_get(url: str, timeout_seconds: float) -> WebscrapingHTTPResponse:
        return WebscrapingHTTPResponse(
            status_code=403,
            headers={"content-type": "text/html"},
            body=b"<html><body>blocked</body></html>",
        )

    adapter = RealWebscrapeProviderAdapter(
        policy=WebscrapingPolicyConfig(
            enabled=True,
            allowlist_hosts=frozenset({"example.com"}),
            enforce_robots=False,
        ),
        http_get=fake_http_get,
    )

    out = adapter.fetch(url="https://example.com/page", timeout_seconds=1.0)

    assert out["status_code"] == 403
    assert out["reason_code"] == "http_status_not_ok"
