from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from amo_bot.auth.roles import Role
from amo_bot.db.base import create_session_factory
from amo_bot.db.init_db import init_db
from amo_bot.db.models import (
    ResearchProvider,
    ResearchProviderHealth,
    WebToolAuditEvent,
)
from amo_bot.db.repositories import ResearchProviderHealthRepository, WebToolRoleQuotaRepository
from amo_bot.telegram.webtool_evidence import (
    BinanceTickerEvidenceProvider,
    CoinGeckoEvidenceProvider,
    DbBackedProviderHealthRegistry,
    DomainEvidenceResult,
    EvidenceProviderCandidate,
    EvidenceSource,
    PROVIDER_REGISTRY,
    ProviderDefinition,
    ResilientCryptoEvidenceProvider,
    build_evidence_candidates_from_db,
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

    def get(self, url, *, params, timeout):
        return _Response(self.payloads.pop(0))


class _CryptoProvider:
    def __init__(self, result: DomainEvidenceResult):
        self.result = result
        self.calls = 0

    def get_crypto(self, *, query: str, locale: str):
        self.calls += 1
        return self.result


def _fresh_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _db(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'research_provider_health.sqlite3'}"
    init_db(database_url)
    return create_session_factory(database_url)


def test_research_provider_tables_exist_and_seed_provider_models(tmp_path):
    session_factory = _db(tmp_path)
    engine = session_factory.kw["bind"]

    tables = set(inspect(engine).get_table_names())

    assert {
        "research_providers",
        "research_provider_health",
        "research_source_observations",
        "research_eval_cases",
    }.issubset(tables)
    with session_factory() as session:
        provider = session.scalar(
            select(ResearchProvider).where(ResearchProvider.provider_name == "open_meteo_weather")
        )
        assert provider is not None
        assert provider.domain == "weather"
        assert provider.source_name == "Open-Meteo"


def test_db_provider_registry_filters_disabled_providers_and_preserves_metadata(tmp_path):
    session_factory = _db(tmp_path)
    with session_factory() as session:
        coingecko = session.scalar(
            select(ResearchProvider).where(ResearchProvider.provider_name == "coingecko_crypto")
        )
        assert coingecko is not None
        coingecko.enabled = False
        binance = session.scalar(
            select(ResearchProvider).where(ResearchProvider.provider_name == "binance_crypto")
        )
        assert binance is not None
        binance.default_priority = 5
        binance.min_confidence = 0.82
        session.commit()

    candidates = build_evidence_candidates_from_db(
        session_factory=session_factory,
        domain="crypto",
        providers={
            "coingecko_crypto": CoinGeckoEvidenceProvider(client=_FakeClient([])),
            "binance_crypto": BinanceTickerEvidenceProvider(client=_FakeClient([])),
        },
    )

    assert [candidate.definition.name for candidate in candidates] == ["binance_crypto"]
    assert candidates[0].definition.default_priority == 5
    assert candidates[0].definition.min_confidence == 0.82


def test_disabled_db_providers_do_not_fall_back_to_default_candidates(tmp_path):
    session_factory = _db(tmp_path)
    with session_factory() as session:
        providers = session.scalars(select(ResearchProvider).where(ResearchProvider.domain == "crypto")).all()
        assert providers
        for provider in providers:
            provider.enabled = False
        session.commit()

    candidates = build_evidence_candidates_from_db(
        session_factory=session_factory,
        domain="crypto",
        providers={
            "coingecko_crypto": CoinGeckoEvidenceProvider(client=_FakeClient([])),
            "binance_crypto": BinanceTickerEvidenceProvider(client=_FakeClient([])),
        },
    )
    result = ResilientCryptoEvidenceProvider(candidates).get_crypto(query="btc price", locale="en")

    assert candidates == ()
    assert result.status == "unavailable"
    assert result.warnings == ("crypto_provider_not_configured",)


def test_research_provider_health_repository_records_and_loads_outcomes(tmp_path):
    session_factory = _db(tmp_path)

    with session_factory() as session:
        repo = ResearchProviderHealthRepository(session)
        repo.record_success("coingecko_crypto")
        repo.record_failure("coingecko_crypto", reason="bad_payload")
        repo.record_timeout("coingecko_crypto", reason="timeout")
        repo.record_rate_limit("coingecko_crypto", reason="429 rate limit")

    with session_factory() as session:
        health = ResearchProviderHealthRepository(session).load_provider_health("coingecko_crypto")

    assert health.success_count == 1
    assert health.failure_count == 3
    assert health.timeout_count == 1
    assert health.rate_limit_count == 1
    assert health.last_error == "429 rate limit"


def test_research_provider_health_repository_recovers_from_create_race(tmp_path):
    session_factory = _db(tmp_path)
    race_triggered = False

    with session_factory() as session:
        original_flush = session.flush

        def racing_flush(*args, **kwargs):
            nonlocal race_triggered
            if not race_triggered:
                race_triggered = True
                with session_factory() as racing_session:
                    racing_session.add(ResearchProviderHealth(provider_name="race_provider", failure_count=2))
                    racing_session.commit()
                raise IntegrityError("insert", {}, Exception("provider already exists"))
            return original_flush(*args, **kwargs)

        session.flush = racing_flush

        health = ResearchProviderHealthRepository(session).record_success("race_provider")

    assert health.provider_name == "race_provider"
    assert health.success_count == 1
    assert health.failure_count == 2
    assert race_triggered is True

    with session_factory() as session:
        rows = session.scalars(
            select(ResearchProviderHealth).where(ResearchProviderHealth.provider_name == "race_provider")
        ).all()

    assert len(rows) == 1
    assert rows[0].success_count == 1
    assert rows[0].failure_count == 2


def test_research_provider_health_repository_atomically_increments_existing_stale_rows(tmp_path):
    session_factory = _db(tmp_path)

    with session_factory() as session:
        session.add(ResearchProviderHealth(provider_name="stale_provider"))
        session.commit()

    success_session_1 = session_factory()
    success_session_2 = session_factory()
    try:
        success_session_1.scalar(
            select(ResearchProviderHealth).where(ResearchProviderHealth.provider_name == "stale_provider")
        )
        success_session_2.scalar(
            select(ResearchProviderHealth).where(ResearchProviderHealth.provider_name == "stale_provider")
        )

        ResearchProviderHealthRepository(success_session_1).record_success("stale_provider")
        ResearchProviderHealthRepository(success_session_2).record_success("stale_provider")
    finally:
        success_session_1.close()
        success_session_2.close()

    failure_session_1 = session_factory()
    failure_session_2 = session_factory()
    try:
        failure_session_1.scalar(
            select(ResearchProviderHealth).where(ResearchProviderHealth.provider_name == "stale_provider")
        )
        failure_session_2.scalar(
            select(ResearchProviderHealth).where(ResearchProviderHealth.provider_name == "stale_provider")
        )

        ResearchProviderHealthRepository(failure_session_1).record_failure("stale_provider", reason="bad_payload")
        ResearchProviderHealthRepository(failure_session_2).record_timeout("stale_provider", reason="timeout")
    finally:
        failure_session_1.close()
        failure_session_2.close()

    with session_factory() as session:
        health = ResearchProviderHealthRepository(session).load_provider_health("stale_provider")

    assert health.success_count == 2
    assert health.failure_count == 2
    assert health.timeout_count == 1
    assert health.last_error == "timeout"


def test_db_backed_provider_health_deprioritizes_primary_for_new_wrapper(tmp_path):
    session_factory = _db(tmp_path)
    with session_factory() as session:
        ResearchProviderHealthRepository(session).record_timeout("coingecko_crypto", reason="timeout")

    fetched_at = _fresh_iso()
    primary = _CryptoProvider(
        DomainEvidenceResult(
            domain="crypto",
            status="confirmed",
            confidence=0.9,
            text=f"Asset: bitcoin. Stand: {fetched_at}. Kurs: 67000 USD.",
            sources=(EvidenceSource("CoinGecko", "https://api.coingecko.com/api/v3/simple/price", fetched_at),),
        )
    )
    fallback = _CryptoProvider(
        DomainEvidenceResult(
            domain="crypto",
            status="confirmed",
            confidence=0.78,
            text=f"Asset: bitcoin. Stand: {fetched_at}. Kurs: 68000 USD/USDT.",
            sources=(EvidenceSource("Binance", "https://api.binance.com/api/v3/ticker/24hr", fetched_at),),
        )
    )
    fallback_definition = ProviderDefinition("binance_crypto", "Binance", "crypto", 35, True, 0.70, 15 * 60)

    provider = ResilientCryptoEvidenceProvider(
        (
            EvidenceProviderCandidate(PROVIDER_REGISTRY["coingecko_crypto"], primary),
            EvidenceProviderCandidate(fallback_definition, fallback),
        ),
        health=DbBackedProviderHealthRegistry(session_factory=session_factory),
    )

    result = provider.get_crypto(query="BTC price now USD", locale="en")

    assert result.confirmed is True
    assert "68000 USD/USDT" in result.text
    assert primary.calls == 0
    assert fallback.calls == 1


def test_neutral_crypto_asset_miss_does_not_poison_db_health(tmp_path):
    session_factory = _db(tmp_path)
    provider = ResilientCryptoEvidenceProvider(
        (
            EvidenceProviderCandidate(
                PROVIDER_REGISTRY["coingecko_crypto"],
                CoinGeckoEvidenceProvider(client=_FakeClient([])),
            ),
        ),
        health=DbBackedProviderHealthRegistry(session_factory=session_factory),
    )

    result = provider.get_crypto(query="FOOBAR token price now", locale="en")

    assert result.confirmed is False
    assert "crypto_asset_not_recognized" in " ".join(result.warnings)
    with session_factory() as session:
        row = session.scalar(
            select(ResearchProviderHealth).where(ResearchProviderHealth.provider_name == "coingecko_crypto")
        )
        assert row is None


def test_webtool_quota_audit_remains_metadata_only(tmp_path):
    session_factory = _db(tmp_path)

    with session_factory() as session:
        repo = WebToolRoleQuotaRepository(session)
        repo.check_quota(
            user_id=42,
            role=Role.OWNER,
            chat_id=-100,
            message_thread_id=None,
            operation_type="crypto",
            day="2026-06-11",
        )

    with session_factory() as session:
        audit = session.scalar(select(WebToolAuditEvent))

    assert audit is not None
    assert audit.operation_type == "crypto"
    audit_values = " ".join(
        str(value)
        for value in (audit.operation_type, audit.decision, audit.reason, audit.error)
        if value is not None
    )
    assert "BTC price now" not in audit_values
    assert "https://" not in audit_values
