from __future__ import annotations

import asyncio

from sqlalchemy import select

from amo_bot.auth.roles import Role
from amo_bot.db.base import create_session_factory
from amo_bot.db.init_db import init_db
from amo_bot.db.models import DbRole, User
from amo_bot.telegram.commands import create_builtin_registry
from amo_bot.telegram.dispatcher import Dispatcher
from amo_bot.telegram.role_resolver import DBRoleResolver


class CapturingSender:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str, int | None]] = []

    async def send_text(self, chat_id: int, text: str, message_thread_id: int | None = None) -> object:
        self.sent.append((chat_id, text, message_thread_id))
        return {"ok": True}


def _mk_update(*, uid: int, chat_id: int, text: str, chat_type: str = "private", update_id: int = 1) -> dict[str, object]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id + 100,
            "from": {"id": uid, "is_bot": False, "first_name": "U", "username": f"u{uid}"},
            "chat": {"id": chat_id, "type": chat_type},
            "text": text,
        },
    }


def _mk_bot_update(*, uid: int, chat_id: int, text: str, chat_type: str = "private", update_id: int = 1) -> dict[str, object]:
    update = _mk_update(uid=uid, chat_id=chat_id, text=text, chat_type=chat_type, update_id=update_id)
    update["message"]["from"]["is_bot"] = True  # type: ignore[index]
    return update


def _bootstrap_dispatcher(db_url: str) -> tuple[Dispatcher, CapturingSender]:
    init_db(db_url)
    sf = create_session_factory(db_url)
    reg = create_builtin_registry(database_url=db_url)
    sender = CapturingSender()
    dispatcher = Dispatcher(
        command_registry=reg,
        role_resolver=DBRoleResolver(sf),
        send_text=sender.send_text,
        bot_username="AmoBot",
        database_url=db_url,
    )
    return dispatcher, sender


def _seed_user(db_url: str, *, user_id: int, role: str, consent: str) -> None:
    sf = create_session_factory(db_url)
    with sf() as session:
        role_map = {row.name: row.id for row in session.scalars(select(DbRole)).all()}
        session.add(User(telegram_user_id=user_id, role_id=role_map[role], consent_status=consent))
        session.commit()


def test_pending_human_normal_can_use_bot_without_accept(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'consent_gate_pending.db'}"
    dispatcher, sender = _bootstrap_dispatcher(db_url)
    _seed_user(db_url, user_id=1101, role="normal", consent="pending")

    asyncio.run(dispatcher.handle_raw_update(_mk_update(uid=1101, chat_id=1101, text="/ping", update_id=1)))
    assert sender.sent[-1] == (1101, "pong", None)


def test_declined_human_normal_can_use_bot_without_reactivation(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'consent_gate_declined.db'}"
    dispatcher, sender = _bootstrap_dispatcher(db_url)
    _seed_user(db_url, user_id=1102, role="normal", consent="declined")

    asyncio.run(dispatcher.handle_raw_update(_mk_update(uid=1102, chat_id=1102, text="/help", update_id=1)))
    assert sender.sent[-1][1].startswith("Verfügbare Befehle:")


def test_unreachable_human_normal_can_use_bot_without_private_start(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'consent_gate_unreachable.db'}"
    dispatcher, sender = _bootstrap_dispatcher(db_url)
    _seed_user(db_url, user_id=1103, role="normal", consent="unreachable")

    asyncio.run(dispatcher.handle_raw_update(_mk_update(uid=1103, chat_id=1103, text="/ping", update_id=1)))
    assert sender.sent[-1] == (1103, "pong", None)


def test_accepted_allows_normal_usage(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'consent_gate_accepted.db'}"
    dispatcher, sender = _bootstrap_dispatcher(db_url)
    _seed_user(db_url, user_id=1104, role="normal", consent="accepted")

    asyncio.run(dispatcher.handle_raw_update(_mk_update(uid=1104, chat_id=1104, text="/ping", update_id=1)))
    assert sender.sent[-1] == (1104, "pong", None)


def test_owner_pending_bypasses_consent_gate(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'consent_gate_owner_bypass.db'}"
    dispatcher, sender = _bootstrap_dispatcher(db_url)
    _seed_user(db_url, user_id=1105, role="owner", consent="pending")

    asyncio.run(dispatcher.handle_raw_update(_mk_update(uid=1105, chat_id=1105, text="/ping", update_id=1)))
    assert sender.sent[-1] == (1105, "pong", None)


def test_global_ignore_stays_blocked_even_when_owner_or_accepted(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'consent_gate_ignore.db'}"
    dispatcher, sender = _bootstrap_dispatcher(db_url)
    _seed_user(db_url, user_id=1106, role="ignore", consent="accepted")

    asyncio.run(dispatcher.handle_raw_update(_mk_update(uid=1106, chat_id=1106, text="/ping", update_id=1)))
    assert sender.sent == []


def test_group_human_normal_with_declined_consent_is_not_privacy_blocked(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'consent_gate_group_privacy.db'}"
    dispatcher, sender = _bootstrap_dispatcher(db_url)
    _seed_user(db_url, user_id=1107, role="normal", consent="declined")

    asyncio.run(dispatcher.handle_raw_update(_mk_update(uid=1107, chat_id=-1107, chat_type="group", text="/ping", update_id=1)))
    assert sender.sent[-1] == (-1107, "pong", None)


def test_bot_messages_do_not_trigger_runtime_gate(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'consent_gate_bot_message.db'}"
    dispatcher, sender = _bootstrap_dispatcher(db_url)
    _seed_user(db_url, user_id=1108, role="normal", consent="pending")

    asyncio.run(dispatcher.handle_raw_update(_mk_bot_update(uid=1108, chat_id=1108, text="/ping", update_id=1)))
    assert sender.sent == []
