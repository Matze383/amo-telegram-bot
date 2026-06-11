from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx

from amo_bot.auth.roles import Role
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
    assert classify_evidence_domain("Was macht die Nvidia Aktie?") == "stock"
    assert classify_evidence_domain("ETH kurs jetzt") == "crypto"
    assert classify_evidence_domain("Wie ist das Wetter morgen in Berlin?") == "weather"
    assert classify_evidence_domain("aktuelle News zu OpenAI") == "news"
    assert classify_evidence_domain("Erklär mir Python decorators") == "generic"


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


def test_pipeline_fail_closed_for_stock_and_sports_without_structured_provider():
    pipeline = WebEvidencePipeline()

    stock = pipeline.evaluate(query="Was macht die Nvidia Aktie?", locale="de")
    sports = pipeline.evaluate(query="Wie stehen die Gruppen der Fußball WM?", locale="de")

    assert stock.status == "unavailable"
    assert stock.warnings == ("stock_structured_provider_not_configured",)
    assert sports.status == "unavailable"
    assert sports.warnings == ("sports_structured_provider_not_configured",)


def test_autoresearch_stock_does_not_use_search_snippet_numbers(monkeypatch):
    monkeypatch.setattr("amo_bot.telegram.dispatcher.AIRouter.decide", lambda self, **kwargs: _allowing_router_decision())
    search = SimpleNamespace(
        allowed=True,
        decision="allow",
        reason="search_completed",
        text="Nvidia stock is 999 USD in search snippet",
        sources=("https://finance.example/nvda",),
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
            message=_mk_message("@amo_bot Was macht die Nvidia Aktie?", reply_to_is_bot=False, reply_to_user_is_bot=False, reply_to_username=""),
            role=Role.ADMIN,
            bot_username="amo_bot",
            from_parsed_update=True,
        )
    )

    assert d.webtool_dispatcher.calls == []
    assert calls == []
    assert sent and "Aktienkurs" in sent[0]
    assert "999" not in sent[0]


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
    assert "3123 EUR" in calls[0]
    assert "Binance: https://api.binance.com/api/v3/ticker/24hr" in calls[0]
    assert "ETH snippet 1 EUR" not in calls[0]


def test_autoresearch_sports_does_not_use_search_snippet_table(monkeypatch):
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
    d, sent = _mk_dispatcher(search)
    d.web_evidence_pipeline = WebEvidencePipeline()

    asyncio.run(
        d._maybe_handle_ai_autoreply(
            message=_mk_message("@amo_bot Wie stehen die Gruppen der Fußball WM?", reply_to_is_bot=False, reply_to_user_is_bot=False, reply_to_username=""),
            role=Role.ADMIN,
            bot_username="amo_bot",
            from_parsed_update=True,
        )
    )

    assert d.webtool_dispatcher.calls == []
    assert sent and "Tabelle oder den Stand" in sent[0]
    assert "Team X" not in sent[0]
