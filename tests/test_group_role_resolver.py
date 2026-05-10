import asyncio

from amo_bot.auth.roles import Role
from amo_bot.db.base import create_session_factory
from amo_bot.db.init_db import init_db
from amo_bot.db.repositories import ChatScopedRoleRepository, UserRoleRepository
from amo_bot.telegram.role_resolver import DBRoleResolver


def test_no_group_role_defaults_normal_even_when_global_admin_or_vip(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    init_db(db_url)
    sf = create_session_factory(db_url)

    with sf() as session:
        urepo = UserRoleRepository(session)
        urepo.set_user_role(actor_telegram_user_id=1, target_telegram_user_id=1001, role=Role.ADMIN)
        urepo.set_user_role(actor_telegram_user_id=1, target_telegram_user_id=1002, role=Role.VIP)

    resolver = DBRoleResolver(sf)
    assert asyncio.run(resolver.resolve(1001, chat_id=-1001, chat_type="group")) == Role.NORMAL
    assert asyncio.run(resolver.resolve(1002, chat_id=-1001, chat_type="supergroup")) == Role.NORMAL


def test_group_role_overrides_group_fallback(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    init_db(db_url)
    sf = create_session_factory(db_url)

    with sf() as session:
        urepo = UserRoleRepository(session)
        urepo.set_user_role(actor_telegram_user_id=1, target_telegram_user_id=2001, role=Role.VIP)
        # prerequisite chat row (FK)
        from amo_bot.db.repositories import ChatTopicRepository

        ChatTopicRepository(session).upsert_chat(chat_id=-200, chat_type="supergroup")
        ChatScopedRoleRepository(session).set_group_role(chat_id=-200, telegram_user_id=2001, role=Role.ADMIN)

    resolver = DBRoleResolver(sf)
    assert asyncio.run(resolver.resolve(2001, chat_id=-200, chat_type="supergroup")) == Role.ADMIN


def test_global_owner_and_ignore_override_everywhere(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    init_db(db_url)
    sf = create_session_factory(db_url)

    with sf() as session:
        urepo = UserRoleRepository(session)
        urepo.set_user_role(actor_telegram_user_id=1, target_telegram_user_id=3001, role=Role.OWNER)
        urepo.set_user_role(actor_telegram_user_id=1, target_telegram_user_id=3002, role=Role.IGNORE)

    resolver = DBRoleResolver(sf)
    assert asyncio.run(resolver.resolve(3001, chat_id=-300, chat_type="group")) == Role.OWNER
    assert asyncio.run(resolver.resolve(3001, chat_id=3001, chat_type="private")) == Role.OWNER
    assert asyncio.run(resolver.resolve(3002, chat_id=-300, chat_type="supergroup")) == Role.IGNORE
    assert asyncio.run(resolver.resolve(3002, chat_id=3002, chat_type="private")) == Role.IGNORE


def test_group_ignore_only_in_that_group(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    init_db(db_url)
    sf = create_session_factory(db_url)

    with sf() as session:
        urepo = UserRoleRepository(session)
        urepo.set_user_role(actor_telegram_user_id=1, target_telegram_user_id=4001, role=Role.VIP)
        from amo_bot.db.repositories import ChatTopicRepository

        crepo = ChatTopicRepository(session)
        crepo.upsert_chat(chat_id=-401, chat_type="group")
        crepo.upsert_chat(chat_id=-402, chat_type="group")
        ChatScopedRoleRepository(session).set_group_role(chat_id=-401, telegram_user_id=4001, role=Role.IGNORE)

    resolver = DBRoleResolver(sf)
    assert asyncio.run(resolver.resolve(4001, chat_id=-401, chat_type="group")) == Role.IGNORE
    assert asyncio.run(resolver.resolve(4001, chat_id=-402, chat_type="group")) == Role.NORMAL


def test_group_a_admin_not_group_b_admin_and_dm_uses_global(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    init_db(db_url)
    sf = create_session_factory(db_url)

    with sf() as session:
        urepo = UserRoleRepository(session)
        urepo.set_user_role(actor_telegram_user_id=1, target_telegram_user_id=5001, role=Role.VIP)
        from amo_bot.db.repositories import ChatTopicRepository

        crepo = ChatTopicRepository(session)
        crepo.upsert_chat(chat_id=-501, chat_type="supergroup")
        crepo.upsert_chat(chat_id=-502, chat_type="supergroup")
        ChatScopedRoleRepository(session).set_group_role(chat_id=-501, telegram_user_id=5001, role=Role.ADMIN)

    resolver = DBRoleResolver(sf)
    assert asyncio.run(resolver.resolve(5001, chat_id=-501, chat_type="supergroup")) == Role.ADMIN
    assert asyncio.run(resolver.resolve(5001, chat_id=-502, chat_type="supergroup")) == Role.NORMAL
    assert asyncio.run(resolver.resolve(5001, chat_id=5001, chat_type="private")) == Role.VIP
