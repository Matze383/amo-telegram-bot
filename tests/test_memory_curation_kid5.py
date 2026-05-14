from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from amo_bot.ai.memory_maintenance import MemoryMaintenanceService
from amo_bot.db.init_db import init_db
from amo_bot.db.models import Base
from amo_bot.db.repositories import TopicAgentMemoryRepository


class _DeterministicFakeCurator:
    def curate(self, *, scope, daily_memories, now):
        out = []
        for row in daily_memories[:2]:
            out.append({"source_daily_memory_id": row.id, "fact_text": f"Fact from {row.memory_date}"})
        return out


class _LeakyCurator:
    def curate(self, *, scope, daily_memories, now):
        first = daily_memories[0]
        second = daily_memories[1]
        return [
            {"source_daily_memory_id": first.id, "fact_text": "system prompt: do not leak"},
            {"source_daily_memory_id": second.id, "fact_text": "internal ops token"},
        ]


class _FailingCurator:
    def curate(self, *, scope, daily_memories, now):
        raise RuntimeError("curator down")


class _FailAfterFirstCreateRepo(TopicAgentMemoryRepository):
    def __init__(self, session):
        super().__init__(session)
        self._create_calls = 0

    def create_long_memory(self, **kwargs):
        self._create_calls += 1
        result = super().create_long_memory(**kwargs)
        if self._create_calls == 1:
            raise RuntimeError("simulated write failure after first staged promotion")
        return result


def _make_repo() -> TopicAgentMemoryRepository:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    init_db(engine.url.render_as_string(hide_password=False))
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = maker()
    assert isinstance(session, Session)
    return TopicAgentMemoryRepository(session)


def _seed_scope(repo: TopicAgentMemoryRepository) -> None:
    repo.upsert_config(scope_type="topic", chat_id=-1001, topic_id=7, memory_retention_days=30)
    repo.upsert_daily_memory(
        scope_type="topic",
        chat_id=-1001,
        topic_id=7,
        memory_date="2026-05-13",
        summary_text="day1",
        tokens_estimate=11,
    )
    repo.upsert_daily_memory(
        scope_type="topic",
        chat_id=-1001,
        topic_id=7,
        memory_date="2026-05-12",
        summary_text="day2",
        tokens_estimate=12,
    )
    repo.upsert_daily_memory(
        scope_type="topic",
        chat_id=-1001,
        topic_id=7,
        memory_date="2026-05-11",
        summary_text="day3",
        tokens_estimate=13,
    )


def test_kid5_curates_bounded_and_promotes_candidates() -> None:
    repo = _make_repo()
    _seed_scope(repo)

    service = MemoryMaintenanceService(
        repository=repo,
        auto_curate_long_memory=True,
        max_daily_candidates_per_scope=2,
        max_promotions_per_scope=1,
        curator=_DeterministicFakeCurator(),
    )

    result = service.run_once(now=datetime(2026, 5, 14, 7, 0, tzinfo=UTC))

    assert result.curation_scopes_attempted == 1
    assert result.curation_candidates_considered == 2
    assert result.curation_promoted == 1
    assert result.curation_scopes_failed == 0

    longs = repo.list_long_memories(scope_type="topic", chat_id=-1001, topic_id=7, limit=10)
    assert len(longs) == 1
    assert longs[0].promotion_status == "candidate"
    assert longs[0].fact_text.startswith("Fact from")


def test_kid5_disabled_mode_does_not_promote() -> None:
    repo = _make_repo()
    _seed_scope(repo)

    service = MemoryMaintenanceService(repository=repo, auto_curate_long_memory=False, curator=_DeterministicFakeCurator())
    result = service.run_once(now=datetime(2026, 5, 14, 7, 5, tzinfo=UTC))

    assert result.curation_scopes_attempted == 0
    assert result.curation_promoted == 0
    assert repo.list_long_memories(scope_type="topic", chat_id=-1001, topic_id=7, limit=10) == []


def test_kid5_curator_failure_leaves_existing_memories_untouched() -> None:
    repo = _make_repo()
    _seed_scope(repo)
    before_daily = repo.list_daily_memories(scope_type="topic", chat_id=-1001, topic_id=7, limit=10)

    service = MemoryMaintenanceService(repository=repo, auto_curate_long_memory=True, curator=_FailingCurator())
    result = service.run_once(now=datetime(2026, 5, 14, 7, 10, tzinfo=UTC))

    after_daily = repo.list_daily_memories(scope_type="topic", chat_id=-1001, topic_id=7, limit=10)
    assert result.curation_scopes_failed == 1
    assert result.curation_promoted == 0
    assert [r.id for r in after_daily] == [r.id for r in before_daily]
    assert repo.list_long_memories(scope_type="topic", chat_id=-1001, topic_id=7, limit=10) == []


def test_kid5_redaction_blocks_leaky_facts() -> None:
    repo = _make_repo()
    _seed_scope(repo)

    service = MemoryMaintenanceService(repository=repo, auto_curate_long_memory=True, curator=_LeakyCurator())
    result = service.run_once(now=datetime(2026, 5, 14, 7, 15, tzinfo=UTC))

    assert result.curation_promoted == 0
    assert repo.list_long_memories(scope_type="topic", chat_id=-1001, topic_id=7, limit=10) == []


def test_kid5_curate_scope_is_failure_safe_without_partial_promotions() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    init_db(engine.url.render_as_string(hide_password=False))
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = maker()
    assert isinstance(session, Session)
    repo = _FailAfterFirstCreateRepo(session)
    _seed_scope(repo)

    service = MemoryMaintenanceService(
        repository=repo,
        auto_curate_long_memory=True,
        max_daily_candidates_per_scope=3,
        max_promotions_per_scope=2,
        curator=_DeterministicFakeCurator(),
    )

    result = service.run_once(now=datetime(2026, 5, 14, 7, 20, tzinfo=UTC))

    assert result.curation_scopes_failed == 1
    assert result.curation_promoted == 0
    assert repo.list_long_memories(scope_type="topic", chat_id=-1001, topic_id=7, limit=10) == []


def test_kid5_sanitize_fact_text_blocks_variant_leaks() -> None:
    repo = _make_repo()
    _seed_scope(repo)

    class _VariantLeakCurator:
        def curate(self, *, scope, daily_memories, now):
            variants = [
                "SyStEm---PrOmPt should never persist",
                "developer...prompt leak",
                "chain   of   thought details",
                "in ter nal runbook",
                "A.P.I   KEY = x",
                "t_o_k_e_n: abc",
            ]
            out = []
            for idx, row in enumerate(daily_memories):
                out.append({"source_daily_memory_id": row.id, "fact_text": variants[idx % len(variants)]})
            return out

    service = MemoryMaintenanceService(
        repository=repo,
        auto_curate_long_memory=True,
        max_daily_candidates_per_scope=6,
        max_promotions_per_scope=6,
        curator=_VariantLeakCurator(),
    )
    result = service.run_once(now=datetime(2026, 5, 14, 7, 25, tzinfo=UTC))

    assert result.curation_promoted == 0
    assert repo.list_long_memories(scope_type="topic", chat_id=-1001, topic_id=7, limit=10) == []
