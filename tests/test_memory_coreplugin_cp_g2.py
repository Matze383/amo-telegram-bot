from __future__ import annotations

from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from amo_bot.ai.capability_audit import CapabilityAuditTrail, InMemoryCapabilityAuditSink
from amo_bot.ai.capability_policy import CapabilityActorType
from amo_bot.ai.memory_coreplugin_cp_g2 import MemoryCorepluginRequest, MemoryCorepluginService, MemoryScopeRef
from amo_bot.db.models import Base
from amo_bot.db.repositories import TopicAgentMemoryRepository


def _mk_service() -> tuple[TopicAgentMemoryRepository, InMemoryCapabilityAuditSink, MemoryCorepluginService]:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session = Session(engine)
    repo = TopicAgentMemoryRepository(session)
    sink = InMemoryCapabilityAuditSink()
    trail = CapabilityAuditTrail(recorder=sink)
    service = MemoryCorepluginService(repository=repo, audit_trail=trail)
    return repo, sink, service


def _req(scope: MemoryScopeRef, capability_name: str = "ki.memory.put") -> MemoryCorepluginRequest:
    return MemoryCorepluginRequest(
        actor_type=CapabilityActorType.AI,
        capability_name=capability_name,
        scope=scope,
        consent_granted=True,
    )


def test_cp_g2_scope_isolation_and_search() -> None:
    repo, _, service = _mk_service()
    topic_a = MemoryScopeRef(scope_type="topic", chat_id=-100, topic_id=1)
    topic_b = MemoryScopeRef(scope_type="topic", chat_id=-100, topic_id=2)

    service.put_summary(_req(topic_a), memory_date="2026-05-14", summary_text="alpha note", tokens_estimate=10)
    repo.create_long_memory(scope_type="topic", chat_id=-100, topic_id=1, fact_text="alpha fact")
    repo.create_long_memory(scope_type="topic", chat_id=-100, topic_id=2, fact_text="beta fact")

    a_search = service.search_summaries(_req(topic_a, "ki.memory.search"))
    b_search = service.search_summaries(_req(topic_b, "ki.memory.search"))

    assert len(a_search.summaries) == 1
    assert len(b_search.summaries) == 1
    assert "alpha" not in a_search.summaries[0].summary
    assert "beta" not in b_search.summaries[0].summary
    assert a_search.summaries[0].summary.startswith("[redacted:")
    assert b_search.summaries[0].summary.startswith("[redacted:")


def test_cp_g2_ttl_delete_and_deactivate_behavior() -> None:
    repo, _, service = _mk_service()
    scope = MemoryScopeRef(scope_type="topic", chat_id=-100, topic_id=1)

    service.put_summary(_req(scope), memory_date="2026-04-01", summary_text="old", tokens_estimate=1)
    service.put_summary(_req(scope), memory_date="2026-05-14", summary_text="new", tokens_estimate=1)
    long_row = repo.create_long_memory(scope_type="topic", chat_id=-100, topic_id=1, fact_text="live fact")

    delete_res = service.delete_daily_memory(
        _req(scope, "ki.memory.delete"), retention_days=30, today=date(2026, 5, 14)
    )
    assert delete_res.reason_code == "memory_delete_ok"

    assert repo.get_daily_memory(scope_type="topic", chat_id=-100, topic_id=1, memory_date="2026-04-01") is None
    assert repo.get_daily_memory(scope_type="topic", chat_id=-100, topic_id=1, memory_date="2026-05-14") is not None

    deactivate_res = service.deactivate_long_memory(_req(scope, "ki.memory.delete"), memory_id=long_row.id)
    assert deactivate_res.reason_code == "memory_deactivate_ok"

    listed = repo.list_long_memories(scope_type="topic", chat_id=-100, topic_id=1, active_only=False)
    assert listed[0].is_active is False


def test_cp_g2_redaction_and_audit_without_raw_memory_text() -> None:
    repo, sink, service = _mk_service()
    scope = MemoryScopeRef(scope_type="private_user", user_id=77)
    raw_text = "SECRET_PRIVATE_TOKEN_12345"

    put_res = service.put_summary(_req(scope), memory_date="2026-05-14", summary_text=raw_text, tokens_estimate=9)
    assert put_res.summaries
    assert put_res.summaries[0].summary.startswith("[redacted:")
    assert raw_text not in put_res.summaries[0].summary

    get_res = service.get_summary(_req(scope, "ki.memory.get"), memory_date="2026-05-14")
    assert get_res.summaries
    assert get_res.summaries[0].summary.startswith("[redacted:")
    assert raw_text not in get_res.summaries[0].summary

    repo.create_long_memory(scope_type="private_user", user_id=77, fact_text=raw_text)
    search_res = service.search_summaries(_req(scope, "ki.memory.search"))
    assert search_res.summaries
    assert search_res.summaries[0].summary.startswith("[redacted:")
    assert raw_text not in search_res.summaries[0].summary

    for event in sink.events:
        payload_text = str(event)
        assert raw_text not in payload_text
