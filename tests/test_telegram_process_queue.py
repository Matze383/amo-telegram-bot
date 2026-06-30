from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from amo_bot.auth.roles import Role
from amo_bot.db.base import create_session_factory
from amo_bot.db.init_db import init_db
from amo_bot.db.models import TelegramIncomingQueue, TelegramOutgoingQueue, TelegramQueueFailure
from amo_bot.db.telegram_queue import TelegramIncomingQueueRepository, TelegramOutgoingQueueRepository, _supports_skip_locked
from amo_bot.telegram.fake_telegram import FakeTelegramClient
from amo_bot.telegram.outbox_sender import OutboxSender
from amo_bot.telegram.supervisor import ManagedProcess, TelegramProcessSupervisor, get_spawn_context
from amo_bot.telegram.commands import create_builtin_registry
from amo_bot.telegram.dispatcher import Dispatcher
from amo_bot.telegram.role_resolver import InMemoryRoleResolver
from amo_bot.telegram.topic_worker import QueueBackedTelegramSender, QueueWorker, TopicWorker


def _db_url(tmp_path) -> str:
    return f"sqlite+pysqlite:///{tmp_path / 'queue.db'}"


def _text_update(*, update_id: int, chat_id: int, topic_id: int | None, message_id: int, text: str) -> dict:
    message = {
        "message_id": message_id,
        "from": {"id": 42, "is_bot": False, "first_name": "Tester", "username": "tester"},
        "chat": {"id": chat_id, "type": "supergroup" if chat_id < 0 else "private", "title": "Fake"},
        "text": text,
    }
    if topic_id is not None:
        message["message_thread_id"] = topic_id
    return {"update_id": update_id, "message": message}


def test_skip_locked_supports_postgresql_mysql_and_mariadb() -> None:
    class _Dialect:
        def __init__(self, name: str) -> None:
            self.name = name

    class _Bind:
        def __init__(self, name: str) -> None:
            self.dialect = _Dialect(name)

    class _Session:
        def __init__(self, name: str) -> None:
            self._bind = _Bind(name)

        def get_bind(self) -> _Bind:
            return self._bind

    assert _supports_skip_locked(_Session("postgresql")) is True  # type: ignore[arg-type]
    assert _supports_skip_locked(_Session("mysql")) is True  # type: ignore[arg-type]
    assert _supports_skip_locked(_Session("mariadb")) is True  # type: ignore[arg-type]
    assert _supports_skip_locked(_Session("sqlite")) is False  # type: ignore[arg-type]


def test_incoming_queue_claim_is_topic_scoped_and_stale_lease_reclaimable(tmp_path) -> None:
    url = _db_url(tmp_path)
    init_db(url)
    factory = create_session_factory(url)

    with factory() as session:
        repo = TelegramIncomingQueueRepository(session)
        repo.enqueue_update(_text_update(update_id=1, chat_id=-100, topic_id=10, message_id=1001, text="a"))
        repo.enqueue_update(_text_update(update_id=2, chat_id=-100, topic_id=20, message_id=1002, text="b"))

    with factory() as session:
        item = TelegramIncomingQueueRepository(session).claim_next_for_topic(
            chat_id=-100,
            topic_id=10,
            worker_id="topic:-100:10",
        )
        assert item is not None
        assert item.telegram_update_id == 1

    with factory() as session:
        assert (
            TelegramIncomingQueueRepository(session).claim_next_for_topic(
                chat_id=-100,
                topic_id=10,
                worker_id="other",
            )
            is None
        )

    with factory() as session:
        row = session.scalar(select(TelegramIncomingQueue).where(TelegramIncomingQueue.telegram_update_id == 1))
        assert row is not None
        row.locked_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.commit()

    with factory() as session:
        reclaimed = TelegramIncomingQueueRepository(session).claim_next_for_topic(
            chat_id=-100,
            topic_id=10,
            worker_id="topic:-100:10:restarted",
        )
        assert reclaimed is not None
        assert reclaimed.telegram_update_id == 1
        assert reclaimed.attempts == 2

    with factory() as session:
        topic_20 = TelegramIncomingQueueRepository(session).claim_next_for_topic(
            chat_id=-100,
            topic_id=20,
            worker_id="topic:-100:20",
        )
        assert topic_20 is not None
        assert topic_20.telegram_update_id == 2


def test_incoming_queue_pool_claim_blocks_parallel_same_scope_and_allows_other_scopes(tmp_path) -> None:
    url = _db_url(tmp_path)
    init_db(url)
    factory = create_session_factory(url)

    with factory() as session:
        repo = TelegramIncomingQueueRepository(session)
        repo.enqueue_update(_text_update(update_id=1, chat_id=-100, topic_id=10, message_id=1001, text="a1"))
        repo.enqueue_update(_text_update(update_id=2, chat_id=-100, topic_id=10, message_id=1002, text="a2"))
        repo.enqueue_update(_text_update(update_id=3, chat_id=-100, topic_id=20, message_id=1003, text="b1"))

    with factory() as session:
        first = TelegramIncomingQueueRepository(session).claim_next_available(worker_id="worker-1")
        assert first is not None
        assert first.telegram_update_id == 1

    with factory() as session:
        second = TelegramIncomingQueueRepository(session).claim_next_available(worker_id="worker-2")
        assert second is not None
        assert second.telegram_update_id == 3

    with factory() as session:
        assert TelegramIncomingQueueRepository(session).claim_next_available(worker_id="worker-3") is None


def test_incoming_queue_pool_claim_reclaims_stale_scope_before_followup(tmp_path) -> None:
    url = _db_url(tmp_path)
    init_db(url)
    factory = create_session_factory(url)

    with factory() as session:
        repo = TelegramIncomingQueueRepository(session)
        repo.enqueue_update(_text_update(update_id=1, chat_id=-100, topic_id=10, message_id=1001, text="a1"))
        repo.enqueue_update(_text_update(update_id=2, chat_id=-100, topic_id=10, message_id=1002, text="a2"))

    with factory() as session:
        first = TelegramIncomingQueueRepository(session).claim_next_available(worker_id="worker-1")
        assert first is not None
        assert first.telegram_update_id == 1

    with factory() as session:
        row = session.scalar(select(TelegramIncomingQueue).where(TelegramIncomingQueue.telegram_update_id == 1))
        assert row is not None
        row.locked_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.commit()

    with factory() as session:
        reclaimed = TelegramIncomingQueueRepository(session).claim_next_available(worker_id="worker-2")
        assert reclaimed is not None
        assert reclaimed.telegram_update_id == 1
        assert reclaimed.attempts == 2


def test_incoming_queue_quarantines_after_three_failures(tmp_path) -> None:
    url = _db_url(tmp_path)
    init_db(url)
    factory = create_session_factory(url)

    with factory() as session:
        TelegramIncomingQueueRepository(session).enqueue_update(
            _text_update(update_id=1, chat_id=-100, topic_id=10, message_id=1001, text="boom")
        )

    for attempt in range(1, 4):
        worker_id = f"worker-{attempt}"
        with factory() as session:
            item = TelegramIncomingQueueRepository(session).claim_next_for_topic(
                chat_id=-100,
                topic_id=10,
                worker_id=worker_id,
            )
            assert item is not None
            TelegramIncomingQueueRepository(session).fail(item.id, worker_id=worker_id, error="x" * 600)

    with factory() as session:
        row = session.scalar(select(TelegramIncomingQueue))
        assert row is not None
        assert row.status == "quarantined"
        assert row.last_error is not None
        assert len(row.last_error) == 512
        failure = session.scalar(select(TelegramQueueFailure))
        assert failure is not None
        assert failure.queue_name == "telegram_incoming_queue"


def test_topic_worker_enqueues_reply_with_job_topic_and_trigger_correlation(tmp_path) -> None:
    url = _db_url(tmp_path)
    init_db(url)
    factory = create_session_factory(url)

    with factory() as session:
        TelegramIncomingQueueRepository(session).enqueue_update(
            _text_update(update_id=11, chat_id=-100, topic_id=77, message_id=555, text="@AMO_bot hi")
        )

    class _FakeDispatcher:
        def __init__(self, sender: QueueBackedTelegramSender) -> None:
            self.sender = sender

        async def handle_raw_update(self, raw_update) -> None:  # noqa: ANN001
            await self.sender.send_text(-100, "queued answer", 77)

    worker = TopicWorker(
        database_url=url,
        chat_id=-100,
        topic_id=77,
        dispatcher_factory=lambda sender: _FakeDispatcher(sender),  # type: ignore[return-value]
        worker_id="topic:-100:77:test",
    )

    assert asyncio.run(worker.process_one()) is True

    with factory() as session:
        assert session.scalar(select(TelegramIncomingQueue)) is None
        outbox = session.scalar(select(TelegramOutgoingQueue))
        assert outbox is not None
        assert outbox.chat_id == -100
        assert outbox.topic_id == 77
        assert outbox.trigger_message_id == 555
        assert outbox.job_id == "in-1-11"


def test_topic_worker_restart_command_completes_incoming_before_runtime_restart(tmp_path) -> None:
    url = _db_url(tmp_path)
    init_db(url)
    factory = create_session_factory(url)

    with factory() as session:
        TelegramIncomingQueueRepository(session).enqueue_update(
            _text_update(update_id=21, chat_id=99, topic_id=None, message_id=7001, text="/restart")
        )

    restart_requests: list[bool] = []

    def _dispatcher_factory(sender: QueueBackedTelegramSender) -> Dispatcher:
        return Dispatcher(
            command_registry=create_builtin_registry(database_url=url),
            role_resolver=InMemoryRoleResolver({42: Role.OWNER}),
            send_text=sender.send_text,
            bot_username="AMO_bot",
            restart_terminator=lambda: restart_requests.append(True),
        )

    worker = TopicWorker(
        database_url=url,
        chat_id=99,
        topic_id=None,
        dispatcher_factory=_dispatcher_factory,
        worker_id="topic:99:root:test",
    )

    assert asyncio.run(worker.process_one()) is True

    assert restart_requests == [True]
    with factory() as session:
        assert session.scalar(select(TelegramIncomingQueue)) is None
        outbox = session.scalar(select(TelegramOutgoingQueue))
        assert outbox is not None
        assert outbox.chat_id == 99
        assert outbox.topic_id is None
        assert outbox.trigger_message_id == 7001
        assert outbox.text == "Restart wird ausgelöst."
        assert outbox.job_id == "in-1-21"


def test_queue_worker_claims_multiple_topics_from_pool(tmp_path) -> None:
    url = _db_url(tmp_path)
    init_db(url)
    factory = create_session_factory(url)

    with factory() as session:
        repo = TelegramIncomingQueueRepository(session)
        repo.enqueue_update(_text_update(update_id=31, chat_id=-100, topic_id=77, message_id=555, text="@AMO_bot hi"))
        repo.enqueue_update(_text_update(update_id=32, chat_id=-100, topic_id=88, message_id=556, text="@AMO_bot yo"))

    seen_topics: list[int | None] = []

    class _FakeDispatcher:
        def __init__(self, sender: QueueBackedTelegramSender) -> None:
            self.sender = sender

        async def handle_raw_update(self, raw_update) -> None:  # noqa: ANN001
            seen_topics.append(raw_update["message"].get("message_thread_id"))
            await self.sender.send_text(raw_update["message"]["chat"]["id"], "queued answer")

    worker = QueueWorker(
        database_url=url,
        dispatcher_factory=lambda sender: _FakeDispatcher(sender),  # type: ignore[return-value]
        worker_id="queue-worker:test",
    )

    assert asyncio.run(worker.process_one()) is True
    assert asyncio.run(worker.process_one()) is True

    assert seen_topics == [77, 88]
    with factory() as session:
        assert session.scalar(select(TelegramIncomingQueue)) is None
        assert len(session.scalars(select(TelegramOutgoingQueue)).all()) == 2


def test_outbox_sender_sends_in_order_as_reply_and_deletes_only_after_ok(tmp_path) -> None:
    url = _db_url(tmp_path)
    init_db(url)
    factory = create_session_factory(url)
    with factory() as session:
        repo = TelegramOutgoingQueueRepository(session)
        repo.enqueue_text(chat_id=-100, topic_id=77, trigger_message_id=555, text="first", job_id="a")
        repo.enqueue_text(chat_id=-100, topic_id=77, trigger_message_id=556, text="second", job_id="b")

    fake = FakeTelegramClient()
    persisted: list[dict] = []

    class _Persistence:
        async def persist_bot_sent_message(self, **kwargs) -> None:  # noqa: ANN003
            persisted.append(kwargs)

    sender = OutboxSender(
        database_url=url,
        telegram_client=fake,
        sender_id="sender:test",
        message_persistence=_Persistence(),
        bot_username="AMO_bot",
    )

    assert asyncio.run(sender.send_one()) is True
    assert asyncio.run(sender.send_one()) is True

    assert [item["text"] for item in fake.sent_messages] == ["first", "second"]
    assert [item["reply_to_message_id"] for item in fake.sent_messages] == [555, 556]
    assert [item["message_thread_id"] for item in fake.sent_messages] == [77, 77]
    assert [item["text"] for item in persisted] == ["first", "second"]
    assert [item["message_thread_id"] for item in persisted] == [77, 77]
    assert [item["bot_username"] for item in persisted] == ["AMO_bot", "AMO_bot"]
    with factory() as session:
        assert session.scalars(select(TelegramOutgoingQueue)).all() == []


def test_outbox_sender_retries_floodwait_without_deleting_row(tmp_path) -> None:
    url = _db_url(tmp_path)
    init_db(url)
    factory = create_session_factory(url)
    with factory() as session:
        TelegramOutgoingQueueRepository(session).enqueue_text(
            chat_id=-100,
            topic_id=77,
            trigger_message_id=555,
            text="later",
            job_id="flood",
        )

    fake = FakeTelegramClient(flood_wait_seconds=5)
    sender = OutboxSender(database_url=url, telegram_client=fake, sender_id="sender:test")

    assert asyncio.run(sender.send_one()) is False
    with factory() as session:
        row = session.scalar(select(TelegramOutgoingQueue))
        assert row is not None
        assert row.status == "queued"
        assert row.attempts == 0
        assert row.not_before is not None


def test_outbox_sender_parks_sent_message_when_persistence_fails(tmp_path) -> None:
    url = _db_url(tmp_path)
    init_db(url)
    factory = create_session_factory(url)
    with factory() as session:
        TelegramOutgoingQueueRepository(session).enqueue_text(
            chat_id=-100,
            topic_id=77,
            trigger_message_id=555,
            text="already sent",
            job_id="post-send-failure",
        )

    class _FailingPersistence:
        async def persist_bot_sent_message(self, **kwargs) -> None:  # noqa: ANN003
            raise RuntimeError("persistence offline")

    fake = FakeTelegramClient()
    sender = OutboxSender(
        database_url=url,
        telegram_client=fake,
        sender_id="sender:test",
        message_persistence=_FailingPersistence(),
    )

    assert asyncio.run(sender.send_one()) is False
    assert [item["text"] for item in fake.sent_messages] == ["already sent"]

    with factory() as session:
        row = session.scalar(select(TelegramOutgoingQueue))
        assert row is not None
        assert row.status == "sent_unconfirmed"
        assert row.attempts == 1
        assert row.sent_message_id == 1001
        assert row.locked_by is None
        assert row.locked_until is None
        failure = session.scalar(select(TelegramQueueFailure))
        assert failure is not None
        assert failure.job_id == "post-send-failure"

    assert asyncio.run(sender.send_one()) is False
    assert [item["text"] for item in fake.sent_messages] == ["already sent"]


def test_outbox_sender_recovers_sent_unconfirmed_without_resending(tmp_path) -> None:
    url = _db_url(tmp_path)
    init_db(url)
    factory = create_session_factory(url)
    with factory() as session:
        TelegramOutgoingQueueRepository(session).enqueue_text(
            chat_id=-100,
            topic_id=77,
            trigger_message_id=555,
            text="recover me",
            job_id="recover",
        )

    class _FailingPersistence:
        async def persist_bot_sent_message(self, **kwargs) -> None:  # noqa: ANN003
            raise RuntimeError("persistence offline")

    fake = FakeTelegramClient()
    failing_sender = OutboxSender(
        database_url=url,
        telegram_client=fake,
        sender_id="sender:test",
        message_persistence=_FailingPersistence(),
        bot_username="AMO_bot",
    )
    assert asyncio.run(failing_sender.send_one()) is False
    assert [item["text"] for item in fake.sent_messages] == ["recover me"]

    persisted: list[dict] = []

    class _RecoveredPersistence:
        async def persist_bot_sent_message(self, **kwargs) -> None:  # noqa: ANN003
            persisted.append(kwargs)

    recovering_sender = OutboxSender(
        database_url=url,
        telegram_client=fake,
        sender_id="sender:recovery",
        message_persistence=_RecoveredPersistence(),
        bot_username="AMO_bot",
    )

    assert asyncio.run(recovering_sender.send_one()) is True
    assert [item["text"] for item in fake.sent_messages] == ["recover me"]
    assert persisted == [
        {
            "chat_id": -100,
            "message_thread_id": 77,
            "message_id": 1001,
            "text": "recover me",
            "bot_username": "AMO_bot",
        }
    ]
    with factory() as session:
        assert session.scalars(select(TelegramOutgoingQueue)).all() == []


def test_outbox_sender_quarantines_repeated_sent_unconfirmed_finalization_failures(tmp_path) -> None:
    url = _db_url(tmp_path)
    init_db(url)
    factory = create_session_factory(url)
    with factory() as session:
        TelegramOutgoingQueueRepository(session).enqueue_text(
            chat_id=-100,
            topic_id=77,
            trigger_message_id=555,
            text="never persists",
            job_id="quarantine",
        )

    class _FailingPersistence:
        async def persist_bot_sent_message(self, **kwargs) -> None:  # noqa: ANN003
            raise RuntimeError("persistence still offline")

    fake = FakeTelegramClient()
    sender = OutboxSender(
        database_url=url,
        telegram_client=fake,
        sender_id="sender:test",
        message_persistence=_FailingPersistence(),
        max_attempts=3,
    )

    assert asyncio.run(sender.send_one()) is False
    assert asyncio.run(sender.send_one()) is False
    assert asyncio.run(sender.send_one()) is False

    assert [item["text"] for item in fake.sent_messages] == ["never persists"]
    with factory() as session:
        row = session.scalar(select(TelegramOutgoingQueue))
        assert row is not None
        assert row.status == "quarantined"
        assert row.attempts == 3
        assert row.sent_message_id == 1001
        failures = session.scalars(select(TelegramQueueFailure)).all()
        assert len(failures) == 3
        assert failures[-1].attempts == 3

    assert asyncio.run(sender.send_one()) is False
    assert [item["text"] for item in fake.sent_messages] == ["never persists"]


def test_supervisor_uses_spawn_start_method() -> None:
    assert get_spawn_context().get_start_method() == "spawn"


def test_supervisor_registers_fixed_queue_workers(tmp_path) -> None:
    class _TestSupervisor(TelegramProcessSupervisor):
        def __init__(self, database_url: str) -> None:
            super().__init__(database_url=database_url)
            self.started: list[list[str] | None] = []

        def start_registered(self, names: list[str] | None = None) -> None:
            self.started.append(names)

    supervisor = _TestSupervisor(database_url=_db_url(tmp_path))
    sender = ManagedProcess(name="sender", kind="sender", target=lambda: None)
    workers = [
        ManagedProcess(name="worker-1", kind="queue_worker", target=lambda: None),
        ManagedProcess(name="worker-2", kind="queue_worker", target=lambda: None),
    ]
    poller = ManagedProcess(name="poller", kind="poller", target=lambda: None)

    supervisor.start_runtime(sender=sender, workers=workers, poller=poller)

    assert list(supervisor.processes) == ["sender", "worker-1", "worker-2", "poller"]
    assert supervisor.started == [["sender", "worker-1", "worker-2", "poller"]]


def test_supervisor_runtime_start_order_is_sender_workers_poller(tmp_path) -> None:
    class _TestSupervisor(TelegramProcessSupervisor):
        def __init__(self, database_url: str) -> None:
            super().__init__(database_url=database_url)
            self.order: list[list[str] | None] = []

        def validate_and_prepare(self) -> None:
            return None

        def start_registered(self, names: list[str] | None = None) -> None:
            self.order.append(names)

    supervisor = _TestSupervisor(database_url=_db_url(tmp_path))

    sender = ManagedProcess(name="sender", kind="sender", target=lambda: None)
    worker = ManagedProcess(name="worker", kind="queue_worker", target=lambda: None)
    poller = ManagedProcess(name="poller", kind="poller", target=lambda: None)

    supervisor.start_runtime(sender=sender, workers=[worker], poller=poller)

    assert supervisor.order == [["sender", "worker", "poller"]]
