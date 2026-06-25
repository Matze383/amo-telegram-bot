from __future__ import annotations

import httpx

from amo_bot.ai.webtool_dispatcher import WebtoolCapabilityRequest
from amo_bot.ai.webtool_provider_adapter import RealBrowserProviderAdapter, RealWebscrapeProviderAdapter, RealWebsearchProviderAdapter
from amo_bot.ai.webtool_subagent import create_webtool_subagent_service
from amo_bot.auth.roles import Role
from amo_bot.db.base import create_session_factory
from amo_bot.db.init_db import init_db
from amo_bot.db.repositories import ResearchSourcePreferenceRepository, WebToolRoleQuotaRepository
import amo_bot.telegram.webtool_evidence as evidence_module
from amo_bot.main import SessionBoundSourcePreferenceRepository, SessionBoundWebtoolCapabilityDispatcher


class _StopFlow(RuntimeError):
    pass


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


def test_session_bound_source_preference_repository_uses_fresh_session(tmp_path):
    db_path = tmp_path / "source_preferences.sqlite3"
    database_url = f"sqlite:///{db_path}"
    init_db(database_url)
    session_factory = create_session_factory(database_url)

    with session_factory() as session:
        ResearchSourcePreferenceRepository(session).record_preference(
            host="www.runtime.example",
            domain="news",
            signal="trusted",
            chat_id=-100,
            topic_id=7,
        )

    wrapper = SessionBoundSourcePreferenceRepository(session_factory=session_factory)
    preferences = wrapper.list_for_hosts(
        source_hosts=("runtime.example",),
        domain="news",
        chat_id=-100,
        topic_id=7,
        user_id=42,
    )

    assert preferences["runtime.example"].signal == "trusted"


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


def test_main_runtime_wires_session_factory_into_web_evidence_pipeline(monkeypatch, tmp_path):
    from amo_bot import main as main_module

    monkeypatch.setenv("BOT_TOKEN", "123:ABC")
    monkeypatch.setenv("WEBUI_PASSWORD", "secret")
    monkeypatch.setenv("WEBUI_SECRET_KEY", "unit-test-webui-secret-key-0123456789abcdef")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'bot.db'}")
    monkeypatch.setenv("OFFSET_STATE_FILE", str(tmp_path / "offset.json"))
    monkeypatch.setenv("BOT_PID_FILE", str(tmp_path / "amo_bot.pid"))
    monkeypatch.setenv("AMO_PLUGIN_DIR", str(tmp_path / "plugins"))
    monkeypatch.setenv("WEBUI_OWNER_TELEGRAM_ID", "")
    monkeypatch.setenv("AMO_ENV_OVERRIDE", "0")
    monkeypatch.setenv("WEBUI_LOGIN_DELAY_BASE_SECONDS", "0.25")
    monkeypatch.setenv("WEBUI_LOGIN_DELAY_MAX_SECONDS", "2.0")

    captured: dict[str, object] = {}

    class _DummyPipeline:
        def __init__(self, *, session_factory=None) -> None:
            captured["pipeline_session_factory"] = session_factory

    class _DummyDispatcher:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            captured["dispatcher_pipeline"] = kwargs["web_evidence_pipeline"]

    async def _fake_run_polling(*args, **kwargs):  # noqa: ANN002,ANN003
        raise _StopFlow()

    monkeypatch.setattr(main_module, "WebEvidencePipeline", _DummyPipeline)
    monkeypatch.setattr(main_module, "Dispatcher", _DummyDispatcher)
    monkeypatch.setattr(main_module, "run_polling", _fake_run_polling)

    try:
        main_module.run([])
    except _StopFlow:
        pass

    assert captured["pipeline_session_factory"] is not None
    assert captured["dispatcher_pipeline"].__class__ is _DummyPipeline


def test_main_runtime_wires_current_info_when_enabled(monkeypatch, tmp_path):
    from amo_bot import main as main_module

    monkeypatch.setenv("BOT_TOKEN", "123:ABC")
    monkeypatch.setenv("WEBUI_PASSWORD", "secret")
    monkeypatch.setenv("WEBUI_SECRET_KEY", "unit-test-webui-secret-key-0123456789abcdef")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'bot.db'}")
    monkeypatch.setenv("OFFSET_STATE_FILE", str(tmp_path / "offset.json"))
    monkeypatch.setenv("BOT_PID_FILE", str(tmp_path / "amo_bot.pid"))
    monkeypatch.setenv("AMO_PLUGIN_DIR", str(tmp_path / "plugins"))
    monkeypatch.setenv("WEBUI_OWNER_TELEGRAM_ID", "")
    monkeypatch.setenv("AMO_ENV_OVERRIDE", "0")
    monkeypatch.setenv("WEBUI_LOGIN_DELAY_BASE_SECONDS", "0.25")
    monkeypatch.setenv("WEBUI_LOGIN_DELAY_MAX_SECONDS", "2.0")
    monkeypatch.setenv("AMO_CURRENT_INFO_ENABLED", "true")
    monkeypatch.setenv("AMO_CURRENT_INFO_TIMEOUT_SECONDS", "6")
    monkeypatch.setenv("AMO_CURRENT_INFO_LATE_SYNTHESIS_TIMEOUT_SECONDS", "42")
    monkeypatch.setenv("AMO_CURRENT_INFO_MAX_RESULTS", "4")
    monkeypatch.setenv("AMO_CURRENT_INFO_MAX_DOCUMENTS", "2")

    captured: dict[str, object] = {}

    class _DummyDispatcher:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            captured.update(kwargs)

    async def _fake_run_polling(*args, **kwargs):  # noqa: ANN002,ANN003
        raise _StopFlow()

    monkeypatch.setattr(main_module, "Dispatcher", _DummyDispatcher)
    monkeypatch.setattr(main_module, "run_polling", _fake_run_polling)
    vector_components = (object(), object(), object())

    monkeypatch.setattr(main_module, "build_search_broker_from_settings", lambda settings: object())
    monkeypatch.setattr(main_module, "build_document_fetcher_from_settings", lambda settings: object())
    monkeypatch.setattr(
        main_module,
        "build_current_info_vector_components_from_settings",
        lambda settings, **kwargs: vector_components,
    )

    def _build_cached_fetch_provider(settings, *, session_factory, fetch_provider, vector_indexer=None):
        captured["cached_fetch_vector_indexer"] = vector_indexer
        return object()

    def _build_retrieval_provider(settings, *, session_factory, vector_components=None):
        captured["retrieval_vector_components"] = vector_components
        return object()

    monkeypatch.setattr(
        main_module,
        "build_cached_fetch_provider_from_settings",
        _build_cached_fetch_provider,
    )
    monkeypatch.setattr(
        main_module,
        "build_current_info_retrieval_provider_from_settings",
        _build_retrieval_provider,
    )

    try:
        main_module.run([])
    except _StopFlow:
        pass

    assert captured["current_info_enabled"] is True
    assert captured["current_info_service"] is not None
    assert captured["current_info_timeout_seconds"] == 6
    assert captured["current_info_late_synthesis_timeout_seconds"] == 42
    assert captured["current_info_max_results"] == 4
    assert captured["current_info_max_documents"] == 2
    assert captured["cached_fetch_vector_indexer"] is vector_components[0]
    assert captured["retrieval_vector_components"] is vector_components
