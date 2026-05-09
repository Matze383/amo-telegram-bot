import asyncio

from amo_bot.auth.roles import Role
from amo_bot.db.base import create_session_factory
from amo_bot.db.init_db import init_db
from amo_bot.telegram.commands import CommandContext, create_builtin_registry
from amo_bot.telegram.role_resolver import DBRoleResolver


def _ctx(*, user_id: int, role: Role, argument: str | None) -> CommandContext:
    return CommandContext(
        chat_id=1,
        user_id=user_id,
        role=role,
        command_name="setrole",
        argument=argument,
    )


def test_db_role_resolver_unknown_defaults_to_normal(tmp_path) -> None:
    db_file = tmp_path / "test.db"
    db_url = f"sqlite:///{db_file}"
    init_db(db_url)
    sf = create_session_factory(db_url)

    resolver = DBRoleResolver(sf)
    out = asyncio.run(resolver.resolve(123456789))
    assert out == Role.NORMAL


def test_owner_can_set_roles(tmp_path) -> None:
    db_file = tmp_path / "test.db"
    db_url = f"sqlite:///{db_file}"
    init_db(db_url)

    reg = create_builtin_registry(database_url=db_url)
    cmd = reg.get("setrole")
    assert cmd is not None

    out = asyncio.run(cmd.handler(_ctx(user_id=1, role=Role.OWNER, argument="200 vip")))
    assert out is not None
    assert "role updated" in out
    assert "-> vip" in out


def test_admin_can_set_vip_normal_ignore(tmp_path) -> None:
    db_file = tmp_path / "test.db"
    db_url = f"sqlite:///{db_file}"
    init_db(db_url)

    reg = create_builtin_registry(database_url=db_url)
    cmd = reg.get("setrole")
    assert cmd is not None

    out_vip = asyncio.run(cmd.handler(_ctx(user_id=10, role=Role.ADMIN, argument="201 vip")))
    out_normal = asyncio.run(cmd.handler(_ctx(user_id=10, role=Role.ADMIN, argument="201 normal")))
    out_ignore = asyncio.run(cmd.handler(_ctx(user_id=10, role=Role.ADMIN, argument="201 ignore")))

    assert out_vip and "-> vip" in out_vip
    assert out_normal and "-> normal" in out_normal
    assert out_ignore and "-> ignore" in out_ignore


def test_admin_cannot_set_admin_or_owner(tmp_path) -> None:
    db_file = tmp_path / "test.db"
    db_url = f"sqlite:///{db_file}"
    init_db(db_url)

    reg = create_builtin_registry(database_url=db_url)
    cmd = reg.get("setrole")
    assert cmd is not None

    out_admin = asyncio.run(cmd.handler(_ctx(user_id=10, role=Role.ADMIN, argument="202 admin")))
    out_owner = asyncio.run(cmd.handler(_ctx(user_id=10, role=Role.ADMIN, argument="202 owner")))

    assert out_admin == "permission denied. admin may only assign: ignore, normal, vip"
    assert out_owner == "permission denied. admin may only assign: ignore, normal, vip"


def test_normal_and_vip_cannot_set_roles(tmp_path) -> None:
    db_file = tmp_path / "test.db"
    db_url = f"sqlite:///{db_file}"
    init_db(db_url)

    reg = create_builtin_registry(database_url=db_url)
    cmd = reg.get("setrole")
    assert cmd is not None

    out_normal = asyncio.run(cmd.handler(_ctx(user_id=11, role=Role.NORMAL, argument="300 vip")))
    out_vip = asyncio.run(cmd.handler(_ctx(user_id=12, role=Role.VIP, argument="300 normal")))

    assert out_normal == "permission denied"
    assert out_vip == "permission denied"


def test_audit_event_written_on_role_change(tmp_path) -> None:
    from sqlalchemy import select

    from amo_bot.db.models import AuditEvent

    db_file = tmp_path / "test.db"
    db_url = f"sqlite:///{db_file}"
    init_db(db_url)

    reg = create_builtin_registry(database_url=db_url)
    cmd = reg.get("setrole")
    assert cmd is not None

    _ = asyncio.run(cmd.handler(_ctx(user_id=77, role=Role.OWNER, argument="400 vip")))

    sf = create_session_factory(db_url)
    with sf() as session:
        events = session.scalars(select(AuditEvent).where(AuditEvent.event_type == "role_set")).all()

    assert len(events) == 1
    assert events[0].actor_telegram_user_id == 77
    assert '"target_telegram_user_id": 400' in events[0].payload_json
    assert '"new_role": "vip"' in events[0].payload_json


def test_bootstrap_owner_from_settings_empty_db_creates_owner(tmp_path) -> None:
    db_file = tmp_path / "test.db"
    db_url = f"sqlite:///{db_file}"
    init_db(db_url)

    sf = create_session_factory(db_url)
    with sf() as session:
        from amo_bot.db.repositories import UserRoleRepository

        changed = UserRoleRepository(session).bootstrap_owner_from_settings(owner_telegram_user_id=777)
        assert changed is True

    with sf() as session:
        from amo_bot.db.repositories import UserRoleRepository

        assert UserRoleRepository(session).get_user_role(777) == Role.OWNER


def test_bootstrap_owner_from_settings_is_idempotent(tmp_path) -> None:
    db_file = tmp_path / "test.db"
    db_url = f"sqlite:///{db_file}"
    init_db(db_url)

    sf = create_session_factory(db_url)
    with sf() as session:
        from amo_bot.db.repositories import UserRoleRepository

        repo = UserRoleRepository(session)
        assert repo.bootstrap_owner_from_settings(owner_telegram_user_id=888) is True

    with sf() as session:
        from amo_bot.db.repositories import UserRoleRepository

        repo = UserRoleRepository(session)
        assert repo.bootstrap_owner_from_settings(owner_telegram_user_id=888) is False


def test_bootstrap_owner_from_settings_without_owner_id_does_nothing(tmp_path) -> None:
    from sqlalchemy import select

    from amo_bot.db.models import AuditEvent

    db_file = tmp_path / "test.db"
    db_url = f"sqlite:///{db_file}"
    init_db(db_url)

    sf = create_session_factory(db_url)
    with sf() as session:
        from amo_bot.db.repositories import UserRoleRepository

        changed = UserRoleRepository(session).bootstrap_owner_from_settings(owner_telegram_user_id=None)
        assert changed is False

    with sf() as session:
        events = session.scalars(select(AuditEvent).where(AuditEvent.event_type == "role_set")).all()
        assert events == []
