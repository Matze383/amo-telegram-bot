from __future__ import annotations

import asyncio

from sqlalchemy import select

from amo_bot.db.models import User

from amo_bot.auth.roles import Role
from amo_bot.db.base import create_session_factory
from amo_bot.db.init_db import init_db
from amo_bot.db.models import TelegramChat, TelegramTopic
from amo_bot.telegram.commands import create_builtin_registry
from amo_bot.telegram.dispatcher import Dispatcher
from amo_bot.telegram.role_resolver import InMemoryRoleResolver
from amo_bot.telegram.chat_topic_persistence import ChatTopicPersistenceService


def _mk_update(
    *,
    update_id: int,
    user_id: int,
    chat_id: int,
    chat_type: str,
    title: str | None = None,
    username: str | None = None,
    first_name: str = "U",
    last_name: str | None = None,
    message_thread_id: int | None = None,
    forum_topic_created_name: str | None = None,
    forum_topic_edited_name: str | None = None,
    text: str | None = "/ping",
) -> dict[str, object]:
    chat: dict[str, object] = {"id": chat_id, "type": chat_type}
    if title is not None:
        chat["title"] = title
    if username is not None:
        chat["username"] = username

    message: dict[str, object] = {
        "message_id": update_id + 100,
        "from": {"id": user_id, "is_bot": False, "first_name": first_name, "username": f"u{user_id}"},
        "chat": chat,
    }
    if text is not None:
        message["text"] = text
    if last_name is not None:
        message["from"]["last_name"] = last_name
    if message_thread_id is not None:
        message["message_thread_id"] = message_thread_id
    if forum_topic_created_name is not None:
        message["forum_topic_created"] = {"name": forum_topic_created_name}
    if forum_topic_edited_name is not None:
        message["forum_topic_edited"] = {"name": forum_topic_edited_name}

    return {"update_id": update_id, "message": message}


def _build_dispatcher(db_url: str) -> Dispatcher:
    sf = create_session_factory(db_url)

    async def _fake_send(_chat_id: int, _text: str, _message_thread_id: int | None = None) -> object:
        return {"ok": True}

    return Dispatcher(
        command_registry=create_builtin_registry(),
        role_resolver=InMemoryRoleResolver({42: Role.NORMAL}),
        send_text=_fake_send,
        bot_username="AmoBot",
        message_persistence=ChatTopicPersistenceService(sf),
    )


def test_message_from_private_chat_persists_chat(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'persist_private.db'}"
    init_db(db_url)
    dispatcher = _build_dispatcher(db_url)

    asyncio.run(
        dispatcher.handle_raw_update(
            _mk_update(update_id=1, user_id=42, chat_id=111, chat_type="private", username="private_name")
        )
    )

    sf = create_session_factory(db_url)
    with sf() as session:
        row = session.scalar(select(TelegramChat).where(TelegramChat.chat_id == 111))
        assert row is not None
        assert row.chat_type == "private"
        assert row.title is None
        assert row.username == "private_name"


def test_message_from_supergroup_persists_chat_title(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'persist_group.db'}"
    init_db(db_url)
    dispatcher = _build_dispatcher(db_url)

    asyncio.run(
        dispatcher.handle_raw_update(
            _mk_update(
                update_id=2,
                user_id=42,
                chat_id=-100123,
                chat_type="supergroup",
                title="My Group",
                username="mygroup",
            )
        )
    )

    sf = create_session_factory(db_url)
    with sf() as session:
        row = session.scalar(select(TelegramChat).where(TelegramChat.chat_id == -100123))
        assert row is not None
        assert row.chat_type == "supergroup"
        assert row.title == "My Group"
        assert row.username == "mygroup"


def test_message_with_thread_id_persists_topic(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'persist_topic.db'}"
    init_db(db_url)
    dispatcher = _build_dispatcher(db_url)

    asyncio.run(
        dispatcher.handle_raw_update(
            _mk_update(
                update_id=3,
                user_id=42,
                chat_id=-100777,
                chat_type="supergroup",
                title="Forum Group",
                message_thread_id=55,
            )
        )
    )

    sf = create_session_factory(db_url)
    with sf() as session:
        topic = session.scalar(
            select(TelegramTopic).where(
                TelegramTopic.chat_id == -100777,
                TelegramTopic.message_thread_id == 55,
            )
        )
        assert topic is not None
        assert topic.telegram_topic_name is None


def test_message_without_thread_id_creates_no_topic(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'persist_no_topic.db'}"
    init_db(db_url)
    dispatcher = _build_dispatcher(db_url)

    asyncio.run(
        dispatcher.handle_raw_update(
            _mk_update(update_id=4, user_id=42, chat_id=-100888, chat_type="group", title="Plain Group")
        )
    )

    sf = create_session_factory(db_url)
    with sf() as session:
        topics = session.scalars(select(TelegramTopic).where(TelegramTopic.chat_id == -100888)).all()
        assert topics == []


def test_new_message_auto_discovers_user_with_default_normal_role(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'persist_user_discovery_new.db'}"
    init_db(db_url)
    dispatcher = _build_dispatcher(db_url)

    asyncio.run(
        dispatcher.handle_raw_update(
            _mk_update(update_id=20, user_id=4242, chat_id=500, chat_type="private", first_name="Alice", last_name="A")
        )
    )

    sf = create_session_factory(db_url)
    with sf() as session:
        user = session.scalar(select(User).where(User.telegram_user_id == 4242))
        assert user is not None
        assert user.role.name == "normal"
        assert user.username == "u4242"
        assert user.first_name == "Alice"
        assert user.last_name == "A"
        assert user.first_seen_at is not None
        assert user.last_seen_at is not None


def test_followup_message_updates_profile_and_last_seen_without_overwriting_role(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'persist_user_discovery_update.db'}"
    init_db(db_url)
    dispatcher = _build_dispatcher(db_url)

    asyncio.run(
        dispatcher.handle_raw_update(
            _mk_update(update_id=21, user_id=4343, chat_id=501, chat_type="private", first_name="Old", last_name="Name")
        )
    )

    sf = create_session_factory(db_url)
    with sf() as session:
        from amo_bot.db.repositories import UserRoleRepository

        repo = UserRoleRepository(session)
        repo.set_user_role(actor_telegram_user_id=1, target_telegram_user_id=4343, role=Role.VIP)

    with sf() as session:
        before = session.scalar(select(User).where(User.telegram_user_id == 4343))
        assert before is not None
        before_seen = before.last_seen_at

    asyncio.run(
        dispatcher.handle_raw_update(
            _mk_update(update_id=22, user_id=4343, chat_id=501, chat_type="private", first_name="New", last_name="Surname")
        )
    )

    with sf() as session:
        after = session.scalar(select(User).where(User.telegram_user_id == 4343))
        assert after is not None
        assert after.role.name == "vip"
        assert after.first_name == "New"
        assert after.last_name == "Surname"
        assert after.last_seen_at is not None
        assert before_seen is not None
        assert after.last_seen_at >= before_seen


def test_topic_name_is_saved_and_preserved_and_updated(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'persist_topic_name_lifecycle.db'}"
    init_db(db_url)
    dispatcher = _build_dispatcher(db_url)

    asyncio.run(
        dispatcher.handle_raw_update(
            _mk_update(
                update_id=10,
                user_id=42,
                chat_id=-100999,
                chat_type="supergroup",
                title="Forum",
                message_thread_id=123,
                forum_topic_created_name="Initial Topic",
            )
        )
    )

    asyncio.run(
        dispatcher.handle_raw_update(
            _mk_update(
                update_id=11,
                user_id=42,
                chat_id=-100999,
                chat_type="supergroup",
                title="Forum",
                message_thread_id=123,
            )
        )
    )

    asyncio.run(
        dispatcher.handle_raw_update(
            _mk_update(
                update_id=12,
                user_id=42,
                chat_id=-100999,
                chat_type="supergroup",
                title="Forum",
                message_thread_id=123,
                forum_topic_created_name="Renamed Topic",
            )
        )
    )

    sf = create_session_factory(db_url)
    with sf() as session:
        topic = session.scalar(
            select(TelegramTopic).where(
                TelegramTopic.chat_id == -100999,
                TelegramTopic.message_thread_id == 123,
            )
        )
        assert topic is not None
        assert topic.telegram_topic_name == "Renamed Topic"


def test_forum_topic_edited_name_is_persisted_without_text_message(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'persist_topic_edited_service.db'}"
    init_db(db_url)
    dispatcher = _build_dispatcher(db_url)

    asyncio.run(
        dispatcher.handle_raw_update(
            _mk_update(
                update_id=30,
                user_id=42,
                chat_id=-100321,
                chat_type="supergroup",
                title="Forum",
                message_thread_id=872,
                forum_topic_edited_name="Projekt: Telegram-bot",
                text=None,
            )
        )
    )

    sf = create_session_factory(db_url)
    with sf() as session:
        topic = session.scalar(
            select(TelegramTopic).where(
                TelegramTopic.chat_id == -100321,
                TelegramTopic.message_thread_id == 872,
            )
        )
        assert topic is not None
        assert topic.telegram_topic_name == "Projekt: Telegram-bot"


def test_reply_to_forum_topic_created_name_is_persisted(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'persist_topic_reply_created.db'}"
    init_db(db_url)
    dispatcher = _build_dispatcher(db_url)

    update = _mk_update(
        update_id=40,
        user_id=42,
        chat_id=-100654,
        chat_type="supergroup",
        title="Forum",
        message_thread_id=951,
    )
    message = update["message"]
    assert isinstance(message, dict)
    message["reply_to_message"] = {"forum_topic_created": {"name": "Reply Created Topic"}}

    asyncio.run(dispatcher.handle_raw_update(update))

    sf = create_session_factory(db_url)
    with sf() as session:
        topic = session.scalar(
            select(TelegramTopic).where(
                TelegramTopic.chat_id == -100654,
                TelegramTopic.message_thread_id == 951,
            )
        )
        assert topic is not None
        assert topic.telegram_topic_name == "Reply Created Topic"


def test_reply_to_forum_topic_edited_name_is_persisted(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'persist_topic_reply_edited.db'}"
    init_db(db_url)
    dispatcher = _build_dispatcher(db_url)

    update = _mk_update(
        update_id=41,
        user_id=42,
        chat_id=-100655,
        chat_type="supergroup",
        title="Forum",
        message_thread_id=952,
    )
    message = update["message"]
    assert isinstance(message, dict)
    message["reply_to_message"] = {"forum_topic_edited": {"name": "Reply Edited Topic"}}

    asyncio.run(dispatcher.handle_raw_update(update))

    sf = create_session_factory(db_url)
    with sf() as session:
        topic = session.scalar(
            select(TelegramTopic).where(
                TelegramTopic.chat_id == -100655,
                TelegramTopic.message_thread_id == 952,
            )
        )
        assert topic is not None
        assert topic.telegram_topic_name == "Reply Edited Topic"
