from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from amo_bot.db.models import Base
from amo_bot.ai.memory_c2_service import MemoryC2Service, MemoryScope
from amo_bot.db.repositories import TopicAgentMemoryRepository, UserMemoryProfileRepository
from amo_bot.telegram.chat_topic_persistence import _extract_coarse_profile_candidate


def _repos() -> tuple[UserMemoryProfileRepository, TopicAgentMemoryRepository]:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session = Session(engine, future=True)
    return UserMemoryProfileRepository(session), TopicAgentMemoryRepository(session)


def _repo() -> UserMemoryProfileRepository:
    return _repos()[0]


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


def test_profile_candidate_update_allows_coarse_fields_only() -> None:
    profile_repo, memory_repo = _repos()
    service = MemoryC2Service(repository=memory_repo, profile_repository=profile_repo)

    result = service.apply_profile_candidate(
        scope=MemoryScope(scope_type="private_user", user_id=777),
        candidate={
            "language": "de",
            "verbosity": "medium",
            "interests": ["python", "ai"],
            "api_token": "secret",
            "message_text": "my private transcript",
        },
    )

    assert result.applied is True
    assert result.profile["language"] == "de"
    assert result.profile["verbosity"] == "medium"
    assert "api_token" not in result.profile
    assert "message_text" not in result.profile


def test_profile_candidate_update_rejects_private_sensitive_detail_rich() -> None:
    profile_repo, memory_repo = _repos()
    service = MemoryC2Service(repository=memory_repo, profile_repository=profile_repo)

    result = service.apply_profile_candidate(
        scope=MemoryScope(scope_type="private_user", user_id=888),
        candidate={
            "language": "alice@example.com",
            "timezone": "+49 170 1234567",
            "interests": ["A" * 200],
            "avoid_topics": ["contact me at bob@example.com"],
            "notes": "full chat transcript with private details",
        },
    )

    assert result.applied is False
    assert result.profile == {}


def test_profile_candidate_update_noop_on_empty_or_rejected() -> None:
    profile_repo, memory_repo = _repos()
    service = MemoryC2Service(repository=memory_repo, profile_repository=profile_repo)

    result = service.apply_profile_candidate(
        scope=MemoryScope(scope_type="private_user", user_id=999),
        candidate={},
    )
    assert result.applied is False
    assert result.profile == {}


def test_profile_candidate_cross_scope_no_private_to_topic_leak() -> None:
    profile_repo, memory_repo = _repos()
    service = MemoryC2Service(repository=memory_repo, profile_repository=profile_repo)

    private_result = service.apply_profile_candidate(
        scope=MemoryScope(scope_type="private_user", user_id=1234),
        candidate={"language": "de", "interests": ["python"]},
    )
    assert private_result.applied is True

    topic_result = service.apply_profile_candidate(
        scope=MemoryScope(scope_type="topic", chat_id=-100, topic_id=42, user_id=1234),
        candidate={"message_text": "private details", "api_token": "abc123"},
    )
    assert topic_result.applied is False
    assert topic_result.profile == {}

    private_profile = profile_repo.get_profile(scope_type="private_user", user_id=1234)
    topic_profile = profile_repo.get_profile(scope_type="topic", chat_id=-100, topic_id=42, user_id=1234)
    assert private_profile.profile == {"language": "de", "interests": ["python"]}
    assert topic_profile.profile == {}


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


def test_list_profiles_for_users_returns_only_requested_scope_and_users() -> None:
    repo = _repo()

    repo.replace_profile(scope_type="topic", chat_id=-100, topic_id=1, user_id=1, profile={"language": "de"})
    repo.replace_profile(scope_type="topic", chat_id=-100, topic_id=1, user_id=2, profile={"verbosity": "low"})
    repo.replace_profile(scope_type="topic", chat_id=-100, topic_id=2, user_id=1, profile={"language": "en"})
    repo.replace_profile(scope_type="private_user", user_id=1, profile={"tone_preference": "direct"})

    rows = repo.list_profiles_for_users(scope_type="topic", chat_id=-100, topic_id=1, user_ids=[2, 1, 999], limit_users=5)

    assert [row.user_id for row in rows] == [2, 1]
    assert rows[0].profile == {"verbosity": "low"}
    assert rows[1].profile == {"language": "de"}


def test_profile_allows_context_role_but_still_rejects_sensitive_details() -> None:
    profile_repo, memory_repo = _repos()
    service = MemoryC2Service(repository=memory_repo, profile_repository=profile_repo)

    result = service.apply_profile_candidate(
        scope=MemoryScope(scope_type="topic", chat_id=-200, topic_id=3, user_id=44),
        candidate={
            "context_role": "tester",
            "language": "de",
            "email": "person@example.org",
            "notes": "full private dossier",
        },
    )

    assert result.applied is True
    assert result.profile == {"context_role": "tester", "language": "de"}
    assert "email" in result.rejected_keys
    assert "notes" in result.rejected_keys


def test_auto_coarse_profile_candidate_extracts_only_clear_preferences() -> None:
    candidate = _extract_coarse_profile_candidate("Ich bin hier Tester, sprich deutsch mit mir und antworte mir lieber kurz in Stichpunkte.")

    assert candidate["context_role"] == "tester"
    assert candidate["language"] == "de"
    assert candidate["verbosity"] == "low"
    assert candidate["communication_style"] == "brief"
    assert candidate["format_preference"] == "bullet_points"

    assert _extract_coarse_profile_candidate("Meine Telefonnummer ist +49 170 1234567") == {}
