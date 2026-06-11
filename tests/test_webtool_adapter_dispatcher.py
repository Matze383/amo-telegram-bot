"""Tests for WebtoolProviderAdapter and WebtoolCapabilityDispatcher (Issue #48).

Tests cover:
- RealWebsearchProviderAdapter / RealWebscrapeProviderAdapter
- create_webtool_subagent_service fail-closed by default (no fake in production)
- WebtoolCapabilityDispatcher enforces quota before provider call
- disabled/quota_exceeded → provider NOT called
- dispatcher sanitizes and maps coreplugin results
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

from amo_bot.auth.roles import Role
from amo_bot.db.base import create_session_factory
from amo_bot.db.init_db import init_db
from amo_bot.ai.webtool_subagent import (
    create_webtool_subagent_service,
    WebtoolSubagentRequest,
    WebtoolOperationType,
)
from amo_bot.ai.webtool_dispatcher import (
    WebtoolCapabilityDispatcher,
    WebtoolCapabilityRequest,
)


@pytest.fixture
def db_url(tmp_path):
    url = f"sqlite:///{tmp_path / 'webtool_adapter_dispatcher.db'}"
    init_db(url)
    return url


@pytest.fixture
def session_factory(db_url):
    return create_session_factory(db_url)


class TestCreateWebtoolSubagentServiceFailClosed:
    """Factory defaults to fail-closed: no real providers → all requests denied."""

    def test_no_providers_no_fake_deny(self, session_factory):
        """Without use_fake_providers or explicit providers, service denies all."""
        from amo_bot.db.repositories import WebToolRoleQuotaRepository

        with session_factory() as s:
            quota_repo = WebToolRoleQuotaRepository(s)
            service = create_webtool_subagent_service(quota_repo)

        request = WebtoolSubagentRequest(
            operation_type=WebtoolOperationType.WEBSEARCH,
            user_id=42,
            role=Role.OWNER,
            chat_id=-100,
            topic_id=None,
            day="2026-05-29",
            query="test",
        )

        result = service.execute(request)

        assert result.allowed is False
        assert result.decision == "provider_unavailable"
        assert result.reason == "search_provider_not_configured"

    def test_explicit_real_providers_not_replaced_by_fake(self, session_factory):
        """Explicit provider args take precedence over use_fake_providers."""
        from amo_bot.db.repositories import WebToolRoleQuotaRepository

        class RealSearchProvider:
            def search(self, *, query: str, locale: str, max_results: int):
                return [{"title": "Real result", "url": "https://real.com", "snippet": "Real snippet"}]

        with session_factory() as s:
            quota_repo = WebToolRoleQuotaRepository(s)
            # Even with use_fake_providers=True, explicit provider wins
            service = create_webtool_subagent_service(
                quota_repo,
                use_fake_providers=True,
                search_provider=RealSearchProvider(),
            )

        request = WebtoolSubagentRequest(
            operation_type=WebtoolOperationType.WEBSEARCH,
            user_id=42,
            role=Role.OWNER,
            chat_id=-100,
            topic_id=None,
            day="2026-05-29",
            query="test",
        )

        result = service.execute(request)

        assert result.allowed is True
        assert result.sanitized.text != ""
        assert "Real result" in result.sanitized.text

    def test_use_fake_providers_creates_working_service(self, session_factory):
        """use_fake_providers=True creates functional service (tests only)."""
        from amo_bot.db.repositories import WebToolRoleQuotaRepository

        with session_factory() as s:
            quota_repo = WebToolRoleQuotaRepository(s)
            service = create_webtool_subagent_service(quota_repo, use_fake_providers=True)

        request = WebtoolSubagentRequest(
            operation_type=WebtoolOperationType.WEBSEARCH,
            user_id=42,
            role=Role.OWNER,
            chat_id=-100,
            topic_id=None,
            day="2026-05-29",
            query="test",
        )

        result = service.execute(request)

        assert result.allowed is True
        assert result.decision == "allow"


class TestWebtoolCapabilityDispatcherQuotaFirst:
    """Dispatcher enforces quota before any provider call."""

    def test_dispatcher_enforces_quota_before_provider_call(self, session_factory):
        """Quota exceeded → provider never called (verified by mock tracking)."""
        from amo_bot.db.repositories import WebToolRoleQuotaRepository

        provider_calls = []

        class TrackingSearchProvider:
            def search(self, *, query: str, locale: str, max_results: int):
                provider_calls.append(("search", query))
                return [{"title": "result", "url": "https://example.com", "snippet": "test"}]

        with session_factory() as s:
            quota_repo = WebToolRoleQuotaRepository(s)
            # Set NORMAL role to limited=1
            quota_repo.upsert_role_quota(role=Role.NORMAL, mode="limited", daily_limit=1)

            service = create_webtool_subagent_service(
                quota_repo,
                search_provider=TrackingSearchProvider(),
            )
            dispatcher = WebtoolCapabilityDispatcher(quota_repo, service=service)

        # Exhaust quota
        r1 = dispatcher.execute(
            WebtoolCapabilityRequest(
                capability="websearch",
                user_id=42,
                role=Role.NORMAL,
                chat_id=-100,
                query="first query",
            )
        )
        assert r1.allowed is True
        assert provider_calls == [("search", "first query")]

        # Second request should be denied by quota before provider is called
        provider_calls.clear()
        r2 = dispatcher.execute(
            WebtoolCapabilityRequest(
                capability="websearch",
                user_id=42,
                role=Role.NORMAL,
                chat_id=-100,
                query="second query",
            )
        )
        assert r2.allowed is False
        assert r2.decision == "quota_exceeded"
        assert provider_calls == []  # Provider was NOT called

    def test_disabled_role_denies_before_provider_call(self, session_factory):
        """Disabled role → provider never called."""
        from amo_bot.db.repositories import WebToolRoleQuotaRepository

        provider_calls = []

        class TrackingSearchProvider:
            def search(self, *, query: str, locale: str, max_results: int):
                provider_calls.append(("search", query))
                return [{"title": "result", "url": "https://example.com", "snippet": "test"}]

        with session_factory() as s:
            quota_repo = WebToolRoleQuotaRepository(s)
            service = create_webtool_subagent_service(
                quota_repo,
                search_provider=TrackingSearchProvider(),
            )
            dispatcher = WebtoolCapabilityDispatcher(quota_repo, service=service)

        provider_calls.clear()
        result = dispatcher.execute(
            WebtoolCapabilityRequest(
                capability="websearch",
                user_id=42,
                role=Role.IGNORE,
                chat_id=-100,
                query="test",
            )
        )

        assert result.allowed is False
        assert result.decision == "disabled"
        assert provider_calls == []  # Provider never called

    def test_weather_evidence_quota_exceeded_denies_before_provider_call(self, session_factory):
        """Structured weather evidence uses the same quota-first path."""
        from amo_bot.db.repositories import WebToolRoleQuotaRepository
        from amo_bot.telegram.webtool_evidence import DomainEvidenceResult, EvidenceSource

        provider_calls = []

        class TrackingWeatherProvider:
            def get_weather(self, *, query: str, locale: str):
                provider_calls.append(("weather", query, locale))
                return DomainEvidenceResult(
                    domain="weather",
                    status="confirmed",
                    confidence=0.9,
                    text="Ort: Berlin. Stand: 2026-06-11T10:00. Aktuell: Temperatur 21 °C.",
                    sources=(EvidenceSource("Open-Meteo", "https://api.open-meteo.com/v1/forecast", "2026-06-11T10:00:00+00:00"),),
                )

        with session_factory() as s:
            quota_repo = WebToolRoleQuotaRepository(s)
            quota_repo.upsert_role_quota(role=Role.NORMAL, mode="limited", daily_limit=1)
            service = create_webtool_subagent_service(
                quota_repo,
                weather_evidence_provider=TrackingWeatherProvider(),
            )
            dispatcher = WebtoolCapabilityDispatcher(quota_repo, service=service)

        first = dispatcher.execute(
            WebtoolCapabilityRequest(
                capability="weather_evidence",
                user_id=42,
                role=Role.NORMAL,
                chat_id=-100,
                query="Wetter in Berlin",
                locale="de",
            )
        )
        assert first.allowed is True
        assert provider_calls == [("weather", "Wetter in Berlin", "de")]

        provider_calls.clear()
        second = dispatcher.execute(
            WebtoolCapabilityRequest(
                capability="weather_evidence",
                user_id=42,
                role=Role.NORMAL,
                chat_id=-100,
                query="Wetter in Hamburg",
                locale="de",
            )
        )

        assert second.allowed is False
        assert second.decision == "quota_exceeded"
        assert provider_calls == []

    def test_crypto_evidence_disabled_role_denies_before_provider_call(self, session_factory):
        """Structured crypto evidence provider is not called when quota policy denies."""
        from amo_bot.db.repositories import WebToolRoleQuotaRepository

        provider_calls = []

        class TrackingCryptoProvider:
            def get_crypto(self, *, query: str, locale: str):
                provider_calls.append(("crypto", query, locale))
                raise AssertionError("provider must not be called")

        with session_factory() as s:
            quota_repo = WebToolRoleQuotaRepository(s)
            service = create_webtool_subagent_service(
                quota_repo,
                crypto_evidence_provider=TrackingCryptoProvider(),
            )
            dispatcher = WebtoolCapabilityDispatcher(quota_repo, service=service)

        result = dispatcher.execute(
            WebtoolCapabilityRequest(
                capability="crypto_evidence",
                user_id=42,
                role=Role.IGNORE,
                chat_id=-100,
                query="ETH kurs jetzt",
                locale="de",
            )
        )

        assert result.allowed is False
        assert result.decision == "disabled"
        assert provider_calls == []

    def test_crypto_evidence_quota_exceeded_denies_before_provider_call(self, session_factory):
        """Structured crypto evidence uses quota counters before provider invocation."""
        from amo_bot.db.repositories import WebToolRoleQuotaRepository
        from amo_bot.telegram.webtool_evidence import DomainEvidenceResult, EvidenceSource

        provider_calls = []

        class TrackingCryptoProvider:
            def get_crypto(self, *, query: str, locale: str):
                provider_calls.append(("crypto", query, locale))
                return DomainEvidenceResult(
                    domain="crypto",
                    status="confirmed",
                    confidence=0.9,
                    text="Asset: ethereum. Stand: 2026-06-11T10:00:00+00:00. Kurs: 3123 EUR.",
                    sources=(EvidenceSource("CoinGecko", "https://api.coingecko.com/api/v3/simple/price", "2026-06-11T10:00:01+00:00"),),
                )

        with session_factory() as s:
            quota_repo = WebToolRoleQuotaRepository(s)
            quota_repo.upsert_role_quota(role=Role.NORMAL, mode="limited", daily_limit=1)
            service = create_webtool_subagent_service(
                quota_repo,
                crypto_evidence_provider=TrackingCryptoProvider(),
            )
            dispatcher = WebtoolCapabilityDispatcher(quota_repo, service=service)

        first = dispatcher.execute(
            WebtoolCapabilityRequest(
                capability="crypto_evidence",
                user_id=42,
                role=Role.NORMAL,
                chat_id=-100,
                query="ETH kurs jetzt",
                locale="de",
            )
        )
        assert first.allowed is True
        assert provider_calls == [("crypto", "ETH kurs jetzt", "de")]

        provider_calls.clear()
        second = dispatcher.execute(
            WebtoolCapabilityRequest(
                capability="crypto_evidence",
                user_id=42,
                role=Role.NORMAL,
                chat_id=-100,
                query="BTC kurs jetzt",
                locale="de",
            )
        )

        assert second.allowed is False
        assert second.decision == "quota_exceeded"
        assert provider_calls == []

    def test_weather_evidence_success_is_quota_and_audit_relevant(self, session_factory):
        """Successful structured weather evidence increments quota and writes metadata-only audit."""
        from amo_bot.db.models import WebToolAuditEvent
        from amo_bot.db.repositories import WebToolRoleQuotaRepository
        from amo_bot.telegram.webtool_evidence import DomainEvidenceResult, EvidenceSource

        class WeatherProvider:
            def get_weather(self, *, query: str, locale: str):
                return DomainEvidenceResult(
                    domain="weather",
                    status="confirmed",
                    confidence=0.9,
                    text="Ort: Berlin. Stand: 2026-06-11T10:00. Aktuell: Temperatur 21 °C.",
                    sources=(EvidenceSource("Open-Meteo", "https://api.open-meteo.com/v1/forecast", "2026-06-11T10:00:01+00:00"),),
                    warnings=("weather_code_requires_interpretation",),
                )

        with session_factory() as s:
            quota_repo = WebToolRoleQuotaRepository(s)
            quota_repo.upsert_role_quota(role=Role.NORMAL, mode="limited", daily_limit=2)
            service = create_webtool_subagent_service(quota_repo, weather_evidence_provider=WeatherProvider())
            dispatcher = WebtoolCapabilityDispatcher(quota_repo, service=service)

            result = dispatcher.execute(
                WebtoolCapabilityRequest(
                    capability="weather_evidence",
                    user_id=42,
                    role=Role.NORMAL,
                    chat_id=-100,
                    query="SECRET_QUERY Wetter in Berlin API_KEY=sk-test",
                    locale="de",
                )
            )

            assert result.allowed is True
            assert result.result_type == "weather_evidence"
            assert result.metadata["operation"] == "weather_evidence"
            assert result.metadata["source_names"] == ("Open-Meteo",)
            metadata_values = str(list(result.metadata.values()))
            assert "SECRET_QUERY" not in metadata_values
            assert "API_KEY" not in metadata_values
            assert "sk-test" not in metadata_values

            events = s.query(WebToolAuditEvent).order_by(WebToolAuditEvent.id.asc()).all()
            assert events[-1].operation_type == "weather_evidence"
            assert events[-1].decision == "allow"
            assert events[-1].count == 1

    def test_crypto_evidence_success_is_quota_and_audit_relevant(self, session_factory):
        """Successful structured crypto evidence increments quota and writes metadata-only audit."""
        from amo_bot.db.models import WebToolAuditEvent
        from amo_bot.db.repositories import WebToolRoleQuotaRepository
        from amo_bot.telegram.webtool_evidence import DomainEvidenceResult, EvidenceSource

        class CryptoProvider:
            def get_crypto(self, *, query: str, locale: str):
                return DomainEvidenceResult(
                    domain="crypto",
                    status="confirmed",
                    confidence=0.9,
                    text="Asset: ethereum. Stand: 2026-06-11T10:00:00+00:00. Kurs: 3123 EUR.",
                    sources=(EvidenceSource("CoinGecko", "https://api.coingecko.com/api/v3/simple/price", "2026-06-11T10:00:01+00:00"),),
                    warnings=("market_prices_are_volatile",),
                )

        with session_factory() as s:
            quota_repo = WebToolRoleQuotaRepository(s)
            quota_repo.upsert_role_quota(role=Role.NORMAL, mode="limited", daily_limit=2)
            service = create_webtool_subagent_service(quota_repo, crypto_evidence_provider=CryptoProvider())
            dispatcher = WebtoolCapabilityDispatcher(quota_repo, service=service)

            result = dispatcher.execute(
                WebtoolCapabilityRequest(
                    capability="crypto_evidence",
                    user_id=42,
                    role=Role.NORMAL,
                    chat_id=-100,
                    query="SECRET_QUERY ETH kurs jetzt API_KEY=sk-test",
                    locale="de",
                )
            )

            assert result.allowed is True
            assert result.result_type == "crypto_evidence"
            assert result.metadata["operation"] == "crypto_evidence"
            assert result.metadata["source_names"] == ("CoinGecko",)
            metadata_values = str(list(result.metadata.values()))
            assert "SECRET_QUERY" not in metadata_values
            assert "API_KEY" not in metadata_values
            assert "sk-test" not in metadata_values

            events = s.query(WebToolAuditEvent).order_by(WebToolAuditEvent.id.asc()).all()
            assert events[-1].operation_type == "crypto_evidence"
            assert events[-1].decision == "allow"
            assert events[-1].count == 1

    def test_dispatcher_maps_result_correctly(self, session_factory):
        """Successful dispatch maps text/sources/hosts/result_type correctly."""
        from amo_bot.db.repositories import WebToolRoleQuotaRepository

        with session_factory() as s:
            quota_repo = WebToolRoleQuotaRepository(s)
            service = create_webtool_subagent_service(quota_repo, use_fake_providers=True)
            dispatcher = WebtoolCapabilityDispatcher(quota_repo, service=service)

        result = dispatcher.execute(
            WebtoolCapabilityRequest(
                capability="websearch",
                user_id=42,
                role=Role.OWNER,
                chat_id=-100,
                query="python",
                max_results=3,
            )
        )

        assert result.allowed is True
        assert result.decision == "allow"
        assert result.text != ""
        assert len(result.sources) > 0
        assert len(result.hosts) > 0
        assert result.result_type == "websearch_summary"
        assert result.metadata["role"] == "owner"
        assert result.metadata["user_id"] == 42
        # Metadata-only: no query in metadata
        assert "query" not in result.metadata

    def test_dispatcher_webscraping_operation(self, session_factory):
        """Dispatcher handles webscraping operations with quota check."""
        from amo_bot.db.repositories import WebToolRoleQuotaRepository

        with session_factory() as s:
            quota_repo = WebToolRoleQuotaRepository(s)
            service = create_webtool_subagent_service(quota_repo, use_fake_providers=True)
            dispatcher = WebtoolCapabilityDispatcher(quota_repo, service=service)

        result = dispatcher.execute(
            WebtoolCapabilityRequest(
                capability="webscraping",
                user_id=42,
                role=Role.VIP,
                chat_id=-100,
                topic_id=5,
                url="https://example.com/page",
            )
        )

        assert result.allowed is True
        assert result.decision == "allow"
        assert result.result_type == "webscraping_text"

    def test_dispatcher_metadata_no_secrets(self, session_factory):
        """Dispatcher metadata contains no query, URL, or secret content."""
        from amo_bot.db.repositories import WebToolRoleQuotaRepository

        with session_factory() as s:
            quota_repo = WebToolRoleQuotaRepository(s)
            service = create_webtool_subagent_service(quota_repo, use_fake_providers=True)
            dispatcher = WebtoolCapabilityDispatcher(quota_repo, service=service)

        result = dispatcher.execute(
            WebtoolCapabilityRequest(
                capability="websearch",
                user_id=42,
                role=Role.OWNER,
                chat_id=-100,
                query="SECRET_QUERY API_KEY=sk-abcdef",
            )
        )

        metadata_str = str(list(result.metadata.values()))
        assert "SECRET_QUERY" not in metadata_str
        assert "API_KEY" not in metadata_str
        assert "sk-abcdef" not in metadata_str

    def test_dispatcher_unknown_capability_fails_closed(self, session_factory):
        """Unknown capability returns fail-closed result."""
        from amo_bot.db.repositories import WebToolRoleQuotaRepository

        with session_factory() as s:
            quota_repo = WebToolRoleQuotaRepository(s)
            service = create_webtool_subagent_service(quota_repo, use_fake_providers=True)
            dispatcher = WebtoolCapabilityDispatcher(quota_repo, service=service)

        result = dispatcher.execute(
            WebtoolCapabilityRequest(
                capability="unknown_tool",
                user_id=42,
                role=Role.OWNER,
                chat_id=-100,
            )
        )

        assert result.allowed is False
        assert result.decision == "deny"


class TestWebtoolCapabilityDispatcherQuotaScoping:
    """Quota scoping (per user, chat, day) in dispatcher context."""

    def test_different_chat_different_quota(self, session_factory):
        """Different chat IDs have independent quota counters."""
        from amo_bot.db.repositories import WebToolRoleQuotaRepository

        with session_factory() as s:
            quota_repo = WebToolRoleQuotaRepository(s)
            quota_repo.upsert_role_quota(role=Role.NORMAL, mode="limited", daily_limit=1)

            service = create_webtool_subagent_service(quota_repo, use_fake_providers=True)
            dispatcher = WebtoolCapabilityDispatcher(quota_repo, service=service)

        # Chat -100: exhaust quota
        r1 = dispatcher.execute(
            WebtoolCapabilityRequest(capability="websearch", user_id=42, role=Role.NORMAL, chat_id=-100, query="test")
        )
        assert r1.allowed is True

        # Chat -200: independent counter, still allowed
        r2 = dispatcher.execute(
            WebtoolCapabilityRequest(capability="websearch", user_id=42, role=Role.NORMAL, chat_id=-200, query="test")
        )
        assert r2.allowed is True
