from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from amo_bot.ai.response_strategy import ResponseStrategy
from amo_bot.auth.roles import Role
from amo_bot.db.base import create_session_factory
from amo_bot.db.init_db import init_db
from amo_bot.db.models import ResearchProvider
from amo_bot.db.repositories import ResearchProviderHealthRepository, ResearchSourceObservationRepository
from amo_bot.telegram import sports_query
from amo_bot.telegram.webtool_domain_profiles import build_domain_research_profile
import amo_bot.telegram.webtool_evidence as evidence_module
from amo_bot.telegram.webtool_evidence import (
    BinanceTickerEvidenceProvider,
    CoinGeckoEvidenceProvider,
    DomainEvidenceResult,
    EvidenceProviderCandidate,
    EvidenceSource,
    OpenMeteoEvidenceProvider,
    PROVIDER_REGISTRY,
    ProviderDefinition,
    ProviderHealthRegistry,
    ResilientCryptoEvidenceProvider,
    ResilientWeatherEvidenceProvider,
    WebEvidencePipeline,
    WttrInEvidenceProvider,
    classify_evidence_domain,
)

from test_webtool_chat_integration import _allowing_router_decision, _mk_dispatcher, _mk_message, _mk_sequence_dispatcher


@pytest.fixture(autouse=True)
def _legacy_webtool_evidence_tests_use_direct_answer_strategy(monkeypatch):
    """Keep autoresearch evidence tests on the legacy webtool path."""

    def _classify_response_strategy(_message, *, context=None):
        return ResponseStrategy("direct_answer", "legacy_webtool_evidence_test")

    async def _skip_current_info_autoreply(*_args, **_kwargs):
        return False

    monkeypatch.setattr("amo_bot.telegram.dispatcher.classify_response_strategy", _classify_response_strategy)
    monkeypatch.setattr(
        "amo_bot.telegram.dispatcher.Dispatcher._maybe_handle_current_info_autoreply",
        _skip_current_info_autoreply,
    )


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def get(self, url, *, params, timeout):
        self.calls.append((url, params, timeout))
        return _Response(self.payloads.pop(0))


@dataclass
class _ProviderCall:
    provider: str
    query: str
    locale: str


class _WeatherProvider:
    def __init__(self, name: str, result: DomainEvidenceResult):
        self.name = name
        self.result = result
        self.calls: list[_ProviderCall] = []

    def get_weather(self, *, query: str, locale: str):
        self.calls.append(_ProviderCall(self.name, query, locale))
        return self.result


class _CryptoProvider:
    def __init__(self, name: str, result: DomainEvidenceResult):
        self.name = name
        self.result = result
        self.calls: list[_ProviderCall] = []

    def get_crypto(self, *, query: str, locale: str):
        self.calls.append(_ProviderCall(self.name, query, locale))
        return self.result


def _fresh_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _old_iso(hours: int = 24) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours)).replace(microsecond=0).isoformat()


def _db(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'webtool_evidence.sqlite3'}"
    init_db(database_url)
    return create_session_factory(database_url)


def _add_research_provider(
    session_factory,
    *,
    provider_name: str,
    source_name: str,
    domain: str,
    profile_needs: str,
    source_type: str = "structured_official",
    strategy: str = "structured_first",
    enabled: bool = True,
    default_priority: int = 5,
) -> None:
    with session_factory() as session:
        session.add(
            ResearchProvider(
                provider_name=provider_name,
                source_name=source_name,
                domain=domain,
                enabled=enabled,
                default_priority=default_priority,
                fallback_allowed=True,
                min_confidence=0.8,
                max_age_seconds=900,
                metadata_json=json.dumps(
                    {
                        "profile_needs": profile_needs,
                        "source_type": source_type,
                        "strategy": strategy,
                    },
                    sort_keys=True,
                ),
            )
        )
        session.commit()


_TEST_WEATHER_FALLBACK = ProviderDefinition(
    "test_weather_fallback",
    "Test weather fallback",
    "weather",
    40,
    True,
    0.68,
    6 * 60 * 60,
)
_TEST_CRYPTO_FALLBACK = ProviderDefinition(
    "test_crypto_fallback",
    "Test crypto fallback",
    "crypto",
    35,
    True,
    0.70,
    15 * 60,
)


def test_domain_classifier_routes_problem_prompts():
    assert classify_evidence_domain("Wie stehen die Gruppen der Fußball WM?") == "sports"
    assert classify_evidence_domain("Was macht die ExampleTech Aktie?") == "stock"
    assert classify_evidence_domain("ETH kurs jetzt") == "crypto"
    assert classify_evidence_domain("Wie ist das Wetter morgen in Berlin?") == "weather"
    assert classify_evidence_domain("aktuelle News zu OpenAI") == "news"
    assert classify_evidence_domain("Erklär mir Python decorators") == "generic"
    assert classify_evidence_domain("Was ist ACMEUSDT auf Bybit?") == "crypto"
    assert classify_evidence_domain("Gibt es ExampleCo tokenized exposure auf Bybit?") == "crypto"
    assert classify_evidence_domain("Was macht Solana?") == "crypto"
    assert classify_evidence_domain("XRP price now") == "crypto"
    assert classify_evidence_domain("Wie steht Dogecoin aktuell?") == "crypto"
    assert classify_evidence_domain("BlorpCoin") == "crypto"
    assert classify_evidence_domain("FooToken") == "crypto"
    assert classify_evidence_domain("BlorpCoin token price now") == "crypto"
    assert classify_evidence_domain("coin price now") == "crypto"
    assert classify_evidence_domain("Was ist ein Blockchain token?") == "crypto"
    assert classify_evidence_domain("Ist SpaceX an der Börse?") == "stock"
    assert classify_evidence_domain("Ist Anthropic an der Börse?") == "stock"
    assert classify_evidence_domain("Ist Siemens an der Börse?") == "stock"
    assert classify_evidence_domain("Ist Adidas börsennotiert?") == "stock"
    assert classify_evidence_domain("Ist Quarvex Labs an der Börse?") == "stock"
    assert classify_evidence_domain("Ist AcmeBlubBla an der Börse?") == "stock"
    assert classify_evidence_domain("Ist FooBarBaz AG an der Börse?") == "stock"
    assert classify_evidence_domain("Kann man Anthropic Aktien kaufen?") == "stock"
    assert classify_evidence_domain("Kann man Siemens Aktien kaufen?") == "stock"
    assert classify_evidence_domain("Kann man Adidas Aktien kaufen?") == "stock"
    assert classify_evidence_domain("Kann man Quarvex Labs Aktien kaufen?") == "stock"
    assert classify_evidence_domain("Kann man AcmeBlubBla Aktien kaufen?") == "stock"
    assert classify_evidence_domain("Kann man FooBarBaz AG Aktien kaufen?") == "stock"
    assert classify_evidence_domain("Nasdaq Anthropic") == "stock"
    assert classify_evidence_domain("Nasdaq AcmeBlubBla") == "stock"
    assert classify_evidence_domain("NYSE Anthropic") == "stock"
    assert classify_evidence_domain("NYSE FooBarBaz AG") == "stock"
    assert classify_evidence_domain("Ist Anthropic öffentlich gelistet?") == "stock"


def test_domain_classifier_ignores_common_standalone_coin_and_token_phrases():
    prompts = [
        "Was ist ein Coin Toss?",
        "coin collector",
        "token bucket",
        "Wie funktioniert ein Token Bucket Algorithmus?",
    ]
    for prompt in prompts:
        assert classify_evidence_domain(prompt) == "generic", prompt


def test_open_meteo_provider_builds_confirmed_weather_evidence_from_mock():
    client = _FakeClient(
        [
            {"results": [{"name": "Berlin", "country_code": "DE", "latitude": 52.52, "longitude": 13.41}]},
            {
                "current": {
                    "time": "2026-06-11T10:00",
                    "temperature_2m": 21.4,
                    "precipitation": 0,
                    "weather_code": 3,
                    "wind_speed_10m": 12.5,
                },
                "current_units": {"temperature_2m": "°C", "precipitation": "mm", "wind_speed_10m": "km/h"},
                "daily": {
                    "time": ["2026-06-11", "2026-06-12"],
                    "temperature_2m_min": [14, 15],
                    "temperature_2m_max": [24, 26],
                    "precipitation_sum": [0.2, 1.0],
                },
            },
        ]
    )

    result = OpenMeteoEvidenceProvider(client=client).get_weather(query="Wetter morgen in Berlin", locale="de")

    assert result.confirmed is True
    assert result.domain == "weather"
    assert "Berlin, DE" in result.text
    assert "21.4 °C" in result.text
    assert "2026-06-11" in result.text
    assert result.sources[0].name == "Open-Meteo"
    assert client.calls[0][1]["name"] == "Berlin"


def test_coingecko_provider_builds_confirmed_crypto_evidence_from_mock():
    client = _FakeClient([{"ethereum": {"eur": 3123.45, "eur_24h_change": 2.5, "last_updated_at": 1781172000}}])

    result = CoinGeckoEvidenceProvider(client=client).get_crypto(query="ETH kurs jetzt", locale="de")

    assert result.confirmed is True
    assert result.domain == "crypto"
    assert "ethereum" in result.text
    assert "3123.45 EUR" in result.text
    assert "24h-Veränderung 2.50%" in result.text
    assert result.sources[0].name == "CoinGecko"
    assert client.calls[0][1]["ids"] == "ethereum"
    assert client.calls[0][1]["vs_currencies"] == "eur"


def test_wttr_provider_builds_confirmed_weather_evidence_from_mock():
    client = _FakeClient(
        [
            {
                "current_condition": [
                    {
                        "localObsDateTime": "2026-06-11 10:00 AM",
                        "temp_C": "21",
                        "humidity": "52",
                        "precipMM": "0.0",
                        "windspeedKmph": "11",
                    }
                ],
                "nearest_area": [{"areaName": [{"value": "Berlin"}], "country": [{"value": "Germany"}]}],
                "weather": [{"date": "2026-06-11", "mintempC": "14", "maxtempC": "24", "hourly": [{"precipMM": "0.1"}]}],
            }
        ]
    )

    result = WttrInEvidenceProvider(client=client).get_weather(query="Wetter in Berlin", locale="de")

    assert result.confirmed is True
    assert "Berlin, Germany" in result.text
    assert "Stand: 2026-06-11 10:00 AM" in result.text
    assert result.sources[0].name == "wttr.in"
    assert client.calls[0][0] == "https://wttr.in/Berlin"
    assert client.calls[0][1]["format"] == "j1"


def test_wttr_provider_fails_closed_when_current_values_are_incomplete():
    client = _FakeClient(
        [
            {
                "current_condition": [
                    {
                        "localObsDateTime": "2026-06-11 10:00 AM",
                        "temp_C": "21",
                        "humidity": "",
                        "precipMM": "0.0",
                        "windspeedKmph": "11",
                    }
                ]
            }
        ]
    )

    result = WttrInEvidenceProvider(client=client).get_weather(query="Wetter in Berlin", locale="de")

    assert result.confirmed is False
    assert result.text == ""
    assert result.warnings == ("weather_current_incomplete",)


def test_binance_provider_builds_confirmed_crypto_evidence_from_mock():
    client = _FakeClient([{"lastPrice": "3123.456", "priceChangePercent": "2.34"}])

    result = BinanceTickerEvidenceProvider(client=client).get_crypto(query="ETH price now USD", locale="en")

    assert result.confirmed is True
    assert "ethereum" in result.text
    assert "3123.46 USD/USDT" in result.text
    assert result.sources[0].name == "Binance"
    assert client.calls[0][1]["symbol"] == "ETHUSDT"


def test_binance_provider_fails_closed_for_unsupported_currency_and_unknown_asset():
    unsupported_currency = BinanceTickerEvidenceProvider(client=_FakeClient([])).get_crypto(query="ETH price now EUR", locale="de")
    unknown_asset = BinanceTickerEvidenceProvider(client=_FakeClient([])).get_crypto(query="FOOBAR price now USD", locale="en")

    assert unsupported_currency.confirmed is False
    assert unsupported_currency.warnings == ("crypto_fallback_supports_usd_only",)
    assert unknown_asset.confirmed is False
    assert unknown_asset.warnings == ("crypto_asset_not_recognized",)


def test_default_resilient_providers_include_real_fallback_candidates():
    weather = ResilientWeatherEvidenceProvider()
    crypto = ResilientCryptoEvidenceProvider()

    weather_names = tuple(candidate.definition.name for candidate in weather._candidates)
    crypto_names = tuple(candidate.definition.name for candidate in crypto._candidates)

    assert weather_names == ("open_meteo_weather", "wttr_in_weather")
    assert crypto_names == ("coingecko_crypto", "binance_crypto")
    assert all(candidate.definition.fallback_allowed for candidate in weather._candidates)
    assert all(candidate.definition.fallback_allowed for candidate in crypto._candidates)


def test_default_weather_primary_down_attempts_wttr_fallback(monkeypatch):
    calls: list[str] = []

    def fake_get(url, *, params, timeout):
        calls.append(url)
        if "open-meteo.com" in url:
            raise httpx.TimeoutException("open-meteo timeout")
        assert url == "https://wttr.in/Berlin"
        return _Response(
            {
                "current_condition": [
                    {
                        "localObsDateTime": "2026-06-11 10:00 AM",
                        "temp_C": "21",
                        "humidity": "52",
                        "precipMM": "0.0",
                        "windspeedKmph": "11",
                    }
                ],
                "nearest_area": [{"areaName": [{"value": "Berlin"}], "country": [{"value": "Germany"}]}],
            }
        )

    monkeypatch.setattr(evidence_module.httpx, "get", fake_get)

    result = ResilientWeatherEvidenceProvider().get_weather(query="Wetter in Berlin", locale="de")

    assert result.confirmed is True
    assert result.sources[0].name == "wttr.in"
    assert any("open_meteo_weather:unavailable" in warning for warning in result.warnings)
    assert calls == ["https://geocoding-api.open-meteo.com/v1/search", "https://wttr.in/Berlin"]


def test_default_crypto_primary_down_attempts_binance_fallback(monkeypatch):
    calls: list[str] = []

    def fake_get(url, *, params, timeout):
        calls.append(url)
        if "coingecko.com" in url:
            raise httpx.TimeoutException("coingecko timeout")
        assert url == "https://api.binance.com/api/v3/ticker/24hr"
        assert params["symbol"] == "ETHUSDT"
        return _Response({"lastPrice": "3123.456", "priceChangePercent": "2.34"})

    monkeypatch.setattr(evidence_module.httpx, "get", fake_get)

    result = ResilientCryptoEvidenceProvider().get_crypto(query="ETH price now USD", locale="en")

    assert result.confirmed is True
    assert result.sources[0].name == "Binance"
    assert any("coingecko_crypto:unavailable" in warning for warning in result.warnings)
    assert calls == ["https://api.coingecko.com/api/v3/simple/price", "https://api.binance.com/api/v3/ticker/24hr"]


def test_default_crypto_health_deprioritizes_broken_primary(monkeypatch):
    calls: list[str] = []
    health = ProviderHealthRegistry()
    health.record_failure("coingecko_crypto", "timeout")

    def fake_get(url, *, params, timeout):
        calls.append(url)
        if "coingecko.com" in url:
            raise AssertionError("CoinGecko should be deprioritized after health penalty")
        return _Response({"lastPrice": "68000.00", "priceChangePercent": "1.25"})

    monkeypatch.setattr(evidence_module.httpx, "get", fake_get)

    result = ResilientCryptoEvidenceProvider(health=health).get_crypto(query="BTC price now USD", locale="en")

    assert result.confirmed is True
    assert result.sources[0].name == "Binance"
    assert calls == ["https://api.binance.com/api/v3/ticker/24hr"]


def test_default_weather_fallback_result_without_quality_fails_closed(monkeypatch):
    def fake_get(url, *, params, timeout):
        if "open-meteo.com" in url:
            raise httpx.TimeoutException("open-meteo timeout")
        return _Response({"current_condition": [{"temp_C": "21", "humidity": "", "precipMM": "0.0", "windspeedKmph": "11"}]})

    monkeypatch.setattr(evidence_module.httpx, "get", fake_get)

    result = ResilientWeatherEvidenceProvider().get_weather(query="Wetter in Berlin", locale="de")

    assert result.confirmed is False
    assert result.status == "unavailable"
    assert result.text == ""
    assert any("weather_current_incomplete" in warning for warning in result.warnings)


def test_coingecko_down_fails_closed_without_unchecked_default_fallback():
    primary = _CryptoProvider(
        "coingecko",
        DomainEvidenceResult(domain="crypto", status="unavailable", confidence=0.0, text="", warnings=("crypto_provider_error:TimeoutError",)),
    )
    provider = ResilientCryptoEvidenceProvider(
        (EvidenceProviderCandidate(PROVIDER_REGISTRY["coingecko_crypto"], primary),)
    )

    result = provider.get_crypto(query="ETH price now USD", locale="en")

    assert result.confirmed is False
    assert result.status == "unavailable"
    assert result.text == ""
    assert primary.calls
    assert any("coingecko_crypto:unavailable" in warning for warning in result.warnings)


def test_coingecko_down_attempts_explicit_crypto_fallback_without_false_answer():
    primary = _CryptoProvider(
        "coingecko",
        DomainEvidenceResult(domain="crypto", status="unavailable", confidence=0.0, text="", warnings=("crypto_provider_error:TimeoutError",)),
    )
    fallback = _CryptoProvider(
        "binance",
        DomainEvidenceResult(
            domain="crypto",
            status="confirmed",
            confidence=0.78,
            text=f"Asset: ethereum. Stand: {_fresh_iso()}. Kurs: 3123.45 USD/USDT.",
            sources=(EvidenceSource("Binance", "https://api.binance.com/api/v3/ticker/24hr", _fresh_iso()),),
            warnings=("exchange_specific_usdt_pair",),
        ),
    )
    provider = ResilientCryptoEvidenceProvider(
        (
            EvidenceProviderCandidate(PROVIDER_REGISTRY["coingecko_crypto"], primary),
            EvidenceProviderCandidate(_TEST_CRYPTO_FALLBACK, fallback),
        )
    )

    result = provider.get_crypto(query="ETH price now USD", locale="en")

    assert result.confirmed is True
    assert "3123.45 USD/USDT" in result.text
    assert primary.calls and fallback.calls
    assert any("coingecko_crypto:unavailable" in warning for warning in result.warnings)


def test_open_meteo_down_fails_closed_without_unchecked_default_fallback():
    primary = _WeatherProvider(
        "open_meteo",
        DomainEvidenceResult(domain="weather", status="unavailable", confidence=0.0, text="", warnings=("weather_provider_error:TimeoutError",)),
    )
    provider = ResilientWeatherEvidenceProvider(
        (EvidenceProviderCandidate(PROVIDER_REGISTRY["open_meteo_weather"], primary),)
    )

    result = provider.get_weather(query="Wetter in Berlin", locale="de")

    assert result.confirmed is False
    assert result.status == "unavailable"
    assert result.text == ""
    assert primary.calls


def test_open_meteo_down_attempts_explicit_weather_fallback():
    primary = _WeatherProvider(
        "open_meteo",
        DomainEvidenceResult(domain="weather", status="unavailable", confidence=0.0, text="", warnings=("weather_provider_error:TimeoutError",)),
    )
    fallback = _WeatherProvider(
        "wttr",
        DomainEvidenceResult(
            domain="weather",
            status="confirmed",
            confidence=0.72,
            text=f"Ort: Berlin. Stand: {_fresh_iso()}. Aktuell: Temperatur 21 °C.",
            sources=(EvidenceSource("wttr.in", "https://wttr.in/Berlin", _fresh_iso()),),
        ),
    )
    provider = ResilientWeatherEvidenceProvider(
        (
            EvidenceProviderCandidate(PROVIDER_REGISTRY["open_meteo_weather"], primary),
            EvidenceProviderCandidate(_TEST_WEATHER_FALLBACK, fallback),
        )
    )

    result = provider.get_weather(query="Wetter in Berlin", locale="de")

    assert result.confirmed is True
    assert "Berlin" in result.text
    assert primary.calls and fallback.calls


def test_open_meteo_down_fail_closed_when_fallback_unconfirmed():
    primary = _WeatherProvider(
        "open_meteo",
        DomainEvidenceResult(domain="weather", status="unavailable", confidence=0.0, text="", warnings=("weather_provider_error:TimeoutError",)),
    )
    fallback = _WeatherProvider(
        "wttr",
        DomainEvidenceResult(domain="weather", status="unavailable", confidence=0.0, text="", warnings=("weather_current_missing",)),
    )
    provider = ResilientWeatherEvidenceProvider(
        (
            EvidenceProviderCandidate(PROVIDER_REGISTRY["open_meteo_weather"], primary),
            EvidenceProviderCandidate(_TEST_WEATHER_FALLBACK, fallback),
        )
    )

    result = provider.get_weather(query="Wetter in Berlin", locale="de")

    assert result.confirmed is False
    assert result.status == "unavailable"
    assert result.text == ""
    assert primary.calls and fallback.calls


def test_stale_provider_result_triggers_fallback_instead_of_confirming():
    stale = _CryptoProvider(
        "coingecko",
        DomainEvidenceResult(
            domain="crypto",
            status="confirmed",
            confidence=0.9,
            text=f"Asset: bitcoin. Stand: {_old_iso()}. Kurs: 1 USD.",
            sources=(EvidenceSource("CoinGecko", "https://api.coingecko.com/api/v3/simple/price", _old_iso()),),
        ),
    )
    fallback = _CryptoProvider(
        "binance",
        DomainEvidenceResult(
            domain="crypto",
            status="confirmed",
            confidence=0.78,
            text=f"Asset: bitcoin. Stand: {_fresh_iso()}. Kurs: 68000.00 USD/USDT.",
            sources=(EvidenceSource("Binance", "https://api.binance.com/api/v3/ticker/24hr", _fresh_iso()),),
        ),
    )
    provider = ResilientCryptoEvidenceProvider(
        (
            EvidenceProviderCandidate(PROVIDER_REGISTRY["coingecko_crypto"], stale),
            EvidenceProviderCandidate(_TEST_CRYPTO_FALLBACK, fallback),
        )
    )

    result = provider.get_crypto(query="BTC price now USD", locale="en")

    assert result.confirmed is True
    assert "68000.00" in result.text
    assert stale.calls and fallback.calls
    assert any("stale" in warning for warning in result.warnings)


def test_provider_health_influences_candidate_order():
    health = ProviderHealthRegistry()
    health.record_failure("coingecko_crypto", "timeout")
    primary = _CryptoProvider(
        "coingecko",
        DomainEvidenceResult(
            domain="crypto",
            status="confirmed",
            confidence=0.9,
            text=f"Asset: bitcoin. Stand: {_fresh_iso()}. Kurs: 67000 USD.",
            sources=(EvidenceSource("CoinGecko", "https://api.coingecko.com/api/v3/simple/price", _fresh_iso()),),
        ),
    )
    fallback = _CryptoProvider(
        "binance",
        DomainEvidenceResult(
            domain="crypto",
            status="confirmed",
            confidence=0.78,
            text=f"Asset: bitcoin. Stand: {_fresh_iso()}. Kurs: 68000 USD/USDT.",
            sources=(EvidenceSource("Binance", "https://api.binance.com/api/v3/ticker/24hr", _fresh_iso()),),
        ),
    )
    provider = ResilientCryptoEvidenceProvider(
        (
            EvidenceProviderCandidate(PROVIDER_REGISTRY["coingecko_crypto"], primary),
            EvidenceProviderCandidate(_TEST_CRYPTO_FALLBACK, fallback),
        ),
        health=health,
    )

    result = provider.get_crypto(query="BTC price now USD", locale="en")

    assert result.confirmed is True
    assert "68000 USD/USDT" in result.text
    assert fallback.calls
    assert primary.calls == []


def test_unknown_crypto_asset_is_not_blindly_matched():
    coingecko = CoinGeckoEvidenceProvider(client=_FakeClient([])).get_crypto(query="FOOBAR token price now", locale="en")

    assert coingecko.confirmed is False
    assert coingecko.warnings == ("crypto_asset_not_recognized",)


def test_unknown_crypto_asset_does_not_poison_provider_health():
    health = ProviderHealthRegistry()
    provider = ResilientCryptoEvidenceProvider(
        (
            EvidenceProviderCandidate(PROVIDER_REGISTRY["coingecko_crypto"], CoinGeckoEvidenceProvider(client=_FakeClient([]))),
        ),
        health=health,
    )

    result = provider.get_crypto(query="FOOBAR token price now", locale="en")

    assert result.confirmed is False
    assert result.status == "unavailable"
    assert "crypto_asset_not_recognized" in " ".join(result.warnings)
    assert health.get("coingecko_crypto").failure_count == 0


def test_pipeline_allows_stock_quote_built_in_web_research_without_structured_provider():
    pipeline = WebEvidencePipeline()

    stock = pipeline.evaluate(query="Was macht die ExampleTech Aktie?", locale="de")
    sports = pipeline.evaluate(query="Wie stehen die Gruppen der Fußball WM?", locale="de")

    assert stock.status == "needs_profiled_web_research"
    assert stock.warnings == (
        "stock_domain_profile_builtin_source:finance_quote",
        "strategy:verified_quote_web_research",
    )
    assert "Need: finance_quote" in stock.text
    assert sports.status == "unavailable"
    assert sports.warnings == ("sports_domain_profile_not_configured",)


def test_finance_quote_profile_uses_db_ranked_source_strategy(tmp_path):
    session_factory = _db(tmp_path)
    _add_research_provider(
        session_factory,
        provider_name="finance_official_quote",
        source_name="Official Exchange",
        domain="stock",
        profile_needs="finance_quote",
    )

    profile = build_domain_research_profile(
        session_factory=session_factory,
        domain="stock",
        query="ACME stock price now",
    )
    result = WebEvidencePipeline(session_factory=session_factory).evaluate(query="ACME stock price now", locale="en")

    assert profile.usable is True
    assert profile.need == "finance_quote"
    assert profile.strategy == "structured_first"
    assert profile.provider_names == ("finance_official_quote",)
    assert result.status == "needs_profiled_web_research"
    assert "finance_quote" in " ".join(result.warnings)
    assert "Official Exchange" in result.text


def test_finance_profile_ranking_adjusts_from_db_health(tmp_path):
    session_factory = _db(tmp_path)
    _add_research_provider(
        session_factory,
        provider_name="finance_quote_primary",
        source_name="Primary Quote Source",
        domain="stock",
        profile_needs="finance_quote",
        default_priority=5,
    )
    _add_research_provider(
        session_factory,
        provider_name="finance_quote_backup",
        source_name="Backup Quote Source",
        domain="stock",
        profile_needs="finance_quote",
        default_priority=20,
    )
    with session_factory() as session:
        ResearchProviderHealthRepository(session).record_timeout("finance_quote_primary", reason="timeout")

    profile = build_domain_research_profile(
        session_factory=session_factory,
        domain="stock",
        query="ACME stock price now",
    )

    assert profile.usable is True
    assert profile.provider_names[:2] == ("finance_quote_backup", "finance_quote_primary")


def test_finance_research_profile_is_distinct_from_live_quote(tmp_path):
    session_factory = _db(tmp_path)
    _add_research_provider(
        session_factory,
        provider_name="finance_filings_research",
        source_name="Issuer Filings",
        domain="stock",
        profile_needs="finance_research",
        source_type="official_filings",
    )

    result = WebEvidencePipeline(session_factory=session_factory).evaluate(
        query="ExampleCorp fundamentals filings and dividend research",
        locale="en",
    )

    assert result.status == "needs_profiled_web_research"
    assert "finance_research" in " ".join(result.warnings)
    assert "Issuer Filings" in result.text


def test_finance_listing_profile_is_distinct_from_research_and_quote(tmp_path):
    session_factory = _db(tmp_path)
    _add_research_provider(
        session_factory,
        provider_name="finance_exchange_listing",
        source_name="Exchange Listing Source",
        domain="stock",
        profile_needs="finance_listing",
        source_type="official_exchange",
    )
    _add_research_provider(
        session_factory,
        provider_name="finance_filings_research",
        source_name="Issuer Filings",
        domain="stock",
        profile_needs="finance_research",
        source_type="official_filings",
    )

    result = WebEvidencePipeline(session_factory=session_factory).evaluate(
        query="Ist SpaceX an der Börse?",
        locale="de",
    )

    assert result.status == "needs_profiled_web_research"
    assert "finance_listing" in " ".join(result.warnings)
    assert "finance_research" not in " ".join(result.warnings)
    assert "Exchange Listing Source" in result.text


def test_finance_listing_uses_generic_verified_web_research_profile_without_db_source(tmp_path):
    session_factory = _db(tmp_path)
    _add_research_provider(
        session_factory,
        provider_name="finance_filings_research",
        source_name="Issuer Filings",
        domain="stock",
        profile_needs="finance_research",
        source_type="official_filings",
    )

    for query in (
        "Ist SpaceX an der Börse?",
        "Ist Anthropic an der Börse?",
        "Ist Siemens an der Börse?",
        "Ist Adidas börsennotiert?",
        "Ist Quarvex Labs an der Börse?",
        "Ist AcmeBlubBla an der Börse?",
        "Ist FooBarBaz AG an der Börse?",
        "Kann man SpaceX Aktien kaufen?",
        "Kann man Anthropic Aktien kaufen?",
        "Kann man Siemens Aktien kaufen?",
        "Kann man Adidas Aktien kaufen?",
        "Kann man Quarvex Labs Aktien kaufen?",
        "Kann man AcmeBlubBla Aktien kaufen?",
        "Kann man FooBarBaz AG Aktien kaufen?",
        "Nasdaq Anthropic",
        "Nasdaq Quarvex Labs",
        "Nasdaq AcmeBlubBla",
        "NYSE FooBarBaz AG",
        "Ist Anthropic öffentlich gelistet?",
    ):
        result = WebEvidencePipeline(session_factory=session_factory).evaluate(query=query, locale="de")

        assert classify_evidence_domain(query) == "stock"
        assert result.status == "needs_profiled_web_research"
        assert "stock_domain_profile_builtin_source:finance_listing" in result.warnings
        assert "SEC company ticker data" in result.text


def test_finance_listing_fallback_does_not_use_finance_research_profile(tmp_path):
    session_factory = _db(tmp_path)
    _add_research_provider(
        session_factory,
        provider_name="finance_filings_research",
        source_name="Issuer Filings",
        domain="stock",
        profile_needs="finance_research",
        source_type="official_filings",
    )

    for query in (
        "Nasdaq Anthropic",
        "NYSE Anthropic",
    ):
        result = WebEvidencePipeline(session_factory=session_factory).evaluate(query=query, locale="de")

        assert result.status == "needs_profiled_web_research"
        assert "stock_domain_profile_builtin_source:finance_listing" in result.warnings
        assert "finance_research" not in " ".join(result.warnings)
        assert "Issuer Filings" not in result.text


def test_finance_quote_without_quote_profile_uses_generic_verified_web_research(tmp_path):
    session_factory = _db(tmp_path)
    _add_research_provider(
        session_factory,
        provider_name="finance_filings_research",
        source_name="Issuer Filings",
        domain="stock",
        profile_needs="finance_research",
        source_type="official_filings",
    )

    result = WebEvidencePipeline(session_factory=session_factory).evaluate(query="ACME stock price now", locale="en")

    assert result.status == "needs_profiled_web_research"
    assert "Need: finance_quote" in result.text
    assert "Exchange or current market data quote source" in result.text
    assert result.warnings == (
        "stock_domain_profile_builtin_source:finance_quote",
        "strategy:verified_quote_web_research",
    )


def test_finance_research_without_research_profile_uses_generic_checked_web_research(tmp_path):
    session_factory = _db(tmp_path)
    _add_research_provider(
        session_factory,
        provider_name="finance_quote_source",
        source_name="Quote Source",
        domain="stock",
        profile_needs="finance_quote",
    )

    result = WebEvidencePipeline(session_factory=session_factory).evaluate(
        query="ExampleCorp fundamentals filings and dividend research",
        locale="en",
    )

    assert result.status == "needs_profiled_web_research"
    assert "Need: finance_research" in result.text
    assert "Issuer investor relations or filings" in result.text
    assert result.warnings == (
        "stock_domain_profile_builtin_source:finance_research",
        "strategy:verified_finance_research_web",
    )


def test_ipo_url_routes_to_generic_listing_research_profile_not_unknown_entity(tmp_path):
    session_factory = _db(tmp_path)
    _add_research_provider(
        session_factory,
        provider_name="finance_quote_source",
        source_name="Quote Source",
        domain="stock",
        profile_needs="finance_quote",
    )
    url = (
        "https://www.reutersconnect.com/item/"
        "spacexs-initial-public-offering-ipo-at-the-nasdaq-marketsite-in-new-york-city/"
        "dGFnOnJldXRlcnMuY29tLDIwMjY6bmV3c21sX1JDMktTTEFSWE05Vw"
    )

    for query in (
        f"Quelle zur Frage, ob SpaceX an der Börse ist: {url}",
        f"Ist Anthropic an der Nasdaq? Quelle: {url}",
    ):
        result = WebEvidencePipeline(session_factory=session_factory).evaluate(query=query, locale="de")

        assert result.status == "needs_profiled_web_research"
        assert result.warnings == (
            "stock_domain_profile_builtin_source:finance_listing",
            "strategy:verified_listing_web_research",
        )


def test_derivative_exchange_queries_route_to_crypto_listing_research_not_stock_listing():
    for query in (
        "Was ist ACMEUSDT auf Bybit?",
        "Gibt es ExampleCo tokenized exposure auf Bybit?",
    ):
        result = WebEvidencePipeline().evaluate(query=query, locale="de")

        assert result.domain == "crypto"
        assert result.status == "needs_profiled_web_research"
        assert "Need: crypto_listing" in result.text
        assert result.warnings == (
            "crypto_domain_profile_builtin_source:crypto_listing",
            "strategy:verified_crypto_listing_web_research",
        )
        assert "finance_listing" not in " ".join(result.warnings)


def test_generic_crypto_queries_use_builtin_crypto_web_research_profile():
    for query in (
        "Was macht Solana?",
        "XRP price now",
        "BlorpCoin token price now",
    ):
        result = WebEvidencePipeline().evaluate(query=query, locale="de")

        assert result.domain == "crypto"
        assert result.status == "needs_profiled_web_research"
        assert "Need: crypto_quote" in result.text
        assert result.warnings == (
            "crypto_domain_profile_builtin_source:crypto_quote",
            "strategy:verified_crypto_quote_web_research",
        )


def test_finance_unknown_entity_fails_closed_without_guessing_ticker(tmp_path):
    session_factory = _db(tmp_path)
    _add_research_provider(
        session_factory,
        provider_name="finance_quote_source",
        source_name="Quote Source",
        domain="stock",
        profile_needs="finance_quote",
    )

    result = WebEvidencePipeline(session_factory=session_factory).evaluate(query="Was macht die Aktie jetzt?", locale="de")

    assert result.status == "unavailable"
    assert result.text == ""
    assert result.warnings == ("stock_entity_not_identified",)


def test_sport_profiles_cover_schedule_table_and_result_needs(tmp_path):
    session_factory = _db(tmp_path)
    _add_research_provider(
        session_factory,
        provider_name="sport_official_competition",
        source_name="Official Competition Site",
        domain="sports",
        profile_needs="sport_schedule,sport_table,sport_result",
    )

    pipeline = WebEvidencePipeline(session_factory=session_factory)
    schedule = pipeline.evaluate(query="WM Spielplan heute", locale="de")
    table = pipeline.evaluate(query="WM Gruppen Tabelle", locale="de")
    result = pipeline.evaluate(query="WM Ergebnis live", locale="de")

    assert schedule.status == table.status == result.status == "needs_profiled_web_research"
    assert "sport_schedule" in " ".join(schedule.warnings)
    assert "sport_table" in " ".join(table.warnings)
    assert "sport_result" in " ".join(result.warnings)
    assert "Official Competition Site" in schedule.text


def test_sport_world_cup_2026_german_hyphenated_group_stage_identifies_result_need(tmp_path):
    session_factory = _db(tmp_path)
    _add_research_provider(
        session_factory,
        provider_name="sport_official_competition",
        source_name="Official Competition Site",
        domain="sports",
        profile_needs="sport_schedule,sport_table,sport_result",
    )

    query = "Gegen wen hat Brasilien in der Vorrunde der WM 2026 schon gespielt und wie war das Ergebnis?"
    profile = build_domain_research_profile(session_factory=session_factory, domain="sports", query=query)
    result = WebEvidencePipeline(session_factory=session_factory).evaluate(query=query, locale="de")

    assert classify_evidence_domain(query) == "sports"
    assert profile.need == "sport_result"
    assert profile.usable is True
    assert result.status == "needs_profiled_web_research"
    assert "sports_competition_not_identified" not in result.warnings
    assert "sport_result" in " ".join(result.warnings)


def test_sport_query_registry_normalizes_competition_year_phase_and_need():
    cases = {
        "FIFA World Cup 2026 group stage results": {
            "competition": "world cup",
            "year": 2026,
            "phase": "group stage",
            "need": "sport_result",
        },
        "Euro 2024 standings": {
            "competition": "euro",
            "year": 2024,
            "phase": None,
            "need": "sport_table",
        },
    }

    for query, expected in cases.items():
        assert sports_query.query_terms(query) == expected
        assert sports_query.has_competition(query)
        assert sports_query.has_sports_signal(query)


def test_sport_query_registry_detects_canonical_team_names():
    cases = {
        "Brazil World Cup 2026 group stage result": "Brazil",
        "Germany Euro 2024 result": "Germany",
    }

    for query, expected_team in cases.items():
        assert sports_query.first_team(query) == expected_team


def test_sport_query_registry_detects_canonical_team_names_for_any_registered_team(monkeypatch):
    monkeypatch.setattr(
        sports_query,
        "TEAM_NAME_ALIASES",
        (
            *sports_query.TEAM_NAME_ALIASES,
            sports_query.SportsAlias(canonical="Los Angeles Lakers", aliases=("lakers",)),
        ),
    )

    assert sports_query.first_team("Los Angeles Lakers NBA result") == "Los Angeles Lakers"
    assert sports_query.first_team("Lakers NBA result") == "Los Angeles Lakers"


def test_sport_query_registry_returns_all_matching_teams():
    assert sports_query.matching_teams("Germany vs France Euro 2024 result") == ("Germany", "France")


def test_sport_profile_can_use_learned_reliable_source_observations(tmp_path):
    session_factory = _db(tmp_path)
    with session_factory() as session:
        repo = ResearchSourceObservationRepository(session)
        for _ in range(2):
            repo.record_observation(
                provider_name="websearch_provider",
                domain="sports",
                outcome="search_completed",
                confidence=0.8,
                source_hosts=("scores.example",),
            )

    profile = build_domain_research_profile(
        session_factory=session_factory,
        domain="sports",
        query="WM Gruppen Tabelle",
    )
    result = WebEvidencePipeline(session_factory=session_factory).evaluate(query="WM Gruppen Tabelle", locale="de")

    assert profile.usable is True
    assert profile.strategy == "learned_search_scrape_chain"
    assert profile.source_names == ("scores.example",)
    assert "learned_sources:scores.example" in profile.warnings
    assert result.status == "needs_profiled_web_research"
    assert "scores.example" in result.text


def test_sport_profile_ignores_weak_learned_source_observations(tmp_path):
    session_factory = _db(tmp_path)
    with session_factory() as session:
        repo = ResearchSourceObservationRepository(session)
        repo.record_observation(
            provider_name="websearch_provider",
            domain="sports",
            outcome="search_completed",
            confidence=0.8,
            source_hosts=("scores.example",),
        )
        repo.record_observation(
            provider_name="websearch_provider",
            domain="sports",
            outcome="empty_result",
            confidence=0.2,
            source_hosts=("scores.example",),
        )
        repo.record_observation(
            provider_name="websearch_provider",
            domain="sports",
            outcome="search_completed",
            confidence=0.7,
            source_hosts=("scores.example",),
            warning_codes=("single_source_host",),
        )

    result = WebEvidencePipeline(session_factory=session_factory).evaluate(query="WM Gruppen Tabelle", locale="de")

    assert result.status == "unavailable"
    assert result.warnings == ("sports_domain_profile_no_usable_source:sport_table",)


def test_sport_profile_ignores_conflicting_learned_source_observations(tmp_path):
    session_factory = _db(tmp_path)
    with session_factory() as session:
        repo = ResearchSourceObservationRepository(session)
        repo.record_observation(
            provider_name="websearch_provider",
            domain="sports",
            outcome="search_completed",
            confidence=0.8,
            source_hosts=("scores.example",),
        )
        repo.record_observation(
            provider_name="websearch_provider",
            domain="sports",
            outcome="search_completed",
            confidence=0.8,
            source_hosts=("scores.example",),
            warning_codes=("source_conflict",),
        )

    result = WebEvidencePipeline(session_factory=session_factory).evaluate(query="WM Gruppen Tabelle", locale="de")

    assert result.status == "unavailable"
    assert result.warnings == ("sports_domain_profile_no_usable_source:sport_table",)


def test_sport_unknown_competition_fails_closed(tmp_path):
    session_factory = _db(tmp_path)
    _add_research_provider(
        session_factory,
        provider_name="sport_table_source",
        source_name="Sport Table Source",
        domain="sports",
        profile_needs="sport_table",
    )

    result = WebEvidencePipeline(session_factory=session_factory).evaluate(query="Wie ist die Tabelle?", locale="de")

    assert result.status == "unavailable"
    assert result.text == ""
    assert result.warnings == ("sports_competition_not_identified",)


def test_domain_profile_source_unavailable_fails_closed_from_db_state(tmp_path):
    session_factory = _db(tmp_path)
    _add_research_provider(
        session_factory,
        provider_name="disabled_finance_quote",
        source_name="Disabled Finance Source",
        domain="stock",
        profile_needs="finance_quote",
        enabled=False,
    )

    with session_factory() as session:
        disabled = session.scalar(select(ResearchProvider).where(ResearchProvider.provider_name == "disabled_finance_quote"))
        assert disabled is not None and disabled.enabled is False

    result = WebEvidencePipeline(session_factory=session_factory).evaluate(query="ACME stock price now", locale="en")

    assert result.status == "needs_profiled_web_research"
    assert result.warnings == (
        "stock_domain_profile_builtin_source:finance_quote",
        "strategy:verified_quote_web_research",
    )


def test_autoresearch_stock_does_not_use_search_snippet_numbers(monkeypatch):
    monkeypatch.setattr("amo_bot.telegram.dispatcher.AIRouter.decide", lambda self, **kwargs: _allowing_router_decision())
    search = SimpleNamespace(
        allowed=True,
        decision="allow",
        reason="search_completed",
        text="ACME stock is 999 USD in search snippet",
        sources=("https://finance.example/acme",),
        hosts=("finance.example",),
        error=None,
    )
    d, sent = _mk_dispatcher(search)
    d.web_evidence_pipeline = WebEvidencePipeline()
    calls = []

    async def _ask(prompt: str) -> str:
        calls.append(prompt)
        return "normal ai"

    d.ai_service.ask = _ask
    asyncio.run(
        d._maybe_handle_ai_autoreply(
            message=_mk_message("@amo_bot Was macht die ExampleTech Aktie?", reply_to_is_bot=False, reply_to_user_is_bot=False, reply_to_username=""),
            role=Role.ADMIN,
            bot_username="amo_bot",
            from_parsed_update=True,
        )
    )

    assert [c.capability for c in d.webtool_dispatcher.calls] == ["websearch"]
    assert calls == []
    assert sent and "Aktienkurs" in sent[0]
    assert "999" not in sent[0]


def test_autoresearch_crypto_missing_provider_uses_websearch_but_not_snippet_price(monkeypatch):
    monkeypatch.setattr("amo_bot.telegram.dispatcher.AIRouter.decide", lambda self, **kwargs: _allowing_router_decision())
    crypto_unavailable = SimpleNamespace(
        allowed=False,
        decision="deny",
        reason="crypto_provider_not_configured",
        text="",
        sources=(),
        hosts=(),
        metadata={},
        error=None,
    )
    search = SimpleNamespace(
        allowed=True,
        decision="allow",
        reason="search_completed",
        text="BTC is 123456 USD in search snippet",
        sources=("https://crypto.example/btc",),
        hosts=("crypto.example",),
        error=None,
    )
    d, sent = _mk_sequence_dispatcher([crypto_unavailable, search])
    d.web_evidence_pipeline = WebEvidencePipeline()
    calls = []

    async def _ask(prompt: str) -> str:
        calls.append(prompt)
        return "normal ai"

    d.ai_service.ask = _ask
    asyncio.run(
        d._maybe_handle_ai_autoreply(
            message=_mk_message("@amo_bot BTC price now", reply_to_is_bot=False, reply_to_user_is_bot=False, reply_to_username=""),
            role=Role.ADMIN,
            bot_username="amo_bot",
            from_parsed_update=True,
        )
    )

    assert [c.capability for c in d.webtool_dispatcher.calls] == ["crypto_evidence", "websearch", "webscraping", "browser"]
    assert calls == []
    assert sent and "Krypto-Kurs" in sent[0]
    assert "123456" not in sent[0]


def test_autoresearch_crypto_uses_structured_evidence_before_search(monkeypatch):
    monkeypatch.setattr("amo_bot.telegram.dispatcher.AIRouter.decide", lambda self, **kwargs: _allowing_router_decision())
    evidence = SimpleNamespace(
        allowed=True,
        decision="allow",
        reason="crypto_evidence_completed",
        text="Asset: ethereum. Stand: 2026-06-11T10:00:00+00:00. Kurs: 3123 EUR.",
        sources=("https://api.binance.com/api/v3/ticker/24hr",),
        hosts=("api.binance.com",),
        metadata={"source_names": ("Binance",)},
        error=None,
    )
    d, sent = _mk_sequence_dispatcher([evidence])
    d.web_evidence_pipeline = WebEvidencePipeline()
    calls = []

    async def _ask(prompt: str) -> str:
        calls.append(prompt)
        return "normal ai"

    d.ai_service.ask = _ask
    asyncio.run(
        d._maybe_handle_ai_autoreply(
            message=_mk_message("@amo_bot ETH kurs jetzt", reply_to_is_bot=False, reply_to_user_is_bot=False, reply_to_username=""),
            role=Role.ADMIN,
            bot_username="amo_bot",
            from_parsed_update=True,
        )
    )

    assert len(d.webtool_dispatcher.calls) == 1
    assert d.webtool_dispatcher.calls[0].capability == "crypto_evidence"
    assert sent == ["normal ai"]
    assert calls and "DOMAIN EVIDENCE (STRUCTURED/FRESH)" in calls[0]
    assert "Übersetze oder verändere keine Quellennamen" in calls[0]
    assert "Teamnamen, Titel, Zahlen, Datumsangaben oder technischen Bezeichner" in calls[0]
    assert "übernimm sie im Original, wenn sie aus der Quelle stammen" in calls[0]
    assert "3123 EUR" in calls[0]
    assert "Binance: https://api.binance.com/api/v3/ticker/24hr" in calls[0]
    assert "ETH snippet 1 EUR" not in calls[0]


def test_autoresearch_sports_missing_profile_uses_websearch_but_not_snippet_table(monkeypatch):
    monkeypatch.setattr("amo_bot.telegram.dispatcher.AIRouter.decide", lambda self, **kwargs: _allowing_router_decision())
    search = SimpleNamespace(
        allowed=True,
        decision="allow",
        reason="search_completed",
        text="Group A: Team X 9 points",
        sources=("https://sports.example/wm",),
        hosts=("sports.example",),
        error=None,
    )
    followup_empty = SimpleNamespace(allowed=False, decision="deny", reason="empty_result", text="", sources=(), hosts=(), error=None)
    failed_scrape = SimpleNamespace(allowed=False, decision="deny", reason="empty_result", text="", sources=(), hosts=(), error=None)
    failed_browser = SimpleNamespace(
        allowed=False,
        decision="provider_unavailable",
        reason="browser_provider_not_configured",
        text="",
        sources=(),
        hosts=(),
        error="No browser",
    )
    d, sent = _mk_sequence_dispatcher([search, followup_empty, failed_scrape, failed_browser])
    d.web_evidence_pipeline = WebEvidencePipeline()
    calls = []

    async def _ask(prompt: str) -> str:
        calls.append(prompt)
        return "normal ai"

    d.ai_service.ask = _ask

    asyncio.run(
        d._maybe_handle_ai_autoreply(
            message=_mk_message("@amo_bot Wie stehen die Gruppen der Fußball WM?", reply_to_is_bot=False, reply_to_user_is_bot=False, reply_to_username=""),
            role=Role.ADMIN,
            bot_username="amo_bot",
            from_parsed_update=True,
        )
    )

    assert [c.capability for c in d.webtool_dispatcher.calls] == ["websearch", "websearch", "webscraping", "browser"]
    assert calls == []
    assert sent and "Tabelle oder den Stand" in sent[0]
    assert "Team X" not in sent[0]


def test_autoresearch_weather_structured_miss_falls_back_to_checked_websearch(monkeypatch):
    monkeypatch.setattr("amo_bot.telegram.dispatcher.AIRouter.decide", lambda self, **kwargs: _allowing_router_decision())
    weather_unavailable = SimpleNamespace(
        allowed=False,
        decision="deny",
        reason="weather_location_not_found",
        text="",
        sources=(),
        hosts=(),
        metadata={},
        error=None,
    )
    search = SimpleNamespace(
        allowed=True,
        decision="allow",
        reason="search_completed",
        text="Search result says Met Service forecast for Exampletown is 18 C and rain likely.",
        sources=("https://weather.example/forecast/exampletown",),
        hosts=("weather.example",),
        error=None,
    )
    scrape = SimpleNamespace(
        allowed=True,
        decision="allow",
        reason="scrape_completed",
        text=(
            "Exampletown forecast published today by Weather Example. Current conditions and forecast: "
            "temperature 18 C, rain likely this afternoon, wind moderate. Updated 2026-06-18 09:00 UTC."
        ),
        sources=("https://weather.example/forecast/exampletown",),
        hosts=("weather.example",),
        error=None,
    )
    d, sent = _mk_sequence_dispatcher([weather_unavailable, search, scrape])
    d.web_evidence_pipeline = WebEvidencePipeline()
    calls = []

    async def _ask(prompt: str) -> str:
        calls.append(prompt)
        return "normal ai"

    d.ai_service.ask = _ask
    asyncio.run(
        d._maybe_handle_ai_autoreply(
            message=_mk_message("@amo_bot Wetter morgen in Exampletown", reply_to_is_bot=False, reply_to_user_is_bot=False, reply_to_username=""),
            role=Role.ADMIN,
            bot_username="amo_bot",
            from_parsed_update=True,
        )
    )

    assert [c.capability for c in d.webtool_dispatcher.calls] == ["weather_evidence", "websearch", "webscraping"]
    assert sent == ["normal ai"]
    assert calls and "AUTO-RESEARCH (LIVE WEB + SOURCE CHECK)" in calls[0]
    assert "temperature 18 C" in calls[0]


def test_autoresearch_generic_status_release_and_availability_search_first(monkeypatch):
    monkeypatch.setattr("amo_bot.telegram.dispatcher.AIRouter.decide", lambda self, **kwargs: _allowing_router_decision())
    prompts = [
        "@amo_bot Ist GitHub Actions gerade down?",
        "@amo_bot Ist die aktuelle FastAPI Version laut offiziellen Release Notes draußen?",
        "@amo_bot Ist die Playstation Portal heute bei Saturn lieferbar?",
    ]
    for prompt in prompts:
        search = SimpleNamespace(
            allowed=True,
            decision="allow",
            reason="search_completed",
            text="Current checked web result summary points to an official current source.",
            sources=("https://official.example/status",),
            hosts=("official.example",),
            error=None,
        )
        followup = SimpleNamespace(
            allowed=True,
            decision="allow",
            reason="search_completed",
            text="Follow-up result keeps the same official current source as the best candidate.",
            sources=("https://official.example/status",),
            hosts=("official.example",),
            error=None,
        )
        scrape = SimpleNamespace(
            allowed=True,
            decision="allow",
            reason="scrape_completed",
            text=(
                "Official current source page updated today. It contains the requested status, release, "
                "or availability details with a current timestamp and stable confirmation."
            ),
            sources=("https://official.example/status",),
            hosts=("official.example",),
            error=None,
        )
        d, sent = _mk_sequence_dispatcher([search, followup, scrape])
        calls = []

        async def _ask(prompt_text: str) -> str:
            calls.append(prompt_text)
            return "normal ai"

        d.ai_service.ask = _ask
        asyncio.run(
            d._maybe_handle_ai_autoreply(
                message=_mk_message(prompt, reply_to_is_bot=False, reply_to_user_is_bot=False, reply_to_username=""),
                role=Role.ADMIN,
                bot_username="amo_bot",
                from_parsed_update=True,
            )
        )

        assert [c.capability for c in d.webtool_dispatcher.calls] == ["websearch", "websearch", "webscraping"], prompt
        assert sent == ["normal ai"]
        assert calls and "AUTO-RESEARCH (LIVE WEB + SOURCE CHECK)" in calls[0]


def test_autoresearch_news_accepts_checked_official_primary_source(monkeypatch):
    monkeypatch.setattr("amo_bot.telegram.dispatcher.AIRouter.decide", lambda self, **kwargs: _allowing_router_decision())
    search = SimpleNamespace(
        allowed=True,
        decision="allow",
        reason="search_completed",
        text="Official press release says Acme launched the Example Program today.",
        sources=("https://press.example.gov/acme/example-program",),
        hosts=("press.example.gov",),
        error=None,
    )
    followup = SimpleNamespace(
        allowed=True,
        decision="allow",
        reason="search_completed",
        text="Official source result confirms the Acme Example Program press release.",
        sources=("https://press.example.gov/acme/example-program",),
        hosts=("press.example.gov",),
        error=None,
    )
    scrape = SimpleNamespace(
        allowed=True,
        decision="allow",
        reason="scrape_completed",
        text=(
            "2026-06-18 Official press release from the Example Government press office: "
            "Acme launched the Example Program today after approval by the responsible agency, "
            "with implementation beginning immediately and further official updates to follow."
        ),
        sources=("https://press.example.gov/acme/example-program",),
        hosts=("press.example.gov",),
        error=None,
    )
    d, sent = _mk_sequence_dispatcher([search, followup, scrape])
    calls = []

    async def _ask(prompt: str) -> str:
        calls.append(prompt)
        return "normal ai"

    d.ai_service.ask = _ask
    asyncio.run(
        d._maybe_handle_ai_autoreply(
            message=_mk_message("@amo_bot latest news Acme Example Program", reply_to_is_bot=False, reply_to_user_is_bot=False, reply_to_username=""),
            role=Role.ADMIN,
            bot_username="amo_bot",
            from_parsed_update=True,
        )
    )

    assert [c.capability for c in d.webtool_dispatcher.calls] == ["websearch", "websearch", "webscraping"]
    assert sent == ["normal ai"]
    assert calls and "Official press release" in calls[0]
    assert "nicht aus mehreren geprüften Quellen bestätigen" not in sent[0]
