import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from amo_bot.auth.roles import Role
from amo_bot.db.init_db import init_db
from amo_bot.db.models import User
from amo_bot.db.repositories import UserRoleRepository
from amo_bot.telegram.commands import CommandContext, create_builtin_registry


def _ctx(*, command_name: str, user_id: int, chat_id: int, role: Role = Role.NORMAL) -> CommandContext:
    return CommandContext(
        chat_id=chat_id,
        user_id=user_id,
        role=role,
        command_name=command_name,
        argument=None,
    )


def test_accept_sets_accepted(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'consent_accept.db'}"
    init_db(db_url)
    engine = create_engine(db_url)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    try:
        with session_factory() as session:
            UserRoleRepository(session).upsert_discovered_user(
                telegram_user_id=1001,
                username="u1001",
                first_name="U",
                last_name=None,
            )

        reg = create_builtin_registry(database_url=db_url)
        cmd = reg.get("accept")
        assert cmd is not None

        out = asyncio.run(cmd.handler(_ctx(command_name="accept", user_id=1001, chat_id=1001)))
        assert out is not None
        assert "accepted" in out

        with session_factory() as session:
            user = session.query(User).filter_by(telegram_user_id=1001).one()
            assert user.consent_status == "accepted"
    finally:
        engine.dispose()


def test_decline_sets_declined_and_accept_reactivates(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'consent_decline_accept.db'}"
    init_db(db_url)
    engine = create_engine(db_url)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    try:
        with session_factory() as session:
            UserRoleRepository(session).upsert_discovered_user(
                telegram_user_id=1002,
                username="u1002",
                first_name="U",
                last_name=None,
            )

        reg = create_builtin_registry(database_url=db_url)
        decline_cmd = reg.get("decline")
        accept_cmd = reg.get("accept")
        assert decline_cmd is not None
        assert accept_cmd is not None

        decline_out = asyncio.run(decline_cmd.handler(_ctx(command_name="decline", user_id=1002, chat_id=1002)))
        assert decline_out is not None
        assert "declined" in decline_out

        with session_factory() as session:
            user = session.query(User).filter_by(telegram_user_id=1002).one()
            assert user.consent_status == "declined"

        accept_out = asyncio.run(accept_cmd.handler(_ctx(command_name="accept", user_id=1002, chat_id=1002)))
        assert accept_out is not None
        assert "accepted" in accept_out

        with session_factory() as session:
            user = session.query(User).filter_by(telegram_user_id=1002).one()
            assert user.consent_status == "accepted"
    finally:
        engine.dispose()


def test_consent_shows_status_private_and_privacy_in_group(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'consent_status.db'}"
    init_db(db_url)
    engine = create_engine(db_url)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    try:
        with session_factory() as session:
            UserRoleRepository(session).upsert_discovered_user(
                telegram_user_id=1003,
                username="u1003",
                first_name="U",
                last_name=None,
            )

        reg = create_builtin_registry(database_url=db_url)
        consent_cmd = reg.get("consent")
        decline_cmd = reg.get("decline")
        assert consent_cmd is not None
        assert decline_cmd is not None

        asyncio.run(decline_cmd.handler(_ctx(command_name="decline", user_id=1003, chat_id=1003)))
        private_out = asyncio.run(consent_cmd.handler(_ctx(command_name="consent", user_id=1003, chat_id=1003)))
        assert private_out is not None
        assert "consent status: declined" in private_out

        group_out = asyncio.run(consent_cmd.handler(_ctx(command_name="consent", user_id=1003, chat_id=-1003)))
        assert group_out == "for privacy, please use /consent in a private chat with me."
    finally:
        engine.dispose()
