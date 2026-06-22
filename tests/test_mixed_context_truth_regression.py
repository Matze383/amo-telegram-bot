from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from amo_bot.ai.claims import extract_claims
from amo_bot.ai.compact_topic_state import build_compact_topic_state_payload, format_compact_topic_state_prompt
from amo_bot.ai.context_snapshot import build_context_snapshot
from amo_bot.ai.contextwindow_builder import ContextWindowSource, build_contextwindow_v1
from amo_bot.ai.router import AIRouterContextV1, AIRouterReasonCode
from amo_bot.current_info import (
    CurrentInfoDocumentCacheRepository,
    CurrentInfoRequest,
    DbCurrentInfoRetrievalProvider,
    FetchedDocument,
    VectorCurrentInfoRetrievalProvider,
    VectorSearchResult,
)
from amo_bot.db.base import Base
from amo_bot.db.repositories import ClaimRepository, TopicCompactStateRepository


def _factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


class _FakeEmbeddingProvider:
    def embed_texts(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple((0.1, 0.2, 0.3) for _ in texts)


class _FakeVectorStore:
    def __init__(self, search_results: tuple[VectorSearchResult, ...]) -> None:
        self.search_results = search_results

    def search(self, *, vector: tuple[float, ...], limit: int) -> tuple[VectorSearchResult, ...]:
        del vector, limit
        return self.search_results


def test_mixed_context_incident_class_records_boundary_conflict_and_fail_closed_decision() -> None:
    snapshot = build_context_snapshot(
        current_message="@AmoBot Was ist der aktuelle echte Kurs von BTC?",
        normalized_current_message="Was ist der aktuelle echte Kurs von BTC?",
        router_context=AIRouterContextV1(
            scope_type="topic",
            scope_chat_id=-9001,
            scope_topic_id=77,
            user_id=42,
            message_text="@AmoBot Was ist der aktuelle echte Kurs von BTC?",
            route_reason=AIRouterReasonCode.MENTION_IN_ACTIVE_SCOPE,
            flag_ai_scope_active=True,
            flag_bot_mention=True,
            recent_messages_text=(
                "Die Taverne ist voller Orks und Magie.\n"
                "Unser Fantasy-Charakter sucht eine Quest im Koenigreich."
            ),
            recall_memory_text="Alte Simulation: Ein magischer Token gehoert zur Quest.",
        ),
        existing_current_info_signal=True,
        verified_external_evidence_available=False,
    )

    assert snapshot.current_user_intent == "answer_question"
    assert snapshot.active_subject == "aktuelle echte Kurs BTC"
    assert [(candidate.frame, candidate.source) for candidate in snapshot.frame_candidates] == [
        ("current_turn", "current_message"),
        ("recent_chat_context", "recent_messages"),
        ("retrieved_memory_context", "retrieved_memory"),
    ]
    assert snapshot.source_classes == {
        "current_message": "user_claim",
        "recent_messages": "user_claim",
        "retrieved_memory": "semantic_memory",
    }
    assert snapshot.semantic_memory_sources == ("retrieved_memory",)
    assert snapshot.verified_evidence_sources == ()
    assert [conflict.conflict_type for conflict in snapshot.conflicts] == [
        "semantic_frame_conflict",
        "source_frame_boundary",
    ]
    assert snapshot.conflicts[0].frames == ("real_world_current_fact", "fictional_or_simulated_context")
    assert snapshot.conflicts[1].frames == ("current_turn", "background_context")
    assert snapshot.current_info_decision.requires_external_evidence is True
    assert snapshot.current_info_decision.evidence_available is False
    assert "auto_research_signal" in snapshot.current_info_decision.signals
    assert "Do not assert current facts from model_prior, semantic_memory" in snapshot.current_info_decision.fail_closed_instruction
    assert "source_frame_boundary_needs_resolution" in snapshot.uncertainty


def test_false_user_and_bot_claims_are_captured_as_unverified_until_external_evidence_marks_them() -> None:
    factory = _factory()
    with factory() as session:
        repo = ClaimRepository(session)
        extracted = extract_claims(
            "Der Dienst ist offline. The bot has no live data capability. "
            "Bitte such den echten Status. Ist das aktuell?"
        )
        assert [claim.text for claim in extracted] == [
            "Der Dienst ist offline.",
            "The bot has no live data capability.",
        ]

        user_claim = repo.create_claim(
            text=extracted[0].text,
            normalized_subject=extracted[0].normalized_subject,
            source_type="user_claim",
            source_message_id=100,
            scope_type="topic",
            chat_id=-1001,
            topic_id=77,
            verification_status="supported",
            evidence_ref="current_info_document_chunks:should-not-stick",
            confidence=0.95,
        )
        bot_claim = repo.create_claim(
            text=extracted[1].text,
            normalized_subject=extracted[1].normalized_subject,
            source_type="bot_claim",
            source_message_id=101,
            scope_type="topic",
            chat_id=-1001,
            topic_id=77,
            verification_status="supported",
            evidence_ref="current_info_document_chunks:should-not-stick",
            confidence=0.95,
        )

        assert user_claim.verification_status == "unverified"
        assert user_claim.evidence_ref is None
        assert bot_claim.verification_status == "unverified"
        assert bot_claim.evidence_ref is None

        refuted_bot_claim = repo.mark_refuted(
            claim_id=bot_claim.id,
            evidence_ref="current_info_document_chunks:11",
            confidence=0.91,
        )

    assert refuted_bot_claim is not None
    assert refuted_bot_claim.source_type == "bot_claim"
    assert refuted_bot_claim.verification_status == "refuted"
    assert refuted_bot_claim.evidence_ref == "current_info_document_chunks:11"


def test_verified_external_evidence_wins_in_compact_state_without_promoting_semantic_memory() -> None:
    factory = _factory()
    with factory() as session:
        claim_repo = ClaimRepository(session)
        stale_memory_claim = claim_repo.create_claim(
            text="Der Dienst ist offline.",
            normalized_subject="dienst offline",
            source_type="user_claim",
            source_message_id=201,
            scope_type="topic",
            chat_id=-1001,
            topic_id=77,
            auto_commit=False,
        )
        false_bot_claim = claim_repo.create_claim(
            text="The bot has no live data capability.",
            normalized_subject="bot live data capability",
            source_type="bot_claim",
            source_message_id=202,
            scope_type="topic",
            chat_id=-1001,
            topic_id=77,
            auto_commit=False,
        )
        supported_evidence = claim_repo.create_claim(
            text="Der Dienst ist online.",
            normalized_subject="dienst online",
            source_type="verified_external_evidence",
            scope_type="topic",
            chat_id=-1001,
            topic_id=77,
            verification_status="supported",
            confidence=0.94,
            evidence_ref="current_info_document_chunks:42",
            auto_commit=False,
        )
        session.flush()
        claim_repo.mark_refuted(
            claim_id=false_bot_claim.id,
            evidence_ref="current_info_document_chunks:42",
            confidence=0.93,
        )

        snapshot = build_context_snapshot(
            current_message="Wie ist der aktuelle echte Status des Dienstes?",
            router_context=AIRouterContextV1(
                scope_type="topic",
                scope_chat_id=-1001,
                scope_topic_id=77,
                route_reason=AIRouterReasonCode.MENTION_IN_ACTIVE_SCOPE,
                flag_ai_scope_active=True,
                flag_bot_mention=True,
                recall_memory_text="Alte Erinnerung: Der Dienst ist offline.",
            ),
            existing_current_info_signal=True,
            verified_external_evidence_available=True,
        )
        payload = build_compact_topic_state_payload(
            snapshot=snapshot,
            claims=claim_repo.list_claims(scope_type="topic", chat_id=-1001, topic_id=77),
        )
        record = TopicCompactStateRepository(session).upsert_state(
            scope_type="topic",
            chat_id=-1001,
            topic_id=77,
            active_subjects=payload.active_subjects,
            frames=payload.frames,
            conflicts=payload.conflicts,
            verified_facts=payload.verified_facts,
            discarded_assumptions=payload.discarded_assumptions,
            last_snapshot=payload.last_snapshot,
        )

    assert snapshot.current_info_decision.evidence_available is True
    assert snapshot.current_info_decision.fail_closed_instruction == ""
    assert snapshot.semantic_memory_sources == ("retrieved_memory",)
    assert snapshot.verified_evidence_sources == ("verified_external_evidence",)
    assert [item["claim_id"] for item in record.verified_facts] == [supported_evidence.id]
    assert record.verified_facts[0]["evidence_ref"] == "current_info_document_chunks:42"
    assert stale_memory_claim.id not in {item.get("claim_id") for item in record.verified_facts}
    assert any(item["claim_id"] == false_bot_claim.id and item["reason"] == "claim_refuted" for item in record.discarded_assumptions)

    prompt_text = format_compact_topic_state_prompt(record)
    assert "Only verified_facts are evidence" in prompt_text
    assert "Der Dienst ist online." in prompt_text
    assert "The bot has no live data capability." in prompt_text
    assert "Der Dienst ist offline." not in prompt_text


def test_context_window_audit_keeps_retrieved_memory_context_but_excludes_private_memory_sources() -> None:
    result = build_contextwindow_v1(
        token_budget=200,
        sources=[
            ContextWindowSource(
                source_id="current",
                source_type="user",
                text="Was ist der aktuelle echte Status des Dienstes?",
                priority=1,
                metadata={"class": "public", "kind": "input"},
            ),
            ContextWindowSource(
                source_id="semantic-recall",
                source_type="context",
                text="Alte Erinnerung: Der Dienst ist offline.",
                priority=2,
                metadata={"class": "profile", "kind": "context", "tag": "derived"},
            ),
            ContextWindowSource(
                source_id="raw-memory",
                source_type="long-memory",
                text="Private raw memory must not enter the synthesis window.",
                priority=3,
                metadata={"class": "profile", "kind": "context"},
            ),
        ],
    )

    assert [entry.source_id for entry in result.included] == ["current", "semantic-recall"]
    assert result.included[1].metadata["kind"] == "context"
    assert result.excluded[0].source_id == "raw-memory"
    assert result.excluded[0].reason == "sensitive_source_type_excluded"
    assert "Private raw memory" not in str(result.excluded[0])


def test_vector_evidence_requires_verified_database_pointer_before_it_can_become_current_evidence(caplog) -> None:
    factory = _factory()
    provider = VectorCurrentInfoRetrievalProvider(
        session_factory=factory,
        vector_store=_FakeVectorStore((VectorSearchResult(chunk_id=987654, score=0.99, metadata={"title": "orphan"}),)),
        embedding_provider=_FakeEmbeddingProvider(),
        fallback_provider=DbCurrentInfoRetrievalProvider(session_factory=factory),
    )

    unresolved_chunks = provider.retrieve(
        request=CurrentInfoRequest(query="aktueller echter Status", max_results=3),
        documents=(),
        search_results=(),
    )

    assert unresolved_chunks == ()
    assert "current_info_vector_unresolved_mariadb_pointers: count=1" in caplog.text

    now = datetime(2026, 6, 22, 10, 0, tzinfo=UTC)
    with factory() as session:
        row = CurrentInfoDocumentCacheRepository(session).store_document(
            FetchedDocument(
                url="https://status.example/current",
                title="Status",
                text="Der Dienst ist online.",
                metadata={"source_type": "Official"},
            ),
            language="de",
            now=now,
        )
        chunk_id = int(row.chunks[0].id)
        session.commit()

    provider = VectorCurrentInfoRetrievalProvider(
        session_factory=factory,
        vector_store=_FakeVectorStore((VectorSearchResult(chunk_id=chunk_id, score=0.88, metadata={}),)),
        embedding_provider=_FakeEmbeddingProvider(),
        fallback_provider=DbCurrentInfoRetrievalProvider(session_factory=factory),
    )

    verified_chunks = provider.retrieve(
        request=CurrentInfoRequest(query="aktueller echter Status", max_results=3),
        documents=(),
        search_results=(),
    )

    assert len(verified_chunks) == 1
    assert verified_chunks[0].text == "Der Dienst ist online."
    assert verified_chunks[0].metadata["retrieval"] == "vector"
    assert verified_chunks[0].metadata["pointer_status"] == "verified_mariadb_pointer"
