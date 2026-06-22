from __future__ import annotations

import asyncio

from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.orm import Session, sessionmaker

from amo_bot.ai.claims import extract_claims
from amo_bot.ai.compact_topic_state import build_compact_topic_state_payload, format_compact_topic_state_prompt
from amo_bot.ai.context_snapshot import build_context_snapshot
from amo_bot.ai.router import AIRouterContextV1, AIRouterReasonCode
from amo_bot.db.base import Base, create_session_factory
from amo_bot.db.init_db import init_db
from amo_bot.db.models import Claim, TopicCompactState
from amo_bot.db.repositories import ClaimRepository, TopicCompactStateRepository
from amo_bot.telegram.chat_topic_persistence import ChatTopicPersistenceService
from amo_bot.telegram.update_parser import TelegramChat, TelegramMessage, TelegramUser


def _factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


def test_extract_claims_returns_factual_statements_without_questions_or_commands() -> None:
    claims = extract_claims(
        "BTC ist heute bei 100000 USD. Wie ist ETH? Bitte such Nachrichten. "
        "The service is healthy."
    )

    assert [claim.text for claim in claims] == [
        "BTC ist heute bei 100000 USD.",
        "The service is healthy.",
    ]
    assert claims[0].normalized_subject == "btc heute bei 100000 usd"
    assert all(0.0 <= claim.confidence <= 1.0 for claim in claims)


def test_claim_repository_persists_user_claims_as_unverified_and_transitions_with_evidence() -> None:
    factory = _factory()
    with factory() as session:
        repo = ClaimRepository(session)
        claim = repo.create_claim(
            text="BTC ist heute bei 100000 USD.",
            normalized_subject="btc heute bei 100000 usd",
            source_type="user_claim",
            source_message_id=123,
            scope_type="topic",
            chat_id=-1001,
            topic_id=77,
            verification_status="supported",
            confidence=0.6,
            evidence_ref="https://example.invalid/ignored",
        )

        assert claim.verification_status == "unverified"
        assert claim.scope == "topic:-1001:77"
        assert claim.evidence_ref is None

        supported = repo.mark_supported(
            claim_id=claim.id,
            evidence_ref="current_info_document_chunks:42",
            confidence=0.92,
        )
        assert supported is not None
        assert supported.verification_status == "supported"
        assert supported.evidence_ref == "current_info_document_chunks:42"
        assert supported.confidence == 0.92

        refuted = repo.mark_refuted(
            claim_id=claim.id,
            evidence_ref="websearch:evidence:abc",
            confidence=0.81,
        )
        assert refuted is not None
        assert refuted.verification_status == "refuted"
        assert refuted.evidence_ref == "websearch:evidence:abc"


def test_chat_topic_persistence_extracts_claim_records_for_scoped_messages(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'claim_persistence.sqlite3'}"
    init_db(db_url)
    service = ChatTopicPersistenceService(create_session_factory(db_url))

    asyncio.run(
        service.persist_message(
            TelegramMessage(
                message_id=44,
                from_user=TelegramUser(id=42, is_bot=False, first_name="U", username="user42"),
                chat=TelegramChat(id=-1001, type="supergroup", title="Group", username=None),
                message_thread_id=77,
                text="BTC ist heute bei 100000 USD.",
            )
        )
    )

    with create_session_factory(db_url)() as session:
        claims = session.scalars(select(Claim)).all()
        assert len(claims) == 1
        claim = claims[0]
        assert claim.text == "BTC ist heute bei 100000 USD."
        assert claim.source_type == "user_claim"
        assert claim.source_message_id == 44
        assert claim.scope == "topic:-1001:77"
        assert claim.scope_type == "topic"
        assert claim.chat_id == -1001
        assert claim.topic_id == 77
        assert claim.user_id is None
        assert claim.verification_status == "unverified"


def test_bot_peer_persistence_extracts_bot_claim_records(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'bot_claim_persistence.sqlite3'}"
    init_db(db_url)
    service = ChatTopicPersistenceService(create_session_factory(db_url))

    asyncio.run(
        service.persist_bot_peer_recent_message(
            TelegramMessage(
                message_id=55,
                from_user=TelegramUser(id=7002, is_bot=True, first_name="PeerBot", username="peer_bot"),
                chat=TelegramChat(id=-1001, type="supergroup", title="Group", username=None),
                message_thread_id=77,
                text="The service is healthy.",
            )
        )
    )

    with create_session_factory(db_url)() as session:
        claim = session.scalar(select(Claim))
        assert claim is not None
        assert claim.source_type == "bot_claim"
        assert claim.source_message_id == 55
        assert claim.verification_status == "unverified"


def test_init_db_creates_claims_table(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'claims_table.sqlite3'}"

    init_db(db_url)

    engine = create_engine(db_url, future=True)
    try:
        columns = {column["name"] for column in inspect(engine).get_columns("claims")}
    finally:
        engine.dispose()

    assert {
        "text",
        "normalized_subject",
        "source_type",
        "source_message_id",
        "scope",
        "scope_type",
        "timestamp",
        "verification_status",
        "confidence",
        "evidence_ref",
    }.issubset(columns)


def test_init_db_creates_topic_compact_states_table(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'compact_state_table.sqlite3'}"

    init_db(db_url)

    engine = create_engine(db_url, future=True)
    try:
        db_inspector = inspect(engine)
        columns = {column["name"] for column in db_inspector.get_columns("topic_compact_states")}
        indexes = {index["name"] for index in db_inspector.get_indexes("topic_compact_states")}
    finally:
        engine.dispose()

    assert {
        "schema_version",
        "scope",
        "scope_type",
        "active_subjects_json",
        "frames_json",
        "conflicts_json",
        "verified_facts_json",
        "discarded_assumptions_json",
        "last_snapshot_json",
    }.issubset(columns)
    assert "ux_topic_compact_states_scope" in indexes


def test_compact_topic_state_upserts_once_per_stable_scope() -> None:
    factory = _factory()
    with factory() as session:
        repo = TopicCompactStateRepository(session)

        scopes = [
            {"scope_type": "topic", "chat_id": -1001, "topic_id": 77, "user_id": None, "expected": "topic:-1001:77"},
            {"scope_type": "group_chat", "chat_id": -1001, "topic_id": None, "user_id": None, "expected": "group_chat:-1001"},
            {"scope_type": "private_user", "chat_id": None, "topic_id": None, "user_id": 42, "expected": "private_user:42"},
        ]
        for scope in scopes:
            for message_id in (1, 2):
                record = repo.upsert_state(
                    scope_type=scope["scope_type"],
                    chat_id=scope["chat_id"],
                    topic_id=scope["topic_id"],
                    user_id=scope["user_id"],
                    active_subjects=[{"subject": f"message {message_id}"}],
                    frames=[],
                    conflicts=[],
                    verified_facts=[],
                    discarded_assumptions=[],
                    updated_from_message_id=message_id,
                )
            assert record.scope == scope["expected"]
            assert record.updated_from_message_id == 2

        row_count = session.scalar(select(func.count()).select_from(TopicCompactState))
        stored_scopes = set(session.scalars(select(TopicCompactState.scope)))

    assert row_count == 3
    assert stored_scopes == {"topic:-1001:77", "group_chat:-1001", "private_user:42"}


def test_init_db_backfills_compact_state_scope_and_removes_legacy_duplicates(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'compact_state_backfill.sqlite3'}"
    engine = create_engine(db_url, future=True)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                """
                CREATE TABLE topic_compact_states (
                    id INTEGER NOT NULL PRIMARY KEY,
                    schema_version VARCHAR(32) NOT NULL DEFAULT 'topic_compact_state_v1',
                    scope_type VARCHAR(32) NOT NULL,
                    chat_id BIGINT,
                    topic_id BIGINT,
                    user_id BIGINT,
                    active_subjects_json TEXT NOT NULL DEFAULT '[]',
                    frames_json TEXT NOT NULL DEFAULT '[]',
                    conflicts_json TEXT NOT NULL DEFAULT '[]',
                    verified_facts_json TEXT NOT NULL DEFAULT '[]',
                    discarded_assumptions_json TEXT NOT NULL DEFAULT '[]',
                    last_snapshot_json TEXT NOT NULL DEFAULT '{}',
                    updated_from_message_id BIGINT,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT uq_topic_compact_states_scope UNIQUE (scope_type, chat_id, topic_id, user_id)
                )
                """
            )
            connection.exec_driver_sql(
                """
                INSERT INTO topic_compact_states (
                    id, scope_type, chat_id, topic_id, user_id, active_subjects_json, updated_from_message_id
                )
                VALUES
                    (1, 'topic', -1001, 77, NULL, '[{"subject":"old"}]', 1),
                    (2, 'topic', -1001, 77, NULL, '[{"subject":"new"}]', 2),
                    (3, 'group_chat', -1001, NULL, NULL, '[]', 3),
                    (4, 'private_user', NULL, NULL, 42, '[]', 4)
                """
            )
    finally:
        engine.dispose()

    init_db(db_url)

    factory = create_session_factory(db_url)
    with factory() as session:
        row_count = session.scalar(select(func.count()).select_from(TopicCompactState))
        topic_state = TopicCompactStateRepository(session).get_state(scope_type="topic", chat_id=-1001, topic_id=77)
        group_state = TopicCompactStateRepository(session).get_state(scope_type="group_chat", chat_id=-1001)
        private_state = TopicCompactStateRepository(session).get_state(scope_type="private_user", user_id=42)

    assert row_count == 3
    assert topic_state is not None
    assert topic_state.scope == "topic:-1001:77"
    assert topic_state.updated_from_message_id == 2
    assert group_state is not None
    assert group_state.scope == "group_chat:-1001"
    assert private_state is not None
    assert private_state.scope == "private_user:42"


def test_compact_topic_state_persists_snapshot_conflicts_and_claim_statuses() -> None:
    factory = _factory()
    with factory() as session:
        claim_repo = ClaimRepository(session)
        supported_claim = claim_repo.create_claim(
            text="Der Dienst ist online.",
            normalized_subject="dienst online",
            source_type="verified_external_evidence",
            scope_type="topic",
            chat_id=-1001,
            topic_id=77,
            verification_status="supported",
            confidence=0.9,
            evidence_ref="current_info_document_chunks:7",
            auto_commit=False,
        )
        claim_repo.create_claim(
            text="Die Fantasy-Simulation ist die echte WM.",
            normalized_subject="fantasy simulation echte wm",
            source_type="verified_external_evidence",
            scope_type="topic",
            chat_id=-1001,
            topic_id=77,
            verification_status="refuted",
            confidence=0.8,
            evidence_ref="websearch:evidence:wm",
            auto_commit=False,
        )
        session.flush()

        snapshot = build_context_snapshot(
            current_message="Wie stehen aktuell die Gruppen der echten Fußball WM?",
            router_context=AIRouterContextV1(
                scope_type="topic",
                scope_chat_id=-1001,
                scope_topic_id=77,
                route_reason=AIRouterReasonCode.MENTION_IN_ACTIVE_SCOPE,
                flag_ai_scope_active=True,
                flag_bot_mention=True,
                recent_messages_text="Fantasy WM Simulation im Koenigreich.",
            ),
        )
        state_repo = TopicCompactStateRepository(session)
        payload = build_compact_topic_state_payload(
            snapshot=snapshot,
            claims=claim_repo.list_claims(scope_type="topic", chat_id=-1001, topic_id=77),
        )
        record = state_repo.upsert_state(
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

        assert any(item["claim_id"] == supported_claim.id for item in record.verified_facts)
        assert any(item["conflict_type"] == "semantic_frame_conflict" for item in record.conflicts)
        assert any(item["reason"] == "claim_refuted" for item in record.discarded_assumptions)
        prompt_text = format_compact_topic_state_prompt(record)
        assert "Compact topic state:" in prompt_text
        assert "verified_facts:" in prompt_text
        assert "discarded_assumptions:" in prompt_text
