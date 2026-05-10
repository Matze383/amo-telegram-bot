import asyncio
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from amo_bot.auth.roles import Role
from amo_bot.db.base import create_session_factory
from amo_bot.db.init_db import init_db
from amo_bot.db.models import AuditEvent
from amo_bot.telegram.commands import CommandContext, create_builtin_registry


def _ctx(*, command_name: str, user_id: int, role: Role, chat_id: int, argument: str | None = None) -> CommandContext:
    return CommandContext(
        chat_id=chat_id,
        user_id=user_id,
        role=role,
        command_name=command_name,
        argument=argument,
    )


def _get_event(sf, event_type: str) -> AuditEvent:
    with sf() as session:
        event = session.scalar(select(AuditEvent).where(AuditEvent.event_type == event_type))
    assert event is not None
    return event


def test_webui_on_private_owner_allows_and_sets_60_minutes(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'webui_on.db'}"
    init_db(db_url)
    reg = create_builtin_registry(database_url=db_url)
    cmd = reg.get("webui")
    assert cmd is not None

    before = datetime.now(UTC)
    out = asyncio.run(cmd.handler(_ctx(command_name="webui", user_id=777, role=Role.OWNER, chat_id=777, argument="on")))
    after = datetime.now(UTC)

    assert out is not None
    assert "webui access: OPEN" in out
    assert "~60m" in out

    sf = create_session_factory(db_url)
    event = _get_event(sf, "webui_access_enabled")
    payload = json.loads(event.payload_json)
    assert event.actor_telegram_user_id == 777
    assert payload["chat_id"] == 777
    assert payload["chat_type"] == "private"

    enabled_until = datetime.fromisoformat(payload["enabled_until"]) 
    assert before + timedelta(minutes=59, seconds=55) <= enabled_until <= after + timedelta(minutes=60, seconds=5)


def test_webui_on_reon_extends_from_new_now(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'webui_reon.db'}"
    init_db(db_url)
    reg = create_builtin_registry(database_url=db_url)
    cmd = reg.get("webui")
    assert cmd is not None

    first = asyncio.run(cmd.handler(_ctx(command_name="webui", user_id=777, role=Role.OWNER, chat_id=777, argument="on")))
    assert first is not None

    import time
    time.sleep(1.1)

    second = asyncio.run(cmd.handler(_ctx(command_name="webui", user_id=777, role=Role.OWNER, chat_id=777, argument="on")))
    assert second is not None

    sf = create_session_factory(db_url)
    with sf() as session:
        events = session.scalars(
            select(AuditEvent)
            .where(AuditEvent.event_type == "webui_access_enabled")
            .order_by(AuditEvent.id.asc())
        ).all()

    assert len(events) == 2
    first_until = datetime.fromisoformat(json.loads(events[0].payload_json)["enabled_until"])
    second_until = datetime.fromisoformat(json.loads(events[1].payload_json)["enabled_until"])
    assert second_until > first_until


def test_webui_off_private_owner_allows_and_audits(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'webui_off.db'}"
    init_db(db_url)
    reg = create_builtin_registry(database_url=db_url)
    cmd = reg.get("webui")
    assert cmd is not None

    _ = asyncio.run(cmd.handler(_ctx(command_name="webui", user_id=777, role=Role.OWNER, chat_id=777, argument="on")))
    out = asyncio.run(cmd.handler(_ctx(command_name="webui", user_id=777, role=Role.OWNER, chat_id=777, argument="off")))

    assert out == "webui access: CLOSED"

    sf = create_session_factory(db_url)
    event = _get_event(sf, "webui_access_disabled")
    payload = json.loads(event.payload_json)
    assert event.actor_telegram_user_id == 777
    assert payload["chat_id"] == 777
    assert payload["chat_type"] == "private"


def test_webui_status_private_owner_open_closed_and_audit(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'webui_status.db'}"
    init_db(db_url)
    reg = create_builtin_registry(database_url=db_url)
    cmd = reg.get("webui")
    assert cmd is not None

    out_closed = asyncio.run(cmd.handler(_ctx(command_name="webui", user_id=777, role=Role.OWNER, chat_id=777, argument="status")))
    assert out_closed == "webui access: CLOSED"

    _ = asyncio.run(cmd.handler(_ctx(command_name="webui", user_id=777, role=Role.OWNER, chat_id=777, argument="on")))
    out_open = asyncio.run(cmd.handler(_ctx(command_name="webui", user_id=777, role=Role.OWNER, chat_id=777, argument="status")))
    assert out_open is not None
    assert "webui access: OPEN" in out_open
    assert "remaining:" in out_open

    sf = create_session_factory(db_url)
    with sf() as session:
        events = session.scalars(select(AuditEvent).where(AuditEvent.event_type == "webui_access_status").order_by(AuditEvent.id.asc())).all()

    assert len(events) == 2
    closed_payload = json.loads(events[0].payload_json)
    open_payload = json.loads(events[1].payload_json)
    assert closed_payload["open"] is False
    assert closed_payload["remaining_minutes"] == 0
    assert open_payload["open"] is True
    assert open_payload["remaining_minutes"] >= 59


def test_webui_group_or_topic_denied_and_audited_not_private(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'webui_group_deny.db'}"
    init_db(db_url)
    reg = create_builtin_registry(database_url=db_url)
    cmd = reg.get("webui")
    assert cmd is not None

    out_group = asyncio.run(cmd.handler(_ctx(command_name="webui", user_id=777, role=Role.OWNER, chat_id=-100001, argument="status")))
    assert out_group == "permission denied"

    sf = create_session_factory(db_url)
    event = _get_event(sf, "webui_access_denied")
    payload = json.loads(event.payload_json)
    assert payload["reason"] == "not_private"
    assert payload["chat_id"] == -100001


def test_webui_non_owner_denied_and_audited_not_owner(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'webui_not_owner_deny.db'}"
    init_db(db_url)
    reg = create_builtin_registry(database_url=db_url)
    cmd = reg.get("webui")
    assert cmd is not None

    out = asyncio.run(cmd.handler(_ctx(command_name="webui", user_id=1000, role=Role.ADMIN, chat_id=1000, argument="status")))
    assert out == "permission denied"

    sf = create_session_factory(db_url)
    event = _get_event(sf, "webui_access_denied")
    payload = json.loads(event.payload_json)
    assert payload["reason"] == "not_owner"
    assert payload["chat_id"] == 1000


def test_help_for_owner_contains_webui_command(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'webui_help.db'}"
    init_db(db_url)
    reg = create_builtin_registry(database_url=db_url)

    help_cmd = reg.get("help")
    assert help_cmd is not None

    out = asyncio.run(
        help_cmd.handler(
            CommandContext(chat_id=1, user_id=1, role=Role.OWNER, command_name="help", argument=None)
        )
    )
    assert out is not None
    assert "/webui" in out
