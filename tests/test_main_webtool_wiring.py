from __future__ import annotations

import httpx

from amo_bot.ai.webtool_dispatcher import WebtoolCapabilityRequest
from amo_bot.ai.webtool_provider_adapter import RealBrowserProviderAdapter, RealWebscrapeProviderAdapter, RealWebsearchProviderAdapter
from amo_bot.ai.webtool_subagent import create_webtool_subagent_service
from amo_bot.auth.roles import Role
from amo_bot.db.base import create_session_factory
from amo_bot.db.init_db import init_db
from amo_bot.db.repositories import WebToolRoleQuotaRepository
import amo_bot.telegram.webtool_evidence as evidence_module
from amo_bot.main import SessionBoundWebtoolCapabilityDispatcher


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_runtime_like_webtool_dispatcher_wrapper_uses_fresh_session(tmp_path):
    db_path = tmp_path / "runtime_wiring.sqlite3"
    database_url = f"sqlite:///{db_path}"
    init_db(database_url)
    session_factory = create_session_factory(database_url)

    class _SessionBoundWebtoolCapabilityDispatcher:
        def __init__(self, *, session_factory):
            self._session_factory = session_factory

        def execute(self, request):
            from amo_bot.ai.webtool_dispatcher import WebtoolCapabilityDispatcher

            with self._session_factory() as session:
                repo = WebToolRoleQuotaRepository(session)
                browser_provider = None
                candidate = RealBrowserProviderAdapter(deps=None)
                if candidate.available:
                    browser_provider = candidate
                search_provider = RealWebsearchProviderAdapter(quota_limiter=repo)
                scrape_provider = RealWebscrapeProviderAdapter()
                service = create_webtool_subagent_service(
                    quota_repo=repo,
                    search_provider=search_provider,
                    scrape_provider=scrape_provider,
                    browser_provider=browser_provider,
                )
                dispatcher = WebtoolCapabilityDispatcher(quota_repo=repo, service=service)
                return dispatcher.execute(request)

    wrapper = _SessionBoundWebtoolCapabilityDispatcher(session_factory=session_factory)

    req = WebtoolCapabilityRequest(
        capability="websearch",
        user_id=123,
        role=Role.OWNER,
        chat_id=-1001,
        query="python",
    )

    result = wrapper.execute(req)

    assert result.reason != "search_provider_not_configured"


def test_runtime_like_webscraping_has_real_scrape_provider(tmp_path):
    db_path = tmp_path / "runtime_wiring_scrape.sqlite3"
    database_url = f"sqlite:///{db_path}"
    init_db(database_url)
    session_factory = create_session_factory(database_url)

    class _SessionBoundWebtoolCapabilityDispatcher:
        def __init__(self, *, session_factory):
            self._session_factory = session_factory

        def execute(self, request):
            from amo_bot.ai.webtool_dispatcher import WebtoolCapabilityDispatcher

            with self._session_factory() as session:
                repo = WebToolRoleQuotaRepository(session)
                service = create_webtool_subagent_service(
                    quota_repo=repo,
                    search_provider=RealWebsearchProviderAdapter(quota_limiter=repo),
                    scrape_provider=RealWebscrapeProviderAdapter(),
                    browser_provider=None,
                )
                dispatcher = WebtoolCapabilityDispatcher(quota_repo=repo, service=service)
                return dispatcher.execute(request)

    wrapper = _SessionBoundWebtoolCapabilityDispatcher(session_factory=session_factory)

    req = WebtoolCapabilityRequest(
        capability="webscraping",
        user_id=123,
        role=Role.OWNER,
        chat_id=-1001,
        url="https://example.com/",
    )

    result = wrapper.execute(req)

    assert result.reason != "scrape_provider_not_configured"


def test_runtime_webtool_wrapper_reuses_provider_health_across_execute_calls(monkeypatch, tmp_path):
    db_path = tmp_path / "runtime_wiring_provider_health.sqlite3"
    database_url = f"sqlite:///{db_path}"
    init_db(database_url)
    session_factory = create_session_factory(database_url)
    calls: list[str] = []

    class _UnavailableBrowserProvider:
        available = False

    def fake_get(url, *, params, timeout):
        calls.append(url)
        if "coingecko.com" in url:
            raise httpx.TimeoutException("coingecko timeout")
        assert url == "https://api.binance.com/api/v3/ticker/24hr"
        assert params["symbol"] == "BTCUSDT"
        return _Response({"lastPrice": "68000.00", "priceChangePercent": "1.25"})

    monkeypatch.setattr("amo_bot.main.RealBrowserProviderAdapter", _UnavailableBrowserProvider)
    monkeypatch.setattr(evidence_module.httpx, "get", fake_get)

    wrapper = SessionBoundWebtoolCapabilityDispatcher(session_factory=session_factory)
    req = WebtoolCapabilityRequest(
        capability="crypto",
        user_id=123,
        role=Role.OWNER,
        chat_id=-1001,
        query="BTC price now USD",
        locale="en",
    )

    first = wrapper.execute(req)
    second = wrapper.execute(req)

    assert first.allowed is True
    assert second.allowed is True
    assert first.result_type == "crypto_evidence"
    assert second.result_type == "crypto_evidence"
    assert calls == [
        "https://api.coingecko.com/api/v3/simple/price",
        "https://api.binance.com/api/v3/ticker/24hr",
        "https://api.binance.com/api/v3/ticker/24hr",
    ]
