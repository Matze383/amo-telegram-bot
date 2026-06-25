import asyncio
import logging

from amo_bot.auth.roles import Role
from amo_bot.db.base import create_session_factory
from amo_bot.db.init_db import init_db
from amo_bot.db.repositories import ChatScopedRoleRepository, UserRoleRepository
from amo_bot.telegram.role_resolver import DBRoleResolver


class _FakeTelegramClient:
    def __init__(self, statuses: dict[tuple[int, int], str] | None = None, *, fail: bool = False) -> None:
        self.statuses = statuses or {}
        self.fail = fail
        self.calls: list[tuple[int, int]] = []

    async def get_chat_member(self, *, chat_id: int, user_id: int) -> dict[str, object]:
        self.calls.append((chat_id, user_id))
        if self.fail:
            raise RuntimeError("telegram unavailable")
        return {"status": self.statuses.get((chat_id, user_id), "member")}


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


def test_telegram_group_administrator_is_effective_admin_when_no_manual_group_role(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'telegram_admin.db'}"
    init_db(db_url)
    sf = create_session_factory(db_url)

    tg = _FakeTelegramClient({(-601, 6001): "administrator"})
    resolver = DBRoleResolver(sf, telegram_client=tg)

    assert asyncio.run(resolver.resolve(6001, chat_id=-601, chat_type="supergroup")) == Role.ADMIN
    assert tg.calls == [(-601, 6001)]


def test_telegram_group_creator_maps_to_scoped_admin_not_global_owner(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'telegram_creator.db'}"
    init_db(db_url)
    sf = create_session_factory(db_url)

    tg = _FakeTelegramClient({(-602, 6002): "creator"})
    resolver = DBRoleResolver(sf, telegram_client=tg)

    assert asyncio.run(resolver.resolve(6002, chat_id=-602, chat_type="group")) == Role.ADMIN
    assert asyncio.run(resolver.resolve(6002, chat_id=6002, chat_type="private")) == Role.NORMAL


def test_manual_group_role_wins_over_telegram_admin_fallback(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'manual_group_role_wins.db'}"
    init_db(db_url)
    sf = create_session_factory(db_url)

    with sf() as session:
        from amo_bot.db.repositories import ChatTopicRepository

        ChatTopicRepository(session).upsert_chat(chat_id=-603, chat_type="supergroup")
        ChatScopedRoleRepository(session).set_group_role(chat_id=-603, telegram_user_id=6003, role=Role.VIP)

    tg = _FakeTelegramClient({(-603, 6003): "administrator"})
    resolver = DBRoleResolver(sf, telegram_client=tg)

    assert asyncio.run(resolver.resolve(6003, chat_id=-603, chat_type="supergroup")) == Role.VIP
    assert tg.calls == []


def test_global_ignore_is_not_bypassed_by_telegram_admin(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'global_ignore_wins.db'}"
    init_db(db_url)
    sf = create_session_factory(db_url)

    with sf() as session:
        UserRoleRepository(session).set_user_role(actor_telegram_user_id=1, target_telegram_user_id=6004, role=Role.IGNORE)

    tg = _FakeTelegramClient({(-604, 6004): "administrator"})
    resolver = DBRoleResolver(sf, telegram_client=tg)

    assert asyncio.run(resolver.resolve(6004, chat_id=-604, chat_type="supergroup")) == Role.IGNORE
    assert tg.calls == []


def test_global_owner_is_not_downgraded_by_telegram_member_status(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'global_owner_wins.db'}"
    init_db(db_url)
    sf = create_session_factory(db_url)

    with sf() as session:
        UserRoleRepository(session).set_user_role(actor_telegram_user_id=1, target_telegram_user_id=6005, role=Role.OWNER)

    tg = _FakeTelegramClient({(-605, 6005): "member"})
    resolver = DBRoleResolver(sf, telegram_client=tg)

    assert asyncio.run(resolver.resolve(6005, chat_id=-605, chat_type="group")) == Role.OWNER
    assert tg.calls == []


def test_telegram_chat_member_failure_falls_back_to_normal_in_group(tmp_path, caplog) -> None:
    db_url = f"sqlite:///{tmp_path / 'telegram_failure.db'}"
    init_db(db_url)
    sf = create_session_factory(db_url)

    tg = _FakeTelegramClient(fail=True)
    resolver = DBRoleResolver(sf, telegram_client=tg)

    with caplog.at_level(logging.INFO, logger="amo_bot.telegram.role_resolver"):
        assert asyncio.run(resolver.resolve(6006006, chat_id=-100606, chat_type="supergroup")) == Role.NORMAL

    assert tg.calls == [(-100606, 6006006)]

    records = [record for record in caplog.records if record.msg == "telegram chat member lookup failed; falling back to stored role"]
    assert len(records) == 1
    record = records[0]
    assert not hasattr(record, "chat_id")
    assert not hasattr(record, "user_id")
    assert record.chat_id_masked == "-10***..06 [7 digits]"
    assert record.user_id_masked == "600***..06 [7 digits]"
    assert record.chat_type == "supergroup"
    assert record.error_type == "RuntimeError"

    rendered = caplog.text
    assert "-100606" not in rendered
    assert "6006006" not in rendered


def test_telegram_admin_lookup_uses_short_cache(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'telegram_cache.db'}"
    init_db(db_url)
    sf = create_session_factory(db_url)

    tg = _FakeTelegramClient({(-607, 6007): "administrator"})
    resolver = DBRoleResolver(sf, telegram_client=tg)

    assert asyncio.run(resolver.resolve(6007, chat_id=-607, chat_type="group")) == Role.ADMIN
    tg.statuses[(-607, 6007)] = "member"
    assert asyncio.run(resolver.resolve(6007, chat_id=-607, chat_type="group")) == Role.ADMIN
    assert tg.calls == [(-607, 6007)]
