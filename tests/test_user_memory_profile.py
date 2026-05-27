from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from amo_bot.db.models import Base
from amo_bot.db.repositories import UserMemoryProfileRepository


def _repo() -> UserMemoryProfileRepository:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session = Session(engine, future=True)
    return UserMemoryProfileRepository(session)


def test_profile_create_read_replace_private_user() -> None:
    repo = _repo()

    created = repo.replace_profile(
        scope_type="private_user",
        user_id=101,
        profile={
            "language": "de",
            "timezone": "Europe/Berlin",
            "verbosity": "medium",
            "interests": ["python", "automation"],
        },
    )
    assert created.profile["language"] == "de"

    fetched = repo.get_profile(scope_type="private_user", user_id=101)
    assert fetched.profile["timezone"] == "Europe/Berlin"

    replaced = repo.replace_profile(
        scope_type="private_user",
        user_id=101,
        profile={"language": "en", "verbosity": "low"},
    )
    assert replaced.profile == {"language": "en", "verbosity": "low"}


def test_profile_rejects_disallowed_sensitive_or_detail_rich_fields() -> None:
    repo = _repo()

    stored = repo.replace_profile(
        scope_type="private_user",
        user_id=202,
        profile={
            "language": "de",
            "raw_memory": "full transcript text",
            "notes": "private details",
            "communication_style": "detailed",
            "interests": ["x" * 200, "ok"],
            "interaction_preferences": ["short answers", "short answers"],
            "api_token": "secret",
        },
    )

    assert "raw_memory" not in stored.profile
    assert "notes" not in stored.profile
    assert "api_token" not in stored.profile
    assert stored.profile["communication_style"] == "detailed"
    assert len(stored.profile["interests"][0]) == 80
    assert stored.profile["interaction_preferences"] == ["short answers"]


def test_profile_missing_returns_safe_default() -> None:
    repo = _repo()

    missing = repo.get_profile(scope_type="private_user", user_id=303)
    assert missing.profile == {}


def test_profile_scope_isolation_cross_user_and_cross_scope() -> None:
    repo = _repo()

    repo.replace_profile(
        scope_type="topic",
        chat_id=-1000,
        topic_id=77,
        user_id=404,
        profile={"language": "de", "verbosity": "high"},
    )

    wrong_user = repo.get_profile(scope_type="topic", chat_id=-1000, topic_id=77, user_id=405)
    assert wrong_user.profile == {}

    wrong_scope = repo.get_profile(scope_type="group_chat", chat_id=-1000, user_id=404)
    assert wrong_scope.profile == {}

    right_scope = repo.get_profile(scope_type="topic", chat_id=-1000, topic_id=77, user_id=404)
    assert right_scope.profile == {"language": "de", "verbosity": "high"}


def test_profile_scope_validation_denies_invalid_scope_shape() -> None:
    repo = _repo()

    try:
        repo.get_profile(scope_type="topic", chat_id=-1, user_id=1)
        assert False, "expected ValueError"
    except ValueError:
        pass

    try:
        repo.get_profile(scope_type="group_chat", user_id=1)
        assert False, "expected ValueError"
    except ValueError:
        pass
