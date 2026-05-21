from __future__ import annotations

import asyncio

from sqlalchemy import select

from amo_bot.db.models import User

from amo_bot.auth.roles import Role
from amo_bot.db.base import create_session_factory
from amo_bot.db.init_db import init_db
from amo_bot.db.models import TelegramChat, TelegramTopic, TopicRecentMessage
from amo_bot.telegram.commands import create_builtin_registry
from amo_bot.telegram.dispatcher import Dispatcher
from amo_bot.telegram.role_resolver import InMemoryRoleResolver
from amo_bot.telegram.chat_topic_persistence import ChatTopicPersistenceService


def _recent_messages_for_scope(
    db_url: str,
    *,
    scope_type: str,
    chat_id: int | None = None,
    topic_id: int | None = None,
    user_id: int | None = None,
) -> list[TopicRecentMessage]:
    sf = create_session_factory(db_url)
    with sf() as session:
        return session.scalars(
            select(TopicRecentMessage).where(
                TopicRecentMessage.scope_type == scope_type,
                TopicRecentMessage.chat_id == chat_id,
                TopicRecentMessage.topic_id == topic_id,
                TopicRecentMessage.user_id == user_id,
            )
        ).all()


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


def _build_dispatcher(
    db_url: str,
    sent_private: list[tuple[int, str]] | None = None,
    private_send_error: Exception | None = None,
    sent_group_markup: list[tuple[int, str, dict[str, object], int | None]] | None = None,
    sent_group_text: list[tuple[int, str, int | None]] | None = None,
    bot_username: str | None = "AmoBot",
) -> Dispatcher:
    sf = create_session_factory(db_url)

    async def _fake_send(_chat_id: int, _text: str, _message_thread_id: int | None = None) -> object:
        if sent_group_text is not None:
            sent_group_text.append((_chat_id, _text, _message_thread_id))
        return {"ok": True}

    async def _fake_send_markup(_chat_id: int, _text: str, _reply_markup: dict[str, object], _message_thread_id: int | None = None) -> object:
        if sent_group_markup is not None:
            sent_group_markup.append((_chat_id, _text, _reply_markup, _message_thread_id))
        return {"ok": True}

    async def _fake_send_private(chat_id: int, text: str) -> object:
        if private_send_error is not None:
            raise private_send_error
        if sent_private is not None:
            sent_private.append((chat_id, text))
        return {"ok": True}

    return Dispatcher(
        command_registry=create_builtin_registry(database_url=db_url),
        role_resolver=InMemoryRoleResolver({42: Role.NORMAL}),
        send_text=_fake_send,
        send_markup=_fake_send_markup,
        bot_username=bot_username,
        database_url=db_url,
        message_persistence=ChatTopicPersistenceService(
            sf,
            send_private_message=_fake_send_private,
            send_group_markup=_fake_send_markup,
            send_group_text=_fake_send,
            bot_username=bot_username,
        ),
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
        assert user.consent_status == "pending"


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
        assert after.consent_status == "pending"


def test_followup_message_keeps_declined_and_unreachable_consent_status(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'persist_user_discovery_consent_preserve.db'}"
    init_db(db_url)
    dispatcher = _build_dispatcher(db_url)

    sf = create_session_factory(db_url)
    with sf() as session:
        from amo_bot.db.repositories import UserRoleRepository

        repo = UserRoleRepository(session)
        repo.upsert_discovered_user(
            telegram_user_id=4545,
            username="u4545",
            first_name="Declined",
            last_name="User",
        )

    with sf() as session:
        declined = session.scalar(select(User).where(User.telegram_user_id == 4545))
        assert declined is not None
        declined.consent_status = "declined"
        session.commit()

    asyncio.run(
        dispatcher.handle_raw_update(
            _mk_update(update_id=23, user_id=4545, chat_id=502, chat_type="private", first_name="Declined", last_name="Again")
        )
    )

    with sf() as session:
        declined_after = session.scalar(select(User).where(User.telegram_user_id == 4545))
        assert declined_after is not None
        assert declined_after.consent_status == "declined"

    with sf() as session:
        from amo_bot.db.repositories import UserRoleRepository

        repo = UserRoleRepository(session)
        repo.upsert_discovered_user(
            telegram_user_id=4646,
            username="u4646",
            first_name="Unreachable",
            last_name="User",
        )

    with sf() as session:
        unreachable = session.scalar(select(User).where(User.telegram_user_id == 4646))
        assert unreachable is not None
        unreachable.consent_status = "unreachable"
        session.commit()

    asyncio.run(
        dispatcher.handle_raw_update(
            _mk_update(update_id=24, user_id=4646, chat_id=503, chat_type="private", first_name="Unreachable", last_name="Again")
        )
    )

    with sf() as session:
        unreachable_after = session.scalar(select(User).where(User.telegram_user_id == 4646))
        assert unreachable_after is not None
        assert unreachable_after.consent_status == "unreachable"




def test_discovery_pending_user_triggers_private_consent_prompt(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'persist_user_prompt.db'}"
    init_db(db_url)
    sent_private: list[tuple[int, str]] = []
    dispatcher = _build_dispatcher(db_url, sent_private=sent_private)

    asyncio.run(
        dispatcher.handle_raw_update(
            _mk_update(update_id=30, user_id=6001, chat_id=-10001, chat_type="supergroup", title="G")
        )
    )

    assert len(sent_private) == 1
    assert sent_private[0][0] == 6001
    assert "/accept" in sent_private[0][1]

    sf = create_session_factory(db_url)
    with sf() as session:
        user = session.scalar(select(User).where(User.telegram_user_id == 6001))
        assert user is not None
        assert user.consent_status == "pending"
        assert user.consent_prompt_count == 1
        assert user.consent_prompted_at is not None


def test_discovery_forbidden_private_dm_marks_unreachable(tmp_path) -> None:
    from amo_bot.telegram.client import TelegramApiError
    from amo_bot.telegram.owner_notify import OwnerNotifier

    db_url = f"sqlite:///{tmp_path / 'persist_user_unreachable.db'}"
    init_db(db_url)
    sent_owner: list[tuple[int, str]] = []

    async def _owner_send(chat_id: int, text: str) -> object:
        sent_owner.append((chat_id, text))
        return {"ok": True}

    sf = create_session_factory(db_url)

    async def _fake_send(_chat_id: int, _text: str, _message_thread_id: int | None = None) -> object:
        return {"ok": True}

    async def _fake_send_private(_chat_id: int, _text: str) -> object:
        raise TelegramApiError("Forbidden: bot can't initiate conversation with a user")

    dispatcher = Dispatcher(
        command_registry=create_builtin_registry(),
        role_resolver=InMemoryRoleResolver({42: Role.NORMAL}),
        send_text=_fake_send,
        bot_username="AmoBot",
        message_persistence=ChatTopicPersistenceService(
            sf,
            send_private_message=_fake_send_private,
            owner_notifier=OwnerNotifier(owner_telegram_user_id=9999, send_private_text=_owner_send),
        ),
    )

    asyncio.run(
        dispatcher.handle_raw_update(
            _mk_update(update_id=31, user_id=6002, chat_id=-10002, chat_type="supergroup", title="G")
        )
    )

    sf = create_session_factory(db_url)
    with sf() as session:
        user = session.scalar(select(User).where(User.telegram_user_id == 6002))
        assert user is not None
        assert user.consent_status == "unreachable"
        assert user.consent_prompt_count == 0
        assert user.consent_prompted_at is None

    assert len(sent_owner) == 2
    assert sent_owner[0][0] == 9999
    assert "Neuer User erfasst" in sent_owner[0][1]
    assert sent_owner[1][0] == 9999
    assert "Policy-DM nicht zustellbar" in sent_owner[1][1]
    assert "/start" in sent_owner[1][1]


def test_discovery_prompts_only_once_for_pending_user(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'persist_user_prompt_rate_limited.db'}"
    init_db(db_url)
    sent_private: list[tuple[int, str]] = []
    dispatcher = _build_dispatcher(db_url, sent_private=sent_private)

    asyncio.run(
        dispatcher.handle_raw_update(
            _mk_update(update_id=32, user_id=6003, chat_id=-10003, chat_type="supergroup", title="G")
        )
    )
    asyncio.run(
        dispatcher.handle_raw_update(
            _mk_update(update_id=33, user_id=6003, chat_id=-10003, chat_type="supergroup", title="G")
        )
    )

    assert len(sent_private) == 1


def test_discovery_prompt_skips_user_with_existing_prompt_count(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'persist_user_prompt_max_count.db'}"
    init_db(db_url)
    sent_private: list[tuple[int, str]] = []
    dispatcher = _build_dispatcher(db_url, sent_private=sent_private)

    sf = create_session_factory(db_url)
    with sf() as session:
        from datetime import datetime, timezone

        from amo_bot.db.repositories import UserRoleRepository

        user = UserRoleRepository(session).upsert_discovered_user(
            telegram_user_id=6004,
            username="u6004",
            first_name="U",
            last_name=None,
        )
        user.consent_prompt_count = 1
        user.consent_prompted_at = datetime.now(timezone.utc)
        session.commit()

    asyncio.run(
        dispatcher.handle_raw_update(
            _mk_update(update_id=34, user_id=6004, chat_id=-10004, chat_type="supergroup", title="G")
        )
    )

    assert sent_private == []

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

def test_new_user_discovery_notifies_owner_once(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'persist_owner_notify_once.db'}"
    init_db(db_url)
    sent_owner: list[tuple[int, str]] = []

    async def _owner_send(chat_id: int, text: str) -> object:
        sent_owner.append((chat_id, text))
        return {"ok": True}

    from amo_bot.telegram.owner_notify import OwnerNotifier
    from amo_bot.telegram.update_parser import parse_update

    sf = create_session_factory(db_url)
    service = ChatTopicPersistenceService(
        sf,
        send_private_message=None,
        owner_notifier=OwnerNotifier(owner_telegram_user_id=9999, send_private_text=_owner_send),
    )

    msg1 = parse_update(_mk_update(update_id=901, user_id=6001, chat_id=-1005, chat_type="group", title="G"))
    msg2 = parse_update(_mk_update(update_id=902, user_id=6001, chat_id=-1005, chat_type="group", title="G"))
    assert msg1 is not None and msg1.message is not None
    assert msg2 is not None and msg2.message is not None

    asyncio.run(service.persist_message(msg1.message))
    asyncio.run(service.persist_message(msg2.message))

    assert len(sent_owner) == 1
    assert sent_owner[0][0] == 9999
    assert "Neuer User erfasst" in sent_owner[0][1]


def test_repeated_message_from_unreachable_user_does_not_notify_owner_again(tmp_path) -> None:
    from amo_bot.telegram.client import TelegramApiError
    from amo_bot.telegram.owner_notify import OwnerNotifier

    db_url = f"sqlite:///{tmp_path / 'persist_owner_notify_unreachable_once.db'}"
    init_db(db_url)
    sent_owner: list[tuple[int, str]] = []

    async def _owner_send(chat_id: int, text: str) -> object:
        sent_owner.append((chat_id, text))
        return {"ok": True}

    sf = create_session_factory(db_url)

    async def _fake_send(_chat_id: int, _text: str, _message_thread_id: int | None = None) -> object:
        return {"ok": True}

    async def _fake_send_private(_chat_id: int, _text: str) -> object:
        raise TelegramApiError("Forbidden: bot can't initiate conversation with a user")

    dispatcher = Dispatcher(
        command_registry=create_builtin_registry(),
        role_resolver=InMemoryRoleResolver({42: Role.NORMAL}),
        send_text=_fake_send,
        bot_username="AmoBot",
        message_persistence=ChatTopicPersistenceService(
            sf,
            send_private_message=_fake_send_private,
            owner_notifier=OwnerNotifier(owner_telegram_user_id=9999, send_private_text=_owner_send),
        ),
    )

    asyncio.run(
        dispatcher.handle_raw_update(
            _mk_update(update_id=904, user_id=7001, chat_id=-1006, chat_type="supergroup", title="G")
        )
    )
    asyncio.run(
        dispatcher.handle_raw_update(
            _mk_update(update_id=905, user_id=7001, chat_id=-1006, chat_type="supergroup", title="G")
        )
    )

    unreachable_notifies = [text for _, text in sent_owner if "Policy-DM nicht zustellbar" in text]
    fallback_notifies = [text for _, text in sent_owner if "Gruppenfallback für Consent gesendet" in text]
    prompted_notifies = [text for _, text in sent_owner if "Policy-DM erfolgreich gesendet" in text]
    assert len(unreachable_notifies) == 1
    assert len(fallback_notifies) == 0
    assert len(prompted_notifies) == 0


def test_owner_unreachable_notify_failure_does_not_break_persistence(tmp_path) -> None:
    from amo_bot.telegram.client import TelegramApiError
    from amo_bot.telegram.owner_notify import OwnerNotifier

    db_url = f"sqlite:///{tmp_path / 'persist_owner_unreachable_notify_fail.db'}"
    init_db(db_url)

    async def _owner_send(_chat_id: int, text: str) -> object:
        if "Policy-DM nicht zustellbar" in text:
            raise RuntimeError("boom")
        return {"ok": True}

    sf = create_session_factory(db_url)

    async def _fake_send(_chat_id: int, _text: str, _message_thread_id: int | None = None) -> object:
        return {"ok": True}

    async def _fake_send_private(_chat_id: int, _text: str) -> object:
        raise TelegramApiError("Forbidden: bot can't initiate conversation with a user")

    dispatcher = Dispatcher(
        command_registry=create_builtin_registry(),
        role_resolver=InMemoryRoleResolver({42: Role.NORMAL}),
        send_text=_fake_send,
        bot_username="AmoBot",
        message_persistence=ChatTopicPersistenceService(
            sf,
            send_private_message=_fake_send_private,
            owner_notifier=OwnerNotifier(owner_telegram_user_id=9999, send_private_text=_owner_send),
        ),
    )

    asyncio.run(
        dispatcher.handle_raw_update(
            _mk_update(update_id=906, user_id=7002, chat_id=-1007, chat_type="supergroup", title="G")
        )
    )

    with sf() as session:
        user = session.scalar(select(User).where(User.telegram_user_id == 7002))
        assert user is not None
        assert user.consent_status == "unreachable"


def test_owner_notify_failure_does_not_break_persistence(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'persist_owner_notify_fail.db'}"
    init_db(db_url)

    async def _owner_send(_chat_id: int, _text: str) -> object:
        raise RuntimeError("boom")

    from amo_bot.telegram.owner_notify import OwnerNotifier
    from amo_bot.telegram.update_parser import parse_update

    sf = create_session_factory(db_url)
    service = ChatTopicPersistenceService(
        sf,
        send_private_message=None,
        owner_notifier=OwnerNotifier(owner_telegram_user_id=9999, send_private_text=_owner_send),
    )

    msg = parse_update(_mk_update(update_id=903, user_id=6002, chat_id=6002, chat_type="private"))
    assert msg is not None and msg.message is not None
    asyncio.run(service.persist_message(msg.message))

    with sf() as session:
        user = session.scalar(select(User).where(User.telegram_user_id == 6002))
        assert user is not None


def test_forbidden_dm_group_fallback_contains_button_url_and_preserves_thread(tmp_path) -> None:
    from amo_bot.telegram.client import TelegramApiError

    db_url = f"sqlite:///{tmp_path / 'persist_fallback_button_thread.db'}"
    init_db(db_url)
    sent_group_markup: list[tuple[int, str, dict[str, object], int | None]] = []
    sent_group_text: list[tuple[int, str, int | None]] = []

    dispatcher = _build_dispatcher(
        db_url,
        sent_group_markup=sent_group_markup,
        sent_group_text=sent_group_text,
        private_send_error=TelegramApiError("Forbidden: bot can't initiate conversation with a user"),
        bot_username="AmoBot",
    )

    asyncio.run(
        dispatcher.handle_raw_update(
            _mk_update(
                update_id=100,
                user_id=8101,
                chat_id=-108101,
                chat_type="supergroup",
                title="G",
                username="grp",
                message_thread_id=872,
            )
        )
    )

    consent_block_msgs = [m for m in sent_group_text if m[1] == "Bitte kläre Consent privat mit dem Bot."]
    assert len(consent_block_msgs) == 1
    assert consent_block_msgs[0][2] == 872

    assert len(sent_group_markup) == 1
    chat_id, text, markup, thread_id = sent_group_markup[0]
    assert chat_id == -108101
    assert thread_id == 872
    assert text == (
        "Willkommen @u8101 in G. "
        "Ich bin der KI-Bot der Gruppe. "
        "Damit du mich nutzen kannst und ich mit dir interagieren kann, "
        "musst du den Nutzungsbedingungen zustimmen."
    )
    button = markup["inline_keyboard"][0][0]
    assert button["text"] == "Policy privat öffnen"
    assert button["url"] == "https://t.me/AmoBot?start=consent"
    assert "/start" not in text


def test_forbidden_dm_without_bot_username_uses_text_only_fallback(tmp_path) -> None:
    from amo_bot.telegram.client import TelegramApiError

    db_url = f"sqlite:///{tmp_path / 'persist_fallback_text_only.db'}"
    init_db(db_url)
    sent_group_markup: list[tuple[int, str, dict[str, object], int | None]] = []
    sent_group_text: list[tuple[int, str, int | None]] = []

    dispatcher = _build_dispatcher(
        db_url,
        sent_group_markup=sent_group_markup,
        sent_group_text=sent_group_text,
        private_send_error=TelegramApiError("Forbidden: bot can't initiate conversation with a user"),
        bot_username=None,
    )

    asyncio.run(
        dispatcher.handle_raw_update(
            _mk_update(update_id=101, user_id=8102, chat_id=-108102, chat_type="supergroup", title="G")
        )
    )

    assert sent_group_markup == []
    fallback_msgs = [
        m
        for m in sent_group_text
        if m[1]
        == (
            "Willkommen @u8102 in G. "
            "Ich bin der KI-Bot der Gruppe. "
            "Damit du mich nutzen kannst und ich mit dir interagieren kann, "
            "musst du den Nutzungsbedingungen zustimmen."
        )
    ]
    assert len(fallback_msgs) == 1
    block_msgs = [m for m in sent_group_text if m[1] == "Bitte kläre Consent privat mit dem Bot."]
    assert len(block_msgs) == 1


def test_forbidden_dm_existing_group_user_sends_no_group_fallback(tmp_path) -> None:
    from amo_bot.telegram.client import TelegramApiError

    db_url = f"sqlite:///{tmp_path / 'persist_existing_user_no_group_fallback.db'}"
    init_db(db_url)
    sent_group_markup: list[tuple[int, str, dict[str, object], int | None]] = []
    sent_group_text: list[tuple[int, str, int | None]] = []

    dispatcher = _build_dispatcher(
        db_url,
        sent_group_markup=sent_group_markup,
        sent_group_text=sent_group_text,
        private_send_error=TelegramApiError("Forbidden: bot can't initiate conversation with a user"),
        bot_username="AmoBot",
    )

    update = _mk_update(update_id=101, user_id=8102, chat_id=-108102, chat_type="supergroup", title="G")
    asyncio.run(dispatcher.handle_raw_update(update))
    asyncio.run(dispatcher.handle_raw_update(update))

    fallback_msgs = [
        m
        for m in sent_group_markup
        if m[1].startswith("Willkommen") and "Nutzungsbedingungen" in m[1]
    ]
    consent_block_msgs = [m for m in sent_group_text if m[1] == "Bitte kläre Consent privat mit dem Bot."]

    assert len(fallback_msgs) == 1
    assert len(consent_block_msgs) == 2
    assert len(sent_group_markup) == 1



def test_discovery_chat_not_found_private_dm_marks_unreachable_without_persistence_error(tmp_path, caplog) -> None:
    from amo_bot.telegram.client import TelegramApiError

    db_url = f"sqlite:///{tmp_path / 'persist_chat_not_found_unreachable.db'}"
    init_db(db_url)
    sent_group_markup: list[tuple[int, str, dict[str, object], int | None]] = []
    sent_group_text: list[tuple[int, str, int | None]] = []

    dispatcher = _build_dispatcher(
        db_url,
        sent_group_markup=sent_group_markup,
        sent_group_text=sent_group_text,
        private_send_error=TelegramApiError(
            'HTTP 400: {"ok":false,"error_code":400,"description":"Bad Request: chat not found"}'
        ),
        bot_username="AmoBot",
    )

    with caplog.at_level("ERROR"):
        asyncio.run(
            dispatcher.handle_raw_update(
                _mk_update(update_id=105, user_id=8106, chat_id=-108106, chat_type="supergroup", title="G")
            )
        )

    assert "Failed to persist Telegram message" not in caplog.text

    sf = create_session_factory(db_url)
    with sf() as session:
        user = session.scalar(select(User).where(User.telegram_user_id == 8106))
        assert user is not None
        assert user.consent_status == "unreachable"
        assert user.consent_prompt_count == 0
        assert user.consent_prompted_at is None

    assert len(sent_group_markup) == 1
    assert sent_group_markup[0][0] == -108106
    assert sent_group_markup[0][3] is None
    block_msgs = [m for m in sent_group_text if m[1] == "Bitte kläre Consent privat mit dem Bot."]
    assert len(block_msgs) == 1

def test_dm_success_sends_no_group_fallback(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'persist_no_group_fallback_on_dm_success.db'}"
    init_db(db_url)
    sent_group_markup: list[tuple[int, str, dict[str, object], int | None]] = []
    sent_group_text: list[tuple[int, str, int | None]] = []
    sent_private: list[tuple[int, str]] = []

    dispatcher = _build_dispatcher(
        db_url,
        sent_group_markup=sent_group_markup,
        sent_group_text=sent_group_text,
        sent_private=sent_private,
        bot_username="AmoBot",
    )

    asyncio.run(
        dispatcher.handle_raw_update(
            _mk_update(update_id=102, user_id=8103, chat_id=-108103, chat_type="supergroup", title="G")
        )
    )

    assert len(sent_private) == 1
    assert sent_group_markup == []
    block_msgs = [m for m in sent_group_text if m[1] == "Bitte kläre Consent privat mit dem Bot."]
    assert len(block_msgs) == 1


def test_new_user_dm_success_notifies_owner_about_prompt_delivery(tmp_path) -> None:
    from amo_bot.telegram.owner_notify import OwnerNotifier

    db_url = f"sqlite:///{tmp_path / 'persist_owner_notify_prompt_sent.db'}"
    init_db(db_url)
    sent_owner: list[tuple[int, str]] = []

    async def _owner_send(chat_id: int, text: str) -> object:
        sent_owner.append((chat_id, text))
        return {"ok": True}

    sf = create_session_factory(db_url)

    async def _fake_send(_chat_id: int, _text: str, _message_thread_id: int | None = None) -> object:
        return {"ok": True}

    async def _fake_send_private(_chat_id: int, _text: str) -> object:
        return {"ok": True}

    dispatcher = Dispatcher(
        command_registry=create_builtin_registry(),
        role_resolver=InMemoryRoleResolver({42: Role.NORMAL}),
        send_text=_fake_send,
        bot_username="AmoBot",
        message_persistence=ChatTopicPersistenceService(
            sf,
            send_private_message=_fake_send_private,
            owner_notifier=OwnerNotifier(owner_telegram_user_id=9999, send_private_text=_owner_send),
        ),
    )

    asyncio.run(
        dispatcher.handle_raw_update(
            _mk_update(update_id=103, user_id=8104, chat_id=-108104, chat_type="supergroup", title="G")
        )
    )

    prompt_notifies = [text for _, text in sent_owner if "Policy-DM erfolgreich gesendet" in text]
    assert len(prompt_notifies) == 1


def test_topic_text_without_mention_or_reply_persists_recent_and_sends_no_ai_response(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'persist_recent_topic_no_trigger.db'}"
    init_db(db_url)
    sent_group_text: list[tuple[int, str, int | None]] = []
    dispatcher = _build_dispatcher(db_url, sent_group_text=sent_group_text)

    asyncio.run(
        dispatcher.handle_raw_update(
            _mk_update(
                update_id=200,
                user_id=9001,
                chat_id=-12001,
                chat_type="supergroup",
                title="Forum",
                message_thread_id=501,
                text="ein normaler text",
            )
        )
    )

    recent = _recent_messages_for_scope(db_url, scope_type="topic", chat_id=-12001, topic_id=501)
    assert len(recent) == 1
    assert recent[0].message_text == "ein normaler text"
    ai_replies = [m for m in sent_group_text if m[1] != "Bitte kläre Consent privat mit dem Bot."]
    assert ai_replies == []


def test_owner_topic_text_without_mention_or_reply_persists_and_sends_no_ai_response(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'persist_recent_topic_owner_no_trigger.db'}"
    init_db(db_url)
    sent_group_text: list[tuple[int, str, int | None]] = []
    dispatcher = _build_dispatcher(db_url, sent_group_text=sent_group_text)

    sf = create_session_factory(db_url)
    with sf() as session:
        from amo_bot.db.repositories import UserRoleRepository

        UserRoleRepository(session).set_user_role(actor_telegram_user_id=1, target_telegram_user_id=42, role=Role.OWNER)

    asyncio.run(
        dispatcher.handle_raw_update(
            _mk_update(
                update_id=201,
                user_id=42,
                chat_id=-12002,
                chat_type="supergroup",
                title="Forum",
                message_thread_id=502,
                text="owner text",
            )
        )
    )

    recent = _recent_messages_for_scope(db_url, scope_type="topic", chat_id=-12002, topic_id=502)
    assert len(recent) == 1
    assert recent[0].message_text == "owner text"
    ai_replies = [m for m in sent_group_text if m[1] != "Bitte kläre Consent privat mit dem Bot."]
    assert ai_replies == []


def test_mention_or_true_reply_triggers_and_persists_recent(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'persist_recent_triggered.db'}"
    init_db(db_url)
    dispatcher = _build_dispatcher(db_url, bot_username="AmoBot")

    mention_update = _mk_update(
        update_id=202,
        user_id=9101,
        chat_id=-12003,
        chat_type="supergroup",
        title="Forum",
        message_thread_id=503,
        text="@AmoBot sag hallo",
    )
    reply_update = _mk_update(
        update_id=203,
        user_id=9101,
        chat_id=-12003,
        chat_type="supergroup",
        title="Forum",
        message_thread_id=503,
        text="antwort bitte",
    )
    reply_message = reply_update["message"]
    assert isinstance(reply_message, dict)
    reply_message["reply_to_message"] = {"message_id": 999, "from": {"id": 777, "is_bot": True, "username": "AmoBot", "first_name": "Amo"}}

    asyncio.run(dispatcher.handle_raw_update(mention_update))
    asyncio.run(dispatcher.handle_raw_update(reply_update))

    recent = _recent_messages_for_scope(db_url, scope_type="topic", chat_id=-12003, topic_id=503)
    texts = [row.message_text for row in recent]
    assert "@AmoBot sag hallo" in texts
    assert "antwort bitte" in texts


def test_recent_scope_isolated_between_two_topics_and_group_root_and_command_skipped(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'persist_recent_scope_isolation.db'}"
    init_db(db_url)
    dispatcher = _build_dispatcher(db_url)

    asyncio.run(
        dispatcher.handle_raw_update(
            _mk_update(
                update_id=204,
                user_id=9201,
                chat_id=-12004,
                chat_type="supergroup",
                title="Forum",
                message_thread_id=601,
                text="topic eins",
            )
        )
    )
    asyncio.run(
        dispatcher.handle_raw_update(
            _mk_update(
                update_id=205,
                user_id=9202,
                chat_id=-12004,
                chat_type="supergroup",
                title="Forum",
                message_thread_id=602,
                text="topic zwei",
            )
        )
    )
    asyncio.run(
        dispatcher.handle_raw_update(
            _mk_update(
                update_id=206,
                user_id=9203,
                chat_id=-12004,
                chat_type="supergroup",
                title="Forum",
                text="group root",
            )
        )
    )
    asyncio.run(
        dispatcher.handle_raw_update(
            _mk_update(
                update_id=207,
                user_id=9203,
                chat_id=-12004,
                chat_type="supergroup",
                title="Forum",
                text="/ping",
            )
        )
    )

    recent_topic_1 = _recent_messages_for_scope(db_url, scope_type="topic", chat_id=-12004, topic_id=601)
    recent_topic_2 = _recent_messages_for_scope(db_url, scope_type="topic", chat_id=-12004, topic_id=602)
    recent_group = _recent_messages_for_scope(db_url, scope_type="group_chat", chat_id=-12004, topic_id=None)

    assert [row.message_text for row in recent_topic_1] == ["topic eins"]
    assert [row.message_text for row in recent_topic_2] == ["topic zwei"]
    assert [row.message_text for row in recent_group] == ["group root"]


def test_unreachable_dm_group_fallback_notifies_owner_when_fallback_sent(tmp_path) -> None:
    from amo_bot.telegram.client import TelegramApiError
    from amo_bot.telegram.owner_notify import OwnerNotifier

    db_url = f"sqlite:///{tmp_path / 'persist_owner_notify_fallback_sent.db'}"
    init_db(db_url)
    sent_owner: list[tuple[int, str]] = []

    async def _owner_send(chat_id: int, text: str) -> object:
        sent_owner.append((chat_id, text))
        return {"ok": True}

    sf = create_session_factory(db_url)

    async def _fake_send(_chat_id: int, _text: str, _message_thread_id: int | None = None) -> object:
        return {"ok": True}

    async def _fake_send_private(_chat_id: int, _text: str) -> object:
        raise TelegramApiError("Forbidden: bot can't initiate conversation with a user")

    dispatcher = Dispatcher(
        command_registry=create_builtin_registry(),
        role_resolver=InMemoryRoleResolver({42: Role.NORMAL}),
        send_text=_fake_send,
        bot_username="AmoBot",
        message_persistence=ChatTopicPersistenceService(
            sf,
            send_private_message=_fake_send_private,
            owner_notifier=OwnerNotifier(owner_telegram_user_id=9999, send_private_text=_owner_send),
            send_group_text=_fake_send,
            bot_username="AmoBot",
        ),
    )

    asyncio.run(
        dispatcher.handle_raw_update(
            _mk_update(update_id=104, user_id=8105, chat_id=-108105, chat_type="supergroup", title="G")
        )
    )

    unreachable_notifies = [text for _, text in sent_owner if "Policy-DM nicht zustellbar" in text]
    fallback_notifies = [text for _, text in sent_owner if "Gruppenfallback für Consent gesendet" in text]
    assert len(unreachable_notifies) == 1
    assert len(fallback_notifies) == 1
