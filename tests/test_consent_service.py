from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from amo_bot.auth.roles import Role
from amo_bot.consent.service import ConsentService
from amo_bot.db.models import Base, User


def _user(status: str | None = None, prompt_count: int | None = 0) -> User:
    user = User(telegram_user_id=123456789, role_id=1)
    user.consent_status = status
    user.consent_prompt_count = prompt_count
    return user


def test_ensure_pending_for_new_user_sets_pending() -> None:
    svc = ConsentService()
    user = _user(status=None)

    changed = svc.ensure_pending_for_new_user(user)

    assert changed is True
    assert user.consent_status == "pending"
    assert user.consent_updated_at is not None


def test_ensure_pending_for_new_user_on_accepted_sets_pending() -> None:
    svc = ConsentService()
    user = _user(status="accepted")

    changed = svc.ensure_pending_for_new_user(user)

    assert changed is True
    assert user.consent_status == "pending"
    assert user.consent_updated_at is not None


def test_ensure_pending_for_new_user_is_idempotent_on_pending() -> None:
    svc = ConsentService()
    user = _user(status="pending")

    first_changed = svc.ensure_pending_for_new_user(user)
    first_updated_at = user.consent_updated_at
    second_changed = svc.ensure_pending_for_new_user(user)

    assert first_changed is False
    assert second_changed is False
    assert user.consent_status == "pending"
    assert user.consent_updated_at == first_updated_at


def test_ensure_pending_for_new_user_overrides_orm_default_accepted_after_flush() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine)
    svc = ConsentService()

    with Session(bind=engine) as session:
        user = User(telegram_user_id=999001, role_id=1)
        session.add(user)
        session.flush()

        assert user.consent_status == "accepted"

        changed = svc.ensure_pending_for_new_user(user)

        assert changed is True
        assert user.consent_status == "pending"
        assert user.consent_updated_at is not None


def test_ensure_pending_for_new_user_preserves_declined_and_unreachable() -> None:
    svc = ConsentService()
    declined = _user(status="declined")
    unreachable = _user(status="unreachable")

    declined_changed = svc.ensure_pending_for_new_user(declined)
    unreachable_changed = svc.ensure_pending_for_new_user(unreachable)

    assert declined_changed is False
    assert unreachable_changed is False
    assert declined.consent_status == "declined"
    assert unreachable.consent_status == "unreachable"


def test_pending_to_accepted() -> None:
    svc = ConsentService()
    user = _user(status="pending")

    changed = svc.accept(user)

    assert changed is True
    assert user.consent_status == "accepted"


def test_pending_to_declined() -> None:
    svc = ConsentService()
    user = _user(status="pending")

    changed = svc.decline(user)

    assert changed is True
    assert user.consent_status == "declined"


def test_declined_to_accepted() -> None:
    svc = ConsentService()
    user = _user(status="declined")

    changed = svc.accept(user)

    assert changed is True
    assert user.consent_status == "accepted"


def test_unreachable_is_blocked() -> None:
    svc = ConsentService()
    user = _user(status="unreachable")

    assert svc.is_effectively_blocked(user, global_role=Role.NORMAL, is_owner=False) is True


def test_record_prompt_increments_and_sets_timestamp() -> None:
    svc = ConsentService()
    user = _user(status="pending", prompt_count=1)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    svc.record_prompt(user, now=now)

    assert user.consent_prompt_count == 2
    assert user.consent_prompted_at == now


def test_effective_blocking_matrix() -> None:
    svc = ConsentService()
    accepted = _user(status="accepted")
    pending = _user(status="pending")
    declined = _user(status="declined")
    unreachable = _user(status="unreachable")

    assert svc.is_effectively_blocked(accepted, global_role=Role.NORMAL, is_owner=False) is False
    assert svc.is_effectively_blocked(pending, global_role=Role.NORMAL, is_owner=False) is True
    assert svc.is_effectively_blocked(declined, global_role=Role.NORMAL, is_owner=False) is True
    assert svc.is_effectively_blocked(unreachable, global_role=Role.NORMAL, is_owner=False) is True


def test_global_ignore_always_blocks_even_for_owner() -> None:
    svc = ConsentService()
    user = _user(status="accepted")

    assert svc.is_effectively_blocked(user, global_role=Role.IGNORE, is_owner=True) is True


def test_owner_bypasses_consent_only() -> None:
    svc = ConsentService()
    user = _user(status="declined")

    assert svc.is_consent_satisfied(user, is_owner=True) is True
    assert svc.is_effectively_blocked(user, global_role=Role.NORMAL, is_owner=True) is False
