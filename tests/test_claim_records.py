from __future__ import annotations

import asyncio

from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session, sessionmaker

from amo_bot.ai.claims import extract_claims
from amo_bot.db.base import Base, create_session_factory
from amo_bot.db.init_db import init_db
from amo_bot.db.models import Claim
from amo_bot.db.repositories import ClaimRepository
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
