from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import Any

import httpx

from amo_bot.current_info import (
    CrawleeDocumentFetcher,
    DocumentFetchConfig,
    build_document_fetcher_from_settings,
    extract_document,
)


@dataclass(frozen=True)
class _FakeResponse:
    url: str
    status_code: int
    headers: dict[str, str]
    content: bytes


class _FakeHttpClient:
    def __init__(
        self,
        responses: list[_FakeResponse] | None = None,
        error: Exception | None = None,
        **kwargs: Any,
    ) -> None:
        self.responses = responses if responses is not None else []
        self.error = error
        self.kwargs = kwargs
        self.urls: list[str] = []

    def __enter__(self) -> _FakeHttpClient:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def get(self, url: str) -> _FakeResponse:
        self.urls.append(url)
        if self.error is not None:
            raise self.error
        if not self.responses:
            raise AssertionError(f"unexpected fetch for {url}")
        return self.responses.pop(0)


class _FakeHttpClientFactory:
    def __init__(self, responses: list[_FakeResponse] | None = None, error: Exception | None = None) -> None:
        self.responses = list(responses or ())
        self.error = error
        self.clients: list[_FakeHttpClient] = []

    def __call__(self, **kwargs: Any) -> _FakeHttpClient:
        client = _FakeHttpClient(responses=self.responses, error=self.error, **kwargs)
        self.clients.append(client)
        return client


def test_extract_document_from_html_fixture_includes_metadata_and_quality() -> None:
    html = b"""
    <!doctype html>
    <html>
      <head>
        <title>Fallback Title</title>
        <link rel="canonical" href="/canonical-article">
        <meta property="og:title" content="Canonical News Title">
        <meta property="article:published_time" content="2026-06-15T09:30:00+00:00">
        <meta name="dateModified" content="2026-06-16T10:30:00+00:00">
      </head>
      <body>
        <nav>Skip navigation text</nav>
        <main>
          <article>
            <h1>Canonical News Title</h1>
            <p>First paragraph with current facts.</p>
            <p>Second paragraph has enough detail to pass extraction quality checks and remain useful.</p>
          </article>
        </main>
        <footer>Skip footer text</footer>
      </body>
    </html>
    """

    document = extract_document(
        content=html,
        url="https://example.com/news/source",
        status_code=200,
        headers={"content-type": "text/html; charset=utf-8"},
        provider="unit",
    )

    assert document.url == "https://example.com/canonical-article"
    assert document.title == "Fallback Title"
    assert "First paragraph with current facts." in document.text
    assert "Skip navigation text" not in document.text
    assert document.metadata["canonical_url"] == "https://example.com/canonical-article"
    assert document.metadata["final_url"] == "https://example.com/news/source"
    assert document.metadata["published_at"] == "2026-06-15T09:30:00+00:00"
    assert document.metadata["modified_at"] == "2026-06-16T10:30:00+00:00"
    assert document.metadata["extraction_quality"]["text_length"] == len(document.text)


def test_fetch_follows_redirect_and_uses_canonical_url(monkeypatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)
    html = b"""
    <html>
      <head><link rel="canonical" href="https://example.org/canonical"></head>
      <body><main><p>Redirected article body with enough detail for current-info extraction.</p></main></body>
    </html>
    """
    factory = _FakeHttpClientFactory(
        [
            _FakeResponse(
                url="https://example.com/start",
                status_code=302,
                headers={"location": "https://example.org/final"},
                content=b"",
            ),
            _FakeResponse(
                url="https://example.org/final",
                status_code=200,
                headers={"content-type": "text/html; charset=utf-8"},
                content=html,
            ),
        ]
    )
    fetcher = CrawleeDocumentFetcher(
        DocumentFetchConfig(prefer_crawlee=False),
        http_client_factory=factory,
    )

    document = fetcher.fetch(url="https://example.com/start", locale="en")

    assert document is not None
    assert document.url == "https://example.org/canonical"
    assert document.metadata["final_url"] == "https://example.org/final"
    assert [client.urls for client in factory.clients] == [
        ["https://example.com/start"],
        ["https://example.org/final"],
    ]


def test_fetch_returns_none_for_oversized_content_length(monkeypatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)
    factory = _FakeHttpClientFactory(
        [
            _FakeResponse(
                url="https://example.com/large",
                status_code=200,
                headers={"content-type": "text/html", "content-length": "20"},
                content=b"",
            )
        ]
    )
    fetcher = CrawleeDocumentFetcher(
        DocumentFetchConfig(prefer_crawlee=False, max_bytes=10),
        http_client_factory=factory,
    )

    assert fetcher.fetch(url="https://example.com/large", locale="en") is None


def test_fetch_returns_none_for_oversized_body(monkeypatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)
    factory = _FakeHttpClientFactory(
        [
            _FakeResponse(
                url="https://example.com/large-body",
                status_code=200,
                headers={"content-type": "text/html"},
                content=b"<html><body>" + (b"x" * 20) + b"</body></html>",
            )
        ]
    )
    fetcher = CrawleeDocumentFetcher(
        DocumentFetchConfig(prefer_crawlee=False, max_bytes=10),
        http_client_factory=factory,
    )

    assert fetcher.fetch(url="https://example.com/large-body", locale="en") is None


def test_fetch_returns_none_for_invalid_mime_type(monkeypatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)
    factory = _FakeHttpClientFactory(
        [
            _FakeResponse(
                url="https://example.com/image.png",
                status_code=200,
                headers={"content-type": "image/png"},
                content=b"png",
            )
        ]
    )
    fetcher = CrawleeDocumentFetcher(
        DocumentFetchConfig(prefer_crawlee=False),
        http_client_factory=factory,
    )

    assert fetcher.fetch(url="https://example.com/image.png", locale="en") is None


def test_fetch_blocks_private_ip_before_http_client_is_created() -> None:
    factory = _FakeHttpClientFactory()
    fetcher = CrawleeDocumentFetcher(
        DocumentFetchConfig(prefer_crawlee=False),
        http_client_factory=factory,
    )

    assert fetcher.fetch(url="http://127.0.0.1/admin", locale="en") is None
    assert factory.clients == []


def test_fetch_blocks_hostname_resolving_to_private_ip(monkeypatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _private_dns)
    factory = _FakeHttpClientFactory()
    fetcher = CrawleeDocumentFetcher(
        DocumentFetchConfig(prefer_crawlee=False),
        http_client_factory=factory,
    )

    assert fetcher.fetch(url="https://internal.example/status", locale="en") is None
    assert factory.clients == []


def test_fetch_blocks_redirect_to_private_ip(monkeypatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)
    factory = _FakeHttpClientFactory(
        [
            _FakeResponse(
                url="https://example.com/start",
                status_code=302,
                headers={"location": "http://127.0.0.1/admin"},
                content=b"",
            )
        ]
    )
    fetcher = CrawleeDocumentFetcher(
        DocumentFetchConfig(prefer_crawlee=False),
        http_client_factory=factory,
    )

    assert fetcher.fetch(url="https://example.com/start", locale="en") is None
    assert [client.urls for client in factory.clients] == [["https://example.com/start"]]


def test_fetch_returns_none_for_timeout_and_http_failure(monkeypatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)
    timeout_fetcher = CrawleeDocumentFetcher(
        DocumentFetchConfig(prefer_crawlee=False),
        http_client_factory=_FakeHttpClientFactory(error=httpx.TimeoutException("timeout")),
    )
    failure_fetcher = CrawleeDocumentFetcher(
        DocumentFetchConfig(prefer_crawlee=False),
        http_client_factory=_FakeHttpClientFactory(error=httpx.TransportError("failed")),
    )

    assert timeout_fetcher.fetch(url="https://example.com/timeout", locale="en") is None
    assert failure_fetcher.fetch(url="https://example.com/failure", locale="en") is None


def test_build_document_fetcher_from_settings_uses_current_info_fetch_config() -> None:
    class _Settings:
        amo_document_fetch_timeout_seconds = 2.5
        amo_document_fetch_max_bytes = 12345
        amo_document_fetch_max_redirects = 2
        amo_document_fetch_prefer_crawlee = False

    fetcher = build_document_fetcher_from_settings(_Settings(), http_client_factory=_FakeHttpClientFactory())

    assert fetcher._config == DocumentFetchConfig(  # noqa: SLF001
        timeout_seconds=2.5,
        max_bytes=12345,
        max_redirects=2,
        prefer_crawlee=False,
    )


def _public_dns(host: str, port: object, *args: object, **kwargs: object) -> list[tuple[Any, ...]]:
    del host, port, args, kwargs
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]


def _private_dns(host: str, port: object, *args: object, **kwargs: object) -> list[tuple[Any, ...]]:
    del host, port, args, kwargs
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443))]
