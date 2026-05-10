from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from amo_bot.db.models import WebuiAccessWindow


@dataclass(slots=True)
class WebuiAccessStatus:
    open: bool
    remaining_seconds: int
    enabled_until: datetime | None


class WebuiAccessWindowService:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    @staticmethod
    def _utc_now(now_utc: datetime | None) -> datetime:
        if now_utc is None:
            return datetime.now(UTC)
        if now_utc.tzinfo is None:
            return now_utc.replace(tzinfo=UTC)
        return now_utc.astimezone(UTC)

    @staticmethod
    def _to_utc(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def _get_or_create(self, session: Session) -> WebuiAccessWindow:
        row = session.scalar(select(WebuiAccessWindow).where(WebuiAccessWindow.id == 1))
        if row is None:
            row = WebuiAccessWindow(id=1)
            session.add(row)
            session.flush()
        return row

    def enable_for_one_hour(self, actor_id: int, now_utc: datetime | None = None) -> datetime:
        now = self._utc_now(now_utc)
        enabled_until = now + timedelta(hours=1)
        with self._session_factory() as session:
            row = self._get_or_create(session)
            row.enabled_until = enabled_until
            row.updated_by_telegram_id = actor_id
            row.updated_at = now
            session.commit()
        return enabled_until

    def disable(self, actor_id: int, now_utc: datetime | None = None) -> None:
        now = self._utc_now(now_utc)
        with self._session_factory() as session:
            row = self._get_or_create(session)
            row.enabled_until = None
            row.updated_by_telegram_id = actor_id
            row.updated_at = now
            session.commit()

    def get_status(self, now_utc: datetime | None = None) -> WebuiAccessStatus:
        now = self._utc_now(now_utc)
        with self._session_factory() as session:
            row = session.scalar(select(WebuiAccessWindow).where(WebuiAccessWindow.id == 1))
            enabled_until = self._to_utc(row.enabled_until) if row is not None else None

        if enabled_until is None or enabled_until <= now:
            return WebuiAccessStatus(open=False, remaining_seconds=0, enabled_until=enabled_until)

        remaining_seconds = int((enabled_until - now).total_seconds())
        return WebuiAccessStatus(open=True, remaining_seconds=remaining_seconds, enabled_until=enabled_until)

    def is_open(self, now_utc: datetime | None = None) -> bool:
        return self.get_status(now_utc=now_utc).open
