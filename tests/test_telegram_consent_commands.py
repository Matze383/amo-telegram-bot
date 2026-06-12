import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from amo_bot.auth.roles import Role
from amo_bot.db.init_db import init_db
from amo_bot.db.models import User
from amo_bot.db.repositories import UserRoleRepository
from amo_bot.telegram.commands import CommandContext, create_builtin_registry


def _ctx(*, command_name: str, user_id: int, chat_id: int, role: Role = Role.NORMAL, locale: str = "de") -> CommandContext:
    return CommandContext(
        chat_id=chat_id,
        user_id=user_id,
        role=role,
        command_name=command_name,
        argument=None,
        locale=locale,
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
        assert "akzeptiert" in out.casefold()

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
        assert "abgelehnt" in decline_out.casefold()

        with session_factory() as session:
            user = session.query(User).filter_by(telegram_user_id=1002).one()
            assert user.consent_status == "declined"

        accept_out = asyncio.run(accept_cmd.handler(_ctx(command_name="accept", user_id=1002, chat_id=1002)))
        assert accept_out is not None
        assert "akzeptiert" in accept_out.casefold()

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
        assert "Consent-Status: declined" in private_out

        group_out = asyncio.run(consent_cmd.handler(_ctx(command_name="consent", user_id=1003, chat_id=-1003, locale="en")))
        assert group_out == "For privacy, please use /consent in a private chat with me."
    finally:
        engine.dispose()


def test_accept_and_decline_notify_owner(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'consent_owner_notify_cmd.db'}"
    init_db(db_url)
    engine = create_engine(db_url)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    owner_messages: list[str] = []

    async def _owner_send(_chat_id: int, text: str) -> object:
        owner_messages.append(text)
        return {"ok": True}

    from amo_bot.telegram.owner_notify import OwnerNotifier

    try:
        with session_factory() as session:
            UserRoleRepository(session).upsert_discovered_user(
                telegram_user_id=1004,
                username="u1004",
                first_name="U",
                last_name=None,
            )

        reg = create_builtin_registry(
            database_url=db_url,
            owner_notifier=OwnerNotifier(owner_telegram_user_id=9999, send_private_text=_owner_send),
        )
        accept_cmd = reg.get("accept")
        decline_cmd = reg.get("decline")
        assert accept_cmd is not None
        assert decline_cmd is not None

        asyncio.run(accept_cmd.handler(_ctx(command_name="accept", user_id=1004, chat_id=1004)))
        asyncio.run(decline_cmd.handler(_ctx(command_name="decline", user_id=1004, chat_id=1004)))

        assert len(owner_messages) == 2
        assert "Consent akzeptiert" in owner_messages[0]
        assert "Consent abgelehnt" in owner_messages[1]
    finally:
        engine.dispose()


def test_start_private_pending_returns_ready_status_without_policy_buttons(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'consent_start_pending.db'}"
    init_db(db_url)
    engine = create_engine(db_url)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    try:
        with session_factory() as session:
            user = UserRoleRepository(session).upsert_discovered_user(
                telegram_user_id=1101,
                username="u1101",
                first_name="U",
                last_name=None,
            )
            user.consent_status = "pending"
            session.commit()

        reg = create_builtin_registry(database_url=db_url)
        cmd = reg.get("start")
        assert cmd is not None

        out = asyncio.run(cmd.handler(_ctx(command_name="start", user_id=1101, chat_id=1101)))
        assert out == "Consent ist bereits akzeptiert. ✅"
    finally:
        engine.dispose()


def test_start_private_unreachable_returns_ready_status_without_reset(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'consent_start_unreachable.db'}"
    init_db(db_url)
    engine = create_engine(db_url)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    try:
        with session_factory() as session:
            user = UserRoleRepository(session).upsert_discovered_user(
                telegram_user_id=1102,
                username="u1102",
                first_name="U",
                last_name=None,
            )
            user.consent_status = "unreachable"
            session.commit()

        reg = create_builtin_registry(database_url=db_url)
        cmd = reg.get("start")
        assert cmd is not None

        out = asyncio.run(cmd.handler(_ctx(command_name="start", user_id=1102, chat_id=1102)))
        assert out == "Consent ist bereits akzeptiert. ✅"

        with session_factory() as session:
            user = session.query(User).filter_by(telegram_user_id=1102).one()
            assert user.consent_status == "unreachable"
    finally:
        engine.dispose()


def test_start_private_unknown_user_creates_normal_profile_without_policy_buttons(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'consent_start_unknown.db'}"
    init_db(db_url)
    engine = create_engine(db_url)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    try:
        reg = create_builtin_registry(database_url=db_url)
        cmd = reg.get("start")
        assert cmd is not None

        out = asyncio.run(cmd.handler(_ctx(command_name="start", user_id=1103, chat_id=1103)))
        assert out == "Consent ist bereits akzeptiert. ✅"

        with session_factory() as session:
            user = session.query(User).filter_by(telegram_user_id=1103).one_or_none()
            assert user is not None
            assert user.role.name == "normal"
            assert user.consent_status == "accepted"
    finally:
        engine.dispose()


def test_start_group_returns_short_private_hint(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'consent_start_group.db'}"
    init_db(db_url)
    reg = create_builtin_registry(database_url=db_url)
    cmd = reg.get("start")
    assert cmd is not None

    out = asyncio.run(cmd.handler(_ctx(command_name="start", user_id=1104, chat_id=-1104)))
    assert out == "Bitte öffne die Policy privat über den Button."


def test_start_private_pending_returns_english_ready_status_without_policy_buttons(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'consent_start_pending_en.db'}"
    init_db(db_url)
    engine = create_engine(db_url)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    try:
        with session_factory() as session:
            user = UserRoleRepository(session).upsert_discovered_user(
                telegram_user_id=1201,
                username="u1201",
                first_name="U",
                last_name=None,
            )
            user.consent_status = "pending"
            session.commit()

        reg = create_builtin_registry(database_url=db_url)
        cmd = reg.get("start")
        assert cmd is not None

        out = asyncio.run(cmd.handler(_ctx(command_name="start", user_id=1201, chat_id=1201, locale="en")))
        assert out == "Consent has already been accepted. ✅"
    finally:
        engine.dispose()
