from __future__ import annotations

from datetime import datetime, timezone

from amo_bot.auth.roles import Role
from amo_bot.db.models import User

CONSENT_PENDING = "pending"
CONSENT_ACCEPTED = "accepted"
CONSENT_DECLINED = "declined"
CONSENT_UNREACHABLE = "unreachable"
CONSENT_STATUSES: tuple[str, ...] = (
    CONSENT_PENDING,
    CONSENT_ACCEPTED,
    CONSENT_DECLINED,
    CONSENT_UNREACHABLE,
)


class ConsentService:
    def get_status(self, user_or_user_id: User | int) -> str:
        user = self._coerce_user(user_or_user_id)
        status = (user.consent_status or CONSENT_ACCEPTED).strip().lower()
        if status not in CONSENT_STATUSES:
            return CONSENT_ACCEPTED
        return status

    def ensure_pending_for_new_user(self, user: User, now: datetime | None = None) -> bool:
        current_status = (user.consent_status or "").strip().lower()
        if current_status in {CONSENT_DECLINED, CONSENT_UNREACHABLE}:
            return False

        changed = current_status != CONSENT_PENDING
        if changed:
            user.consent_updated_at = self._now(now)
        user.consent_status = CONSENT_PENDING
        return changed

    def accept(self, user: User, source: str | None = None, now: datetime | None = None) -> bool:
        return self._set_status(user, CONSENT_ACCEPTED, now=now)

    def decline(self, user: User, source: str | None = None, now: datetime | None = None) -> bool:
        return self._set_status(user, CONSENT_DECLINED, now=now)

    def mark_unreachable(self, user: User, now: datetime | None = None) -> bool:
        return self._set_status(user, CONSENT_UNREACHABLE, now=now)

    def record_prompt(self, user: User, now: datetime | None = None) -> None:
        user.consent_prompt_count = int(user.consent_prompt_count or 0) + 1
        user.consent_prompted_at = self._now(now)

    def is_consent_satisfied(self, user: User, is_owner: bool = False) -> bool:
        if is_owner:
            return True
        return self.get_status(user) == CONSENT_ACCEPTED

    def is_effectively_blocked(
        self,
        user: User,
        global_role: Role | str | None = None,
        is_owner: bool = False,
    ) -> bool:
        role_value = global_role.value if isinstance(global_role, Role) else (global_role or "")
        if role_value == Role.IGNORE.value:
            return True
        if is_owner:
            return False
        return self.get_status(user) in {CONSENT_PENDING, CONSENT_DECLINED, CONSENT_UNREACHABLE}

    def _set_status(self, user: User, status: str, now: datetime | None = None) -> bool:
        status = status.strip().lower()
        if status not in CONSENT_STATUSES:
            raise ValueError(f"unsupported consent status: {status}")
        changed = self.get_status(user) != status
        user.consent_status = status
        user.consent_updated_at = self._now(now)
        return changed

    @staticmethod
    def _coerce_user(user_or_user_id: User | int) -> User:
        if isinstance(user_or_user_id, User):
            return user_or_user_id
        raise TypeError("ConsentService expects a User model instance")

    @staticmethod
    def _now(now: datetime | None) -> datetime:
        if now is not None:
            if now.tzinfo is None:
                return now.replace(tzinfo=timezone.utc)
            return now.astimezone(timezone.utc)
        return datetime.now(timezone.utc)
