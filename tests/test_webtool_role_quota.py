"""Tests for Webtool role quota enforcement (Issue #48)."""

from __future__ import annotations

import pytest

from amo_bot.auth.roles import Role
from amo_bot.db.base import create_session_factory
from amo_bot.db.init_db import init_db
from amo_bot.db.models import WebToolAuditEvent, WebToolQuotaCounter
from amo_bot.db.repositories import WebToolRoleQuotaRepository, WebToolQuotaDecision


@pytest.fixture
def db_url(tmp_path):
    url = f"sqlite:///{tmp_path / 'webtool_quota.db'}"
    init_db(url)
    return url


@pytest.fixture
def session_factory(db_url):
    return create_session_factory(db_url)


class TestWebToolRoleQuotaDefaults:
    """Default quota settings per role."""

    def test_owner_unlimited(self, session_factory) -> None:
        with session_factory() as s:
            repo = WebToolRoleQuotaRepository(s)
            rec = repo.get_role_quota(Role.OWNER)
            assert rec.mode == "unlimited"
            assert rec.daily_limit is None

    def test_admin_unlimited(self, session_factory) -> None:
        with session_factory() as s:
            repo = WebToolRoleQuotaRepository(s)
            rec = repo.get_role_quota(Role.ADMIN)
            assert rec.mode == "unlimited"

    def test_vip_unlimited(self, session_factory) -> None:
        with session_factory() as s:
            repo = WebToolRoleQuotaRepository(s)
            rec = repo.get_role_quota(Role.VIP)
            assert rec.mode == "unlimited"

    def test_normal_unlimited(self, session_factory) -> None:
        with session_factory() as s:
            repo = WebToolRoleQuotaRepository(s)
            rec = repo.get_role_quota(Role.NORMAL)
            assert rec.mode == "unlimited"

    def test_ignore_disabled(self, session_factory) -> None:
        with session_factory() as s:
            repo = WebToolRoleQuotaRepository(s)
            rec = repo.get_role_quota(Role.IGNORE)
            assert rec.mode == "disabled"


class TestWebToolRoleQuotaRepository:
    """Repository CRUD operations."""

    def test_list_role_quotas_returns_all_roles(self, session_factory) -> None:
        with session_factory() as s:
            repo = WebToolRoleQuotaRepository(s)
            quotas = repo.list_role_quotas()
        role_names = {q.role for q in quotas}
        assert role_names == {Role.OWNER, Role.ADMIN, Role.VIP, Role.NORMAL, Role.IGNORE}

    def test_upsert_sets_limited_mode(self, session_factory) -> None:
        with session_factory() as s:
            repo = WebToolRoleQuotaRepository(s)
            rec = repo.upsert_role_quota(
                role=Role.NORMAL,
                mode="limited",
                daily_limit=5,
                updated_by_telegram_user_id=999,
            )
            assert rec.mode == "limited"
            assert rec.daily_limit == 5

        # Verify persisted
        with session_factory() as s:
            repo = WebToolRoleQuotaRepository(s)
            rec = repo.get_role_quota(Role.NORMAL)
            assert rec.mode == "limited"
            assert rec.daily_limit == 5

    def test_upsert_ignore_role_cannot_be_unlimited(self, session_factory) -> None:
        with session_factory() as s:
            repo = WebToolRoleQuotaRepository(s)
            with pytest.raises(ValueError, match="ignore role cannot be unlimited"):
                repo.upsert_role_quota(role=Role.IGNORE, mode="unlimited", daily_limit=None)

    def test_upsert_limited_requires_positive_limit(self, session_factory) -> None:
        with session_factory() as s:
            repo = WebToolRoleQuotaRepository(s)
            with pytest.raises(ValueError, match="daily_limit >= 1"):
                repo.upsert_role_quota(role=Role.NORMAL, mode="limited", daily_limit=0)

    def test_upsert_limited_accepts_none_limit(self, session_factory) -> None:
        # Setting limited without a limit should raise
        with session_factory() as s:
            repo = WebToolRoleQuotaRepository(s)
            with pytest.raises(ValueError, match="daily_limit >= 1"):
                repo.upsert_role_quota(role=Role.NORMAL, mode="limited", daily_limit=None)


class TestWebToolQuotaCounters:
    """Counter increment and scoping."""

    def test_get_current_count_initially_zero(self, session_factory) -> None:
        with session_factory() as s:
            repo = WebToolRoleQuotaRepository(s)
            count = repo.get_current_count(
                user_id=42,
                role=Role.NORMAL,
                chat_id=-100,
                message_thread_id=None,
                day="2026-05-29",
            )
            assert count == 0

    def test_increment_count_returns_new_value(self, session_factory) -> None:
        repo = WebToolRoleQuotaRepository(session_factory())
        day = "2026-05-29"

        c1 = repo.increment_count(user_id=42, role=Role.NORMAL, chat_id=-100, message_thread_id=None, day=day)
        c2 = repo.increment_count(user_id=42, role=Role.NORMAL, chat_id=-100, message_thread_id=None, day=day)
        assert c1 == 1
        assert c2 == 2

    def test_counter_scoped_per_chat(self, session_factory) -> None:
        day = "2026-05-29"
        repo = WebToolRoleQuotaRepository(session_factory())

        c1 = repo.increment_count(user_id=42, role=Role.NORMAL, chat_id=-100, message_thread_id=None, day=day)
        c2 = repo.increment_count(user_id=42, role=Role.NORMAL, chat_id=-200, message_thread_id=None, day=day)
        # Each chat has independent counter
        assert c1 == 1
        assert c2 == 1

    def test_counter_scoped_per_thread(self, session_factory) -> None:
        day = "2026-05-29"
        repo = WebToolRoleQuotaRepository(session_factory())

        c1 = repo.increment_count(user_id=42, role=Role.NORMAL, chat_id=-100, message_thread_id=1, day=day)
        c2 = repo.increment_count(user_id=42, role=Role.NORMAL, chat_id=-100, message_thread_id=2, day=day)
        assert c1 == 1
        assert c2 == 1

    def test_counter_scoped_per_day(self, session_factory) -> None:
        repo = WebToolRoleQuotaRepository(session_factory())
        repo.increment_count(user_id=42, role=Role.NORMAL, chat_id=-100, message_thread_id=None, day="2026-05-28")
        c = repo.increment_count(user_id=42, role=Role.NORMAL, chat_id=-100, message_thread_id=None, day="2026-05-29")
        assert c == 1


class TestWebToolCheckQuota:
    """check_quota() decision logic."""

    def test_disabled_role_denies(self, session_factory) -> None:
        with session_factory() as s:
            repo = WebToolRoleQuotaRepository(s)
            decision = repo.check_quota(
                user_id=42,
                role=Role.IGNORE,
                chat_id=42,
                message_thread_id=None,
                operation_type="websearch",
                day="2026-05-29",
            )
        assert decision.allowed is False
        assert decision.decision == "disabled"
        assert decision.reason == "role_disabled"
        assert decision.remaining is None

    def test_unlimited_role_allows(self, session_factory) -> None:
        with session_factory() as s:
            repo = WebToolRoleQuotaRepository(s)
            decision = repo.check_quota(
                user_id=42,
                role=Role.OWNER,
                chat_id=42,
                message_thread_id=None,
                operation_type="websearch",
                day="2026-05-29",
            )
        assert decision.allowed is True
        assert decision.decision == "allow"
        assert decision.reason == "unlimited"
        assert decision.remaining is None

    def test_limited_role_allows_under_limit(self, session_factory) -> None:
        with session_factory() as s:
            repo = WebToolRoleQuotaRepository(s)
            repo.upsert_role_quota(role=Role.NORMAL, mode="limited", daily_limit=5)

        decision = repo.check_quota(
            user_id=42,
            role=Role.NORMAL,
            chat_id=-100,
            message_thread_id=None,
            operation_type="webscraping",
            day="2026-05-29",
        )
        assert decision.allowed is True
        assert decision.decision == "allow"
        assert decision.reason == "within_limit"
        assert decision.remaining == 4
        assert decision.current_count == 1
        assert decision.limit == 5

    def test_limited_role_denies_at_limit(self, session_factory) -> None:
        with session_factory() as s:
            repo = WebToolRoleQuotaRepository(s)
            repo.upsert_role_quota(role=Role.NORMAL, mode="limited", daily_limit=2)

        # Exhaust the limit
        for i in range(2):
            repo.check_quota(
                user_id=42,
                role=Role.NORMAL,
                chat_id=-100,
                message_thread_id=None,
                operation_type="browser",
                day="2026-05-29",
            )

        decision = repo.check_quota(
            user_id=42,
            role=Role.NORMAL,
            chat_id=-100,
            message_thread_id=None,
            operation_type="browser",
            day="2026-05-29",
        )
        assert decision.allowed is False
        assert decision.decision == "quota_exceeded"
        assert decision.reason == "daily_limit_reached"
        assert decision.remaining == 0

    def test_check_quota_includes_operation_type(self, session_factory) -> None:
        with session_factory() as s:
            repo = WebToolRoleQuotaRepository(s)
            decision = repo.check_quota(
                user_id=42,
                role=Role.VIP,
                chat_id=-100,
                message_thread_id=None,
                operation_type="websearch",
                day="2026-05-29",
            )
        assert decision.operation_type == "websearch"
        assert decision.role == Role.VIP

    def test_check_quota_returns_timing_ms(self, session_factory) -> None:
        with session_factory() as s:
            repo = WebToolRoleQuotaRepository(s)
            decision = repo.check_quota(
                user_id=42,
                role=Role.ADMIN,
                chat_id=-100,
                message_thread_id=None,
                operation_type="webscraping",
                day="2026-05-29",
            )
        assert decision.timing_ms is not None
        assert decision.timing_ms >= 0


class TestWebToolAuditEvents:
    """Audit events are metadata-only (no query content, URLs, prompts, secrets)."""

    def test_audit_written_on_every_decision(self, session_factory) -> None:
        """Audit is written for every decision, including unlimited (count=0 when not incremented)."""
        with session_factory() as s:
            repo = WebToolRoleQuotaRepository(s)
            decision = repo.check_quota(
                user_id=42,
                role=Role.IGNORE,  # disabled → count=0 in audit
                chat_id=42,
                message_thread_id=None,
                operation_type="websearch",
                day="2026-05-29",
            )
            assert decision.allowed is False

        with session_factory() as s:
            events = s.query(WebToolAuditEvent).all()
            assert len(events) == 1
            ev = events[0]
            assert ev.user_id == 42
            assert ev.role == "ignore"
            assert ev.operation_type == "websearch"
            assert ev.decision == "disabled"
            assert ev.count == 0
            assert ev.timing_ms is not None
            assert ev.error is None

    def test_audit_written_on_deny(self, session_factory) -> None:
        with session_factory() as s:
            repo = WebToolRoleQuotaRepository(s)
            repo.upsert_role_quota(role=Role.NORMAL, mode="limited", daily_limit=1)
        repo.check_quota(user_id=42, role=Role.NORMAL, chat_id=-100, message_thread_id=None, operation_type="browser", day="2026-05-29")
        decision = repo.check_quota(user_id=42, role=Role.NORMAL, chat_id=-100, message_thread_id=None, operation_type="browser", day="2026-05-29")

        with session_factory() as s:
            events = s.query(WebToolAuditEvent).all()
            assert len(events) == 2
            deny_event = events[1]
            assert deny_event.decision == "quota_exceeded"
            assert deny_event.remaining == 0

    def test_audit_is_metadata_only(self, session_factory) -> None:
        """Audit stores no query content, URLs, prompts, or secrets."""
        with session_factory() as s:
            repo = WebToolRoleQuotaRepository(s)
            repo.check_quota(
                user_id=99,
                role=Role.VIP,
                chat_id=-300,
                message_thread_id=5,
                operation_type="webscraping",
                day="2026-06-01",
            )

        with session_factory() as s:
            events = s.query(WebToolAuditEvent).all()
            assert len(events) == 1
            ev = events[0]
            # Metadata fields
            assert ev.user_id == 99
            assert ev.role == "vip"
            assert ev.chat_id == -300
            assert ev.message_thread_id == 5
            assert ev.operation_type == "webscraping"
            assert ev.decision == "allow"
            # No content fields should exist on the model
            assert not hasattr(ev, "query") or ev.query is None
            assert not hasattr(ev, "url") or ev.url is None
            assert not hasattr(ev, "prompt") or ev.prompt is None
            assert not hasattr(ev, "response") or ev.response is None
            assert not hasattr(ev, "error") or ev.error is None


class TestWebToolQuotaDecisionDataclass:
    """WebToolQuotaDecision has all required fields."""

    def test_decision_fields(self) -> None:
        decision = WebToolQuotaDecision(
            allowed=True,
            decision="allow",
            role=Role.ADMIN,
            operation_type="browser",
            current_count=3,
            limit=10,
            remaining=7,
            reason="within_limit",
            error=None,
            timing_ms=5,
        )
        assert decision.allowed is True
        assert decision.decision == "allow"
        assert decision.role == Role.ADMIN
        assert decision.operation_type == "browser"
        assert decision.current_count == 3
        assert decision.limit == 10
        assert decision.remaining == 7
        assert decision.reason == "within_limit"
        assert decision.error is None
        assert decision.timing_ms == 5

    def test_decision_denied_fields(self) -> None:
        decision = WebToolQuotaDecision(
            allowed=False,
            decision="quota_exceeded",
            role=Role.NORMAL,
            operation_type="websearch",
            current_count=5,
            limit=5,
            remaining=0,
            reason="daily_limit_reached",
            error=None,
            timing_ms=2,
        )
        assert decision.allowed is False
        assert decision.remaining == 0
