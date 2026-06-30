from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
import uuid
from typing import Any

from sqlalchemy import and_, exists, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from amo_bot.db.models import (
    TelegramIncomingQueue,
    TelegramOutgoingQueue,
    TelegramProcessHealth,
    TelegramQueueFailure,
)
from amo_bot.telegram.update_parser import TelegramMessage, TelegramReactionEvent, parse_update


SCHEMA_VERSION = "telegram_queue_v1"
DEFAULT_LEASE_SECONDS = 300
DEFAULT_MAX_ATTEMPTS = 3
MAX_STORED_ERROR_CHARS = 512
CLAIM_RACE_RETRIES = 3


@dataclass(slots=True)
class TelegramQueueScope:
    chat_id: int | None
    topic_id: int | None
    message_id: int | None
    update_kind: str


@dataclass(slots=True)
class IncomingQueueItem:
    id: int
    telegram_update_id: int
    chat_id: int | None
    topic_id: int | None
    message_id: int | None
    payload: dict[str, Any]
    attempts: int
    locked_by: str | None


@dataclass(slots=True)
class OutgoingQueueItem:
    id: int
    job_id: str
    chat_id: int
    topic_id: int | None
    trigger_message_id: int | None
    sent_message_id: int | None
    text: str
    parse_mode: str | None
    reply_markup: dict[str, Any] | None
    attempts: int
    locked_by: str | None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _redact_error(error: BaseException | str) -> str:
    text = str(error)
    text = " ".join(text.split())
    if text:
        return text[:MAX_STORED_ERROR_CHARS]
    if isinstance(error, BaseException):
        return error.__class__.__name__[:MAX_STORED_ERROR_CHARS]
    return ""


def _json_dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def _supports_skip_locked(session: Session) -> bool:
    dialect_name = session.get_bind().dialect.name
    return dialect_name in {"postgresql", "mysql", "mariadb"}


def extract_queue_scope(raw_update: object) -> TelegramQueueScope:
    update = parse_update(raw_update)
    if update is None:
        return TelegramQueueScope(chat_id=None, topic_id=None, message_id=None, update_kind="unknown")
    if update.message is not None:
        message: TelegramMessage = update.message
        return TelegramQueueScope(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            message_id=message.message_id,
            update_kind="message",
        )
    if update.edited_message is not None:
        message = update.edited_message
        return TelegramQueueScope(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            message_id=message.message_id,
            update_kind="edited_message",
        )
    if update.message_reaction is not None:
        reaction: TelegramReactionEvent = update.message_reaction
        return TelegramQueueScope(
            chat_id=reaction.chat.id,
            topic_id=reaction.message_thread_id,
            message_id=reaction.message_id,
            update_kind="message_reaction",
        )
    if update.callback_query is not None and update.callback_query.message is not None:
        message = update.callback_query.message
        return TelegramQueueScope(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            message_id=message.message_id,
            update_kind="callback_query",
        )
    return TelegramQueueScope(chat_id=None, topic_id=None, message_id=None, update_kind=update.top_level_kind or "unknown")


class TelegramIncomingQueueRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def enqueue_update(self, raw_update: dict[str, Any], *, schema_version: str = SCHEMA_VERSION) -> IncomingQueueItem | None:
        update_id = int(raw_update["update_id"])
        scope = extract_queue_scope(raw_update)
        payload = {"schema_version": schema_version, "raw_update": raw_update}
        row = TelegramIncomingQueue(
            schema_version=schema_version,
            telegram_update_id=update_id,
            chat_id=scope.chat_id,
            topic_id=scope.topic_id,
            message_id=scope.message_id,
            update_kind=scope.update_kind,
            payload_json=_json_dumps(payload),
            status="queued",
        )
        self._session.add(row)
        try:
            self._session.commit()
        except IntegrityError:
            self._session.rollback()
            existing = self._session.scalar(
                select(TelegramIncomingQueue).where(TelegramIncomingQueue.telegram_update_id == update_id)
            )
            return self._to_incoming_item(existing) if existing is not None else None
        self._session.refresh(row)
        return self._to_incoming_item(row)

    def claim_next_for_topic(
        self,
        *,
        chat_id: int,
        topic_id: int | None,
        worker_id: str,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> IncomingQueueItem | None:
        for _ in range(CLAIM_RACE_RETRIES):
            now = _now()
            lease_until = now + timedelta(seconds=max(1, lease_seconds))
            query = (
                select(TelegramIncomingQueue)
                .where(
                    TelegramIncomingQueue.chat_id == chat_id,
                    TelegramIncomingQueue.topic_id == topic_id,
                    TelegramIncomingQueue.attempts < max_attempts,
                    or_(
                        TelegramIncomingQueue.status == "queued",
                        and_(
                            TelegramIncomingQueue.status == "processing",
                            TelegramIncomingQueue.locked_until.is_not(None),
                            TelegramIncomingQueue.locked_until <= now,
                        ),
                    ),
                )
                .order_by(TelegramIncomingQueue.id.asc())
                .limit(1)
            )
            if _supports_skip_locked(self._session):
                query = query.with_for_update(skip_locked=True)
            candidate = self._session.scalar(query)
            if candidate is None:
                return None
            result = self._session.execute(
                update(TelegramIncomingQueue)
                .where(
                    TelegramIncomingQueue.id == candidate.id,
                    TelegramIncomingQueue.chat_id == chat_id,
                    TelegramIncomingQueue.topic_id == topic_id,
                    TelegramIncomingQueue.attempts == candidate.attempts,
                    or_(
                        TelegramIncomingQueue.status == "queued",
                        and_(
                            TelegramIncomingQueue.status == "processing",
                            TelegramIncomingQueue.locked_until.is_not(None),
                            TelegramIncomingQueue.locked_until <= now,
                        ),
                    ),
                )
                .values(
                    status="processing",
                    locked_by=worker_id,
                    locked_until=lease_until,
                    attempts=candidate.attempts + 1,
                    last_error=None,
                )
                .execution_options(synchronize_session=False)
            )
            if result.rowcount == 1:
                self._session.commit()
                self._session.refresh(candidate)
                return self._to_incoming_item(candidate)
            self._session.rollback()
        return None

    def claim_next_available(
        self,
        *,
        worker_id: str,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> IncomingQueueItem | None:
        for _ in range(CLAIM_RACE_RETRIES):
            now = _now()
            lease_until = now + timedelta(seconds=max(1, lease_seconds))
            active = aliased(TelegramIncomingQueue)
            active_scope_exists = exists(
                select(active.id).where(
                    self._same_scope(active, TelegramIncomingQueue),
                    active.status == "processing",
                    active.locked_until.is_not(None),
                    active.locked_until > now,
                )
            )
            query = (
                select(TelegramIncomingQueue)
                .where(
                    TelegramIncomingQueue.attempts < max_attempts,
                    ~active_scope_exists,
                    or_(
                        TelegramIncomingQueue.status == "queued",
                        and_(
                            TelegramIncomingQueue.status == "processing",
                            TelegramIncomingQueue.locked_until.is_not(None),
                            TelegramIncomingQueue.locked_until <= now,
                        ),
                    ),
                )
                .order_by(TelegramIncomingQueue.id.asc())
                .limit(1)
            )
            if _supports_skip_locked(self._session):
                query = query.with_for_update(skip_locked=True)
            candidate = self._session.scalar(query)
            if candidate is None:
                return None

            active_for_update = aliased(TelegramIncomingQueue)
            active_scope_exists_for_update = exists(
                select(active_for_update.id).where(
                    self._same_scope(active_for_update, TelegramIncomingQueue),
                    active_for_update.status == "processing",
                    active_for_update.locked_until.is_not(None),
                    active_for_update.locked_until > now,
                )
            )
            result = self._session.execute(
                update(TelegramIncomingQueue)
                .where(
                    TelegramIncomingQueue.id == candidate.id,
                    TelegramIncomingQueue.attempts == candidate.attempts,
                    ~active_scope_exists_for_update,
                    or_(
                        TelegramIncomingQueue.status == "queued",
                        and_(
                            TelegramIncomingQueue.status == "processing",
                            TelegramIncomingQueue.locked_until.is_not(None),
                            TelegramIncomingQueue.locked_until <= now,
                        ),
                    ),
                )
                .values(
                    status="processing",
                    locked_by=worker_id,
                    locked_until=lease_until,
                    attempts=candidate.attempts + 1,
                    last_error=None,
                )
                .execution_options(synchronize_session=False)
            )
            if result.rowcount == 1:
                self._session.commit()
                self._session.refresh(candidate)
                return self._to_incoming_item(candidate)
            self._session.rollback()
        return None

    def heartbeat(self, item_id: int, *, worker_id: str, lease_seconds: int = DEFAULT_LEASE_SECONDS) -> bool:
        result = self._session.execute(
            update(TelegramIncomingQueue)
            .where(
                TelegramIncomingQueue.id == item_id,
                TelegramIncomingQueue.status == "processing",
                TelegramIncomingQueue.locked_by == worker_id,
            )
            .values(locked_until=_now() + timedelta(seconds=max(1, lease_seconds)))
            .execution_options(synchronize_session=False)
        )
        self._session.commit()
        return result.rowcount == 1

    def complete(self, item_id: int, *, worker_id: str) -> bool:
        row = self._session.get(TelegramIncomingQueue, item_id)
        if row is None or row.locked_by != worker_id:
            return False
        self._session.delete(row)
        self._session.commit()
        return True

    def fail(
        self,
        item_id: int,
        *,
        worker_id: str,
        error: BaseException | str,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> bool:
        row = self._session.get(TelegramIncomingQueue, item_id)
        if row is None or row.locked_by != worker_id:
            return False
        sanitized = _redact_error(error)
        if row.attempts >= max_attempts:
            row.status = "quarantined"
            row.locked_by = None
            row.locked_until = None
            row.last_error = sanitized
            self._session.add(
                TelegramQueueFailure(
                    queue_name=TelegramIncomingQueue.__tablename__,
                    queue_row_id=row.id,
                    chat_id=row.chat_id,
                    topic_id=row.topic_id,
                    trigger_message_id=row.message_id,
                    attempts=row.attempts,
                    error=sanitized,
                    payload_json=row.payload_json,
                )
            )
        else:
            row.status = "queued"
            row.locked_by = None
            row.locked_until = None
            row.last_error = sanitized
        self._session.commit()
        return True

    @staticmethod
    def _to_incoming_item(row: TelegramIncomingQueue) -> IncomingQueueItem:
        return IncomingQueueItem(
            id=row.id,
            telegram_update_id=row.telegram_update_id,
            chat_id=row.chat_id,
            topic_id=row.topic_id,
            message_id=row.message_id,
            payload=json.loads(row.payload_json),
            attempts=row.attempts,
            locked_by=row.locked_by,
        )

    @staticmethod
    def _same_scope(left, right):  # noqa: ANN001
        return and_(
            or_(left.chat_id == right.chat_id, and_(left.chat_id.is_(None), right.chat_id.is_(None))),
            or_(left.topic_id == right.topic_id, and_(left.topic_id.is_(None), right.topic_id.is_(None))),
        )


class TelegramOutgoingQueueRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def enqueue_text(
        self,
        *,
        chat_id: int,
        text: str,
        topic_id: int | None,
        trigger_message_id: int | None,
        job_id: str | None = None,
        reply_markup: dict[str, Any] | None = None,
        parse_mode: str | None = None,
    ) -> OutgoingQueueItem:
        row = TelegramOutgoingQueue(
            schema_version=SCHEMA_VERSION,
            job_id=job_id or uuid.uuid4().hex,
            chat_id=chat_id,
            topic_id=topic_id,
            trigger_message_id=trigger_message_id,
            text=text,
            parse_mode=parse_mode,
            reply_markup_json=_json_dumps(reply_markup) if reply_markup is not None else None,
            status="queued",
        )
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return self._to_outgoing_item(row)

    def claim_next(
        self,
        *,
        sender_id: str,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> OutgoingQueueItem | None:
        for _ in range(CLAIM_RACE_RETRIES):
            now = _now()
            query = (
                select(TelegramOutgoingQueue)
                .where(
                    TelegramOutgoingQueue.attempts < max_attempts,
                    or_(TelegramOutgoingQueue.not_before.is_(None), TelegramOutgoingQueue.not_before <= now),
                    or_(
                        TelegramOutgoingQueue.status == "queued",
                        and_(
                            TelegramOutgoingQueue.status == "sending",
                            TelegramOutgoingQueue.locked_until.is_not(None),
                            TelegramOutgoingQueue.locked_until <= now,
                        ),
                    ),
                )
                .order_by(TelegramOutgoingQueue.id.asc())
                .limit(1)
            )
            if _supports_skip_locked(self._session):
                query = query.with_for_update(skip_locked=True)
            candidate = self._session.scalar(query)
            if candidate is None:
                return None
            result = self._session.execute(
                update(TelegramOutgoingQueue)
                .where(
                    TelegramOutgoingQueue.id == candidate.id,
                    TelegramOutgoingQueue.attempts == candidate.attempts,
                    or_(
                        TelegramOutgoingQueue.status == "queued",
                        and_(
                            TelegramOutgoingQueue.status == "sending",
                            TelegramOutgoingQueue.locked_until.is_not(None),
                            TelegramOutgoingQueue.locked_until <= now,
                        ),
                    ),
                )
                .values(
                    status="sending",
                    locked_by=sender_id,
                    locked_until=now + timedelta(seconds=max(1, lease_seconds)),
                    attempts=candidate.attempts + 1,
                    last_error=None,
                )
                .execution_options(synchronize_session=False)
            )
            if result.rowcount == 1:
                self._session.commit()
                self._session.refresh(candidate)
                return self._to_outgoing_item(candidate)
            self._session.rollback()
        return None

    def complete_sent(self, item_id: int, *, sender_id: str) -> bool:
        row = self._session.get(TelegramOutgoingQueue, item_id)
        if row is None or row.locked_by != sender_id:
            return False
        self._session.delete(row)
        self._session.commit()
        return True

    def claim_sent_unconfirmed_for_finalization(
        self,
        *,
        sender_id: str,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> OutgoingQueueItem | None:
        for _ in range(CLAIM_RACE_RETRIES):
            now = _now()
            query = (
                select(TelegramOutgoingQueue)
                .where(
                    TelegramOutgoingQueue.status == "sent_unconfirmed",
                    TelegramOutgoingQueue.attempts < max_attempts,
                    or_(TelegramOutgoingQueue.locked_until.is_(None), TelegramOutgoingQueue.locked_until <= now),
                )
                .order_by(TelegramOutgoingQueue.id.asc())
                .limit(1)
            )
            if _supports_skip_locked(self._session):
                query = query.with_for_update(skip_locked=True)
            candidate = self._session.scalar(query)
            if candidate is None:
                return None
            result = self._session.execute(
                update(TelegramOutgoingQueue)
                .where(
                    TelegramOutgoingQueue.id == candidate.id,
                    TelegramOutgoingQueue.status == "sent_unconfirmed",
                    TelegramOutgoingQueue.attempts == candidate.attempts,
                    or_(TelegramOutgoingQueue.locked_until.is_(None), TelegramOutgoingQueue.locked_until <= now),
                )
                .values(
                    locked_by=sender_id,
                    locked_until=now + timedelta(seconds=max(1, lease_seconds)),
                    attempts=candidate.attempts + 1,
                )
                .execution_options(synchronize_session=False)
            )
            if result.rowcount == 1:
                self._session.commit()
                self._session.refresh(candidate)
                return self._to_outgoing_item(candidate)
            self._session.rollback()
        return None

    def complete_sent_unconfirmed(self, item_id: int, *, sender_id: str) -> bool:
        row = self._session.get(TelegramOutgoingQueue, item_id)
        if row is None or row.status != "sent_unconfirmed" or row.locked_by != sender_id:
            return False
        self._session.delete(row)
        self._session.commit()
        return True

    def mark_sent_unconfirmed(
        self,
        item_id: int,
        *,
        sender_id: str,
        error: BaseException | str,
        telegram_message_id: int | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> bool:
        row = self._session.get(TelegramOutgoingQueue, item_id)
        if row is None or row.locked_by != sender_id:
            return False
        sanitized = _redact_error(error)
        row.status = "quarantined" if row.attempts >= max_attempts else "sent_unconfirmed"
        if telegram_message_id is not None:
            row.sent_message_id = telegram_message_id
        row.locked_by = None
        row.locked_until = None
        row.last_error = sanitized
        self._session.add(
            TelegramQueueFailure(
                queue_name=TelegramOutgoingQueue.__tablename__,
                queue_row_id=row.id,
                job_id=row.job_id,
                chat_id=row.chat_id,
                topic_id=row.topic_id,
                trigger_message_id=row.trigger_message_id,
                attempts=row.attempts,
                error=sanitized,
                payload_json=_json_dumps(
                    {
                        "text": row.text[:256],
                        "schema_version": row.schema_version,
                        "sent_message_id": row.sent_message_id,
                    }
                ),
            )
        )
        self._session.commit()
        return True

    def fail_sent_unconfirmed_finalization(
        self,
        item_id: int,
        *,
        sender_id: str,
        error: BaseException | str,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> bool:
        row = self._session.get(TelegramOutgoingQueue, item_id)
        if row is None or row.status != "sent_unconfirmed" or row.locked_by != sender_id:
            return False
        sanitized = _redact_error(error)
        if row.attempts >= max_attempts:
            row.status = "quarantined"
        row.locked_by = None
        row.locked_until = None
        row.last_error = sanitized
        self._session.add(
            TelegramQueueFailure(
                queue_name=TelegramOutgoingQueue.__tablename__,
                queue_row_id=row.id,
                job_id=row.job_id,
                chat_id=row.chat_id,
                topic_id=row.topic_id,
                trigger_message_id=row.trigger_message_id,
                attempts=row.attempts,
                error=sanitized,
                payload_json=_json_dumps(
                    {
                        "text": row.text[:256],
                        "schema_version": row.schema_version,
                        "sent_message_id": row.sent_message_id,
                    }
                ),
            )
        )
        self._session.commit()
        return True

    def fail(
        self,
        item_id: int,
        *,
        sender_id: str,
        error: BaseException | str,
        retry_after_seconds: int | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> bool:
        row = self._session.get(TelegramOutgoingQueue, item_id)
        if row is None or row.locked_by != sender_id:
            return False
        sanitized = _redact_error(error)
        if row.attempts >= max_attempts:
            row.status = "quarantined"
            row.locked_by = None
            row.locked_until = None
            row.last_error = sanitized
            self._session.add(
                TelegramQueueFailure(
                    queue_name=TelegramOutgoingQueue.__tablename__,
                    queue_row_id=row.id,
                    job_id=row.job_id,
                    chat_id=row.chat_id,
                    topic_id=row.topic_id,
                    trigger_message_id=row.trigger_message_id,
                    attempts=row.attempts,
                    error=sanitized,
                    payload_json=_json_dumps({"text": row.text[:256], "schema_version": row.schema_version}),
                )
            )
        else:
            row.status = "queued"
            row.locked_by = None
            row.locked_until = None
            row.last_error = sanitized
            if retry_after_seconds is not None:
                row.not_before = _now() + timedelta(seconds=max(1, retry_after_seconds))
                row.attempts = max(0, row.attempts - 1)
        self._session.commit()
        return True

    @staticmethod
    def _to_outgoing_item(row: TelegramOutgoingQueue) -> OutgoingQueueItem:
        reply_markup = json.loads(row.reply_markup_json) if row.reply_markup_json else None
        return OutgoingQueueItem(
            id=row.id,
            job_id=row.job_id,
            chat_id=row.chat_id,
            topic_id=row.topic_id,
            trigger_message_id=row.trigger_message_id,
            sent_message_id=row.sent_message_id,
            text=row.text,
            parse_mode=row.parse_mode,
            reply_markup=reply_markup if isinstance(reply_markup, dict) else None,
            attempts=row.attempts,
            locked_by=row.locked_by,
        )


class TelegramProcessHealthRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def heartbeat(
        self,
        *,
        process_name: str,
        process_kind: str,
        status: str = "running",
        chat_id: int | None = None,
        topic_id: int | None = None,
        metrics: dict[str, Any] | None = None,
        last_error: str | None = None,
        pid: int | None = None,
    ) -> None:
        row = self._session.scalar(
            select(TelegramProcessHealth).where(TelegramProcessHealth.process_name == process_name)
        )
        if row is None:
            row = TelegramProcessHealth(process_name=process_name, process_kind=process_kind)
            self._session.add(row)
        row.process_kind = process_kind
        row.status = status
        row.chat_id = chat_id
        row.topic_id = topic_id
        row.pid = pid if pid is not None else os.getpid()
        row.last_heartbeat_at = _now()
        row.last_error = last_error[:MAX_STORED_ERROR_CHARS] if last_error else None
        row.metrics_json = _json_dumps(metrics or {})
        self._session.commit()
