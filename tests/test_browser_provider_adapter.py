from __future__ import annotations

import types

import pytest

from amo_bot.ai.webtool_provider_adapter import (
    RealBrowserProviderAdapter,
    _PlaywrightDeps,
    _detect_system_chromium_executable,
)


def test_browser_adapter_blocks_non_https():
    adapter = RealBrowserProviderAdapter(deps=None)
    with pytest.raises(ValueError):
        adapter.render(url="http://example.com", timeout_seconds=1.0)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "data:text/plain,hello",
        "javascript:alert(1)",
        "chrome://settings",
        "https://localhost",
        "https://127.0.0.1",
        "https://[::1]",
    ],
)
def test_browser_adapter_blocks_disallowed_targets(url: str):
    adapter = RealBrowserProviderAdapter(deps=None)
    with pytest.raises(ValueError):
        adapter.render(url=url, timeout_seconds=1.0)


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
    assert len(out["text"]) == 1234
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
