from __future__ import annotations

import os

from sqlalchemy import select

from amo_bot.config.settings import Settings
from amo_bot.db.base import create_session_factory
from amo_bot.db.init_db import init_db
from amo_bot.db.models import TelegramChat, TelegramTopic
from amo_bot.db.repositories import ChatScopedRoleRepository, ChatSeenUserRepository, ChatTopicRepository, UserRoleRepository
from amo_bot.webui.flask_app import create_flask_app


def _make_settings(database_url: str, password: str = "test-secret", owner_id: int | None = None) -> Settings:
    payload = {
        "BOT_TOKEN": "dummy-token",
        "TELEGRAM_API_BASE": "https://api.telegram.org",
        "POLL_TIMEOUT_SECONDS": 30,
        "POLL_LIMIT": 100,
        "POLL_RETRY_MAX_SECONDS": 30,
        "OFFSET_STATE_FILE": ".state/offset.json",
        "DATABASE_URL": database_url,
        "AMO_PLUGIN_DIR": "./plugins",
        "WEBUI_HOST": "127.0.0.1",
        "WEBUI_PORT": 8080,
        "WEBUI_PASSWORD": password,
        "WEBUI_SECRET_KEY": "test-secret-key-0123456789-abcdef",
        "WEBUI_PUBLIC_MODE": False,
        "WEBUI_REQUIRE_HTTPS": False,
        "WEBUI_SESSION_COOKIE_SECURE": False,
        "WEBUI_LOGIN_DELAY_BASE_SECONDS": 0.25,
        "WEBUI_LOGIN_DELAY_MAX_SECONDS": 1.0,
    }
    if owner_id is not None:
        payload["WEBUI_OWNER_TELEGRAM_ID"] = owner_id
    else:
        os.environ.pop("WEBUI_OWNER_TELEGRAM_ID", None)
    return Settings(_env_file=None, **payload)


def _extract_csrf_token(html: str) -> str:
    marker = 'name="csrf_token" type="hidden" value="'
    start = html.find(marker)
    assert start != -1, "csrf token field missing"
    start += len(marker)
    end = html.find('"', start)
    assert end != -1
    return html[start:end]


def _login(client, password: str) -> None:
    page = client.get("/login")
    token = _extract_csrf_token(page.get_data(as_text=True))
    resp = client.post("/login", data={"password": password, "csrf_token": token}, follow_redirects=False)
    assert resp.status_code == 302


def _seed_chat_topic(db_url: str, chat_id: int, thread_id: int) -> None:
    sf = create_session_factory(db_url)
    with sf() as s:
        repo = ChatTopicRepository(s)
        repo.upsert_chat(
            chat_id=chat_id,
            chat_type="supergroup",
            title="Test Group",
            username="testgroup",
        )
        repo.upsert_topic(chat_id=chat_id, message_thread_id=thread_id, telegram_topic_name="General")


def test_groups_requires_login_redirects_to_login(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'groups1.db'}"
    init_db(db_url)
    settings = _make_settings(db_url)
    app = create_flask_app(settings=settings)

    with app.test_client() as client:
        response = client.get("/groups", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_groups_with_login_renders_page_and_empty_state(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'groups2.db'}"
    init_db(db_url)
    settings = _make_settings(db_url)
    app = create_flask_app(settings=settings)

    with app.test_client() as client:
        _login(client, "test-secret")
        response = client.get("/groups")
        html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Groups" in html
    assert "Keine Gruppen vorhanden." in html


def test_groups_lists_seeded_chat_and_topic(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'groups3.db'}"
    init_db(db_url)
    _seed_chat_topic(db_url, -100123, 77)

    settings = _make_settings(db_url)
    app = create_flask_app(settings=settings)

    with app.test_client() as client:
        _login(client, "test-secret")
        response = client.get("/groups")
        html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "-100123" in html
    assert "Test Group" in html
    assert "77" in html
    assert 'href="/groups/-100123"' in html



def test_groups_topic_overview_declutter_keeps_roles_section_and_role_form_action(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'groups_declutter_roles_guard.db'}"
    init_db(db_url)

    _seed_chat_topic(db_url, -1004010, 401)
    _seed_user(db_url, 7401)

    sf = create_session_factory(db_url)
    with sf() as s:
        ChatSeenUserRepository(s).mark_seen(chat_id=-1004010, telegram_user_id=7401)

    settings = _make_settings(db_url, owner_id=777)
    app = create_flask_app(settings=settings)

    with app.test_client() as client:
        _login(client, "test-secret")
        response = client.get("/groups")
        html = response.get_data(as_text=True)

    assert response.status_code == 200

    # B6 guard: overview only, no inline topic edit forms on /groups
    assert 'action="/groups/-1004010/topics/401"' not in html
    assert 'name="display_name"' not in html
    assert 'name="notes"' not in html

    # B6 guard: clear details entry remains
    assert 'href="/groups/-1004010"' in html

    # QA guard: roles area is still separate and workflow entry remains functional
    assert "Gruppenrollen" in html
    assert 'action="/groups/-1004010/roles"' in html
    assert 'name="telegram_user_id"' in html
    assert 'name="role"' in html

def test_group_detail_page_renders_group_metadata_topics_and_metadata_form(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'groups_detail1.db'}"
    init_db(db_url)
    _seed_chat_topic(db_url, -100401, 201)

    settings = _make_settings(db_url)
    app = create_flask_app(settings=settings)

    with app.test_client() as client:
        _login(client, "test-secret")
        response = client.get("/groups/-100401")
        html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Group Detail" in html
    assert "Test Group" in html
    assert "-100401" in html
    assert "Topics" in html
    assert "201" in html
    assert "General" in html
    assert "Back to Groups" in html
    assert 'action="/groups/-100401/topics/201"' in html
    assert 'name="display_name"' in html
    assert 'name="notes"' in html
    assert 'name="enabled"' in html
    assert "Topic AI enabled" in html
    assert "Topic AI response mode" in html
    assert 'name="ai_enabled"' in html
    assert 'name="response_mode"' in html
    assert 'name="topic_soul_text"' in html
    assert "Basic metadata" in html
    assert "Topic Soul" in html
    assert "Advanced" in html
    assert "Owner-only mutation. Escaping is enforced on render." in html
    assert 'maxlength="4000"' in html


def test_group_detail_page_unknown_group_returns_404(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'groups_detail2.db'}"
    init_db(db_url)

    settings = _make_settings(db_url)
    app = create_flask_app(settings=settings)

    with app.test_client() as client:
        _login(client, "test-secret")
        response = client.get("/groups/-999999")

    assert response.status_code == 404


def test_topic_metadata_update_with_owner_id_persists(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'groups4.db'}"
    init_db(db_url)
    _seed_chat_topic(db_url, -100200, 88)

    settings = _make_settings(db_url, owner_id=777)
    app = create_flask_app(settings=settings)

    with app.test_client() as client:
        _login(client, "test-secret")
        page = client.get("/groups/-100200")
        token = _extract_csrf_token(page.get_data(as_text=True))
        response = client.post(
            "/groups/-100200/topics/88",
            data={
                "display_name": "Ops",
                "notes": "Runbook",
                "topic_soul_text": "Topic Soul v1",
                "enabled": "",
                "ai_enabled": "y",
                "response_mode": "mention_or_reply",
                "csrf_token": token,
            },
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/groups/-100200")

    sf = create_session_factory(db_url)
    with sf() as s:
        topic = s.scalar(
            select(TelegramTopic).where(TelegramTopic.chat_id == -100200, TelegramTopic.message_thread_id == 88)
        )
        assert topic is not None
        assert topic.display_name == "Ops"
        assert topic.notes == "Runbook"
        assert topic.enabled is False

        from amo_bot.db.models import TopicAgentConfig

        cfg = s.scalar(
            select(TopicAgentConfig).where(
                TopicAgentConfig.scope_type == "topic",
                TopicAgentConfig.chat_id == -100200,
                TopicAgentConfig.topic_id == 88,
            )
        )
        assert cfg is not None
        assert cfg.topic_soul_text == "Topic Soul v1"
        assert cfg.ai_enabled is True
        assert cfg.response_mode == "mention_or_reply"


def test_topic_metadata_update_without_owner_id_blocked(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'groups5.db'}"
    init_db(db_url)
    _seed_chat_topic(db_url, -100201, 89)

    settings = _make_settings(db_url)
    app = create_flask_app(settings=settings)

    with app.test_client() as client:
        _login(client, "test-secret")
        page = client.get("/groups/-100201")
        token = _extract_csrf_token(page.get_data(as_text=True))
        response = client.post(
            "/groups/-100201/topics/89",
            data={"display_name": "X", "notes": "Y", "topic_soul_text": "Denied edit", "enabled": "y", "csrf_token": token},
            follow_redirects=False,
        )

    assert response.status_code == 403


def test_topic_metadata_update_requires_csrf(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'groups6.db'}"
    init_db(db_url)
    _seed_chat_topic(db_url, -100202, 90)

    settings = _make_settings(db_url, owner_id=777)
    app = create_flask_app(settings=settings)

    with app.test_client() as client:
        _login(client, "test-secret")
        response = client.post(
            "/groups/-100202/topics/90",
            data={"display_name": "X", "notes": "Y", "topic_soul_text": "Needs csrf", "enabled": "y"},
            follow_redirects=False,
        )

    assert response.status_code == 400


def _seed_user(db_url: str, telegram_user_id: int, role: str = "normal") -> None:
    sf = create_session_factory(db_url)
    with sf() as s:
        from amo_bot.auth.roles import Role

        UserRoleRepository(s).set_user_role(
            actor_telegram_user_id=1,
            target_telegram_user_id=telegram_user_id,
            role=Role(role),
        )


def test_group_roles_section_renders_and_shows_default(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'groups_roles1.db'}"
    init_db(db_url)
    _seed_chat_topic(db_url, -100210, 91)
    _seed_user(db_url, 7001)

    sf = create_session_factory(db_url)
    with sf() as s:
        ChatSeenUserRepository(s).mark_seen(chat_id=-100210, telegram_user_id=7001)

    settings = _make_settings(db_url, owner_id=777)
    app = create_flask_app(settings=settings)

    with app.test_client() as client:
        _login(client, "test-secret")
        response = client.get("/groups")
        html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Gruppenrollen" in html
    assert "7001" in html
    assert "normal (default)" in html


def test_group_role_set_persists_scoped_to_chat(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'groups_roles2.db'}"
    init_db(db_url)
    _seed_chat_topic(db_url, -100211, 92)
    _seed_chat_topic(db_url, -100212, 93)
    _seed_user(db_url, 7002)

    sf = create_session_factory(db_url)
    with sf() as s:
        ChatSeenUserRepository(s).mark_seen(chat_id=-100211, telegram_user_id=7002)

    settings = _make_settings(db_url, owner_id=777)
    app = create_flask_app(settings=settings)

    with app.test_client() as client:
        _login(client, "test-secret")
        page = client.get("/groups")
        token = _extract_csrf_token(page.get_data(as_text=True))
        response = client.post(
            "/groups/-100211/roles",
            data={"telegram_user_id": "7002", "role": "admin", "csrf_token": token},
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/groups")

    with sf() as s:
        repo = ChatScopedRoleRepository(s)
        from amo_bot.auth.roles import Role

        assert repo.get_group_role(chat_id=-100211, telegram_user_id=7002) == Role.ADMIN
        assert repo.get_group_role(chat_id=-100212, telegram_user_id=7002) is None


def test_group_role_owner_not_allowed(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'groups_roles3.db'}"
    init_db(db_url)
    _seed_chat_topic(db_url, -100213, 94)
    _seed_user(db_url, 7003)

    sf = create_session_factory(db_url)
    with sf() as s:
        ChatSeenUserRepository(s).mark_seen(chat_id=-100213, telegram_user_id=7003)

    settings = _make_settings(db_url, owner_id=777)
    app = create_flask_app(settings=settings)

    with app.test_client() as client:
        _login(client, "test-secret")
        page = client.get("/groups")
        token = _extract_csrf_token(page.get_data(as_text=True))
        response = client.post(
            "/groups/-100213/roles",
            data={"telegram_user_id": "7003", "role": "owner", "csrf_token": token},
            follow_redirects=False,
        )

    assert response.status_code == 400


def test_group_role_set_writes_audit_event(tmp_path) -> None:
    import json

    db_url = f"sqlite:///{tmp_path / 'groups_roles_audit_set.db'}"
    init_db(db_url)
    _seed_chat_topic(db_url, -100215, 96)
    _seed_user(db_url, 7005)

    sf = create_session_factory(db_url)
    with sf() as s:
        ChatSeenUserRepository(s).mark_seen(chat_id=-100215, telegram_user_id=7005)

    settings = _make_settings(db_url, owner_id=777)
    app = create_flask_app(settings=settings)

    with app.test_client() as client:
        _login(client, "test-secret")
        page = client.get("/groups")
        token = _extract_csrf_token(page.get_data(as_text=True))
        response = client.post(
            "/groups/-100215/roles",
            data={"telegram_user_id": "7005", "role": "vip", "csrf_token": token},
            follow_redirects=False,
        )

    assert response.status_code == 302

    sf = create_session_factory(db_url)
    with sf() as s:
        from amo_bot.db.models import AuditEvent

        event = s.scalar(select(AuditEvent).where(AuditEvent.event_type == "group_role_set"))
        assert event is not None
        payload = json.loads(event.payload_json)
        assert event.actor_telegram_user_id == 777
        assert payload["chat_id"] == -100215
        assert payload["target_telegram_user_id"] == 7005
        assert payload["new_role"] == "vip"
        assert payload["source"] == "webui"


def test_group_role_normal_clears_entry(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'groups_roles4.db'}"
    init_db(db_url)
    _seed_chat_topic(db_url, -100214, 95)
    _seed_user(db_url, 7004)

    sf = create_session_factory(db_url)
    with sf() as s:
        from amo_bot.auth.roles import Role

        ChatScopedRoleRepository(s).set_group_role(chat_id=-100214, telegram_user_id=7004, role=Role.VIP)

    settings = _make_settings(db_url, owner_id=777)
    app = create_flask_app(settings=settings)

    with app.test_client() as client:
        _login(client, "test-secret")
        page = client.get("/groups")
        token = _extract_csrf_token(page.get_data(as_text=True))
        response = client.post(
            "/groups/-100214/roles",
            data={"telegram_user_id": "7004", "role": "normal", "csrf_token": token},
            follow_redirects=False,
        )

    assert response.status_code == 302

    with sf() as s:
        repo = ChatScopedRoleRepository(s)
        assert repo.get_group_role(chat_id=-100214, telegram_user_id=7004) is None


def test_group_role_normal_writes_clear_audit_event(tmp_path) -> None:
    import json

    db_url = f"sqlite:///{tmp_path / 'groups_roles_audit_clear.db'}"
    init_db(db_url)
    _seed_chat_topic(db_url, -100216, 97)
    _seed_user(db_url, 7006)

    sf = create_session_factory(db_url)
    with sf() as s:
        from amo_bot.auth.roles import Role

        ChatScopedRoleRepository(s).set_group_role(chat_id=-100216, telegram_user_id=7006, role=Role.VIP)

    settings = _make_settings(db_url, owner_id=777)
    app = create_flask_app(settings=settings)

    with app.test_client() as client:
        _login(client, "test-secret")
        page = client.get("/groups")
        token = _extract_csrf_token(page.get_data(as_text=True))
        response = client.post(
            "/groups/-100216/roles",
            data={"telegram_user_id": "7006", "role": "normal", "csrf_token": token},
            follow_redirects=False,
        )

    assert response.status_code == 302

    with sf() as s:
        from amo_bot.db.models import AuditEvent

        event = s.scalar(select(AuditEvent).where(AuditEvent.event_type == "group_role_clear"))
        assert event is not None
        payload = json.loads(event.payload_json)
        assert event.actor_telegram_user_id == 777
        assert payload["chat_id"] == -100216
        assert payload["target_telegram_user_id"] == 7006
        assert payload["new_role"] == "normal"
        assert payload["source"] == "webui"


def test_group_roles_only_show_seen_users_per_chat_and_keep_assigned_not_seen(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'groups_roles_seen_filter.db'}"
    init_db(db_url)

    _seed_chat_topic(db_url, -100301, 101)
    _seed_chat_topic(db_url, -100302, 102)

    _seed_user(db_url, 7101)
    _seed_user(db_url, 7102)
    _seed_user(db_url, 7103)
    _seed_user(db_url, 7104)

    sf = create_session_factory(db_url)
    with sf() as s:
        seen_repo = ChatSeenUserRepository(s)
        seen_repo.mark_seen(chat_id=-100301, telegram_user_id=7101)
        seen_repo.mark_seen(chat_id=-100302, telegram_user_id=7102)

        from amo_bot.auth.roles import Role

        ChatScopedRoleRepository(s).set_group_role(chat_id=-100301, telegram_user_id=7104, role=Role.VIP)

    settings = _make_settings(db_url, owner_id=777)
    app = create_flask_app(settings=settings)

    with app.test_client() as client:
        _login(client, "test-secret")
        response = client.get("/groups")
        html = response.get_data(as_text=True)

        assert response.status_code == 200

        assert "<li><strong>chat_id:</strong> -100301</li>" in html
        assert "<li><strong>chat_id:</strong> -100302</li>" in html

        sections = html.split('<section class="group-section">')
        group1_html = next((section for section in sections if "<strong>chat_id:</strong> -100301" in section), "")
        assert group1_html

        assert "7101" in group1_html
        assert "7102" not in group1_html
        assert "7103" not in group1_html
        assert "7104" in group1_html
        assert "assigned/not seen" in group1_html

        page = client.get("/groups")
        token = _extract_csrf_token(page.get_data(as_text=True))
        clear_response = client.post(
            "/groups/-100301/roles",
            data={"telegram_user_id": "7104", "role": "normal", "csrf_token": token},
            follow_redirects=False,
        )
        assert clear_response.status_code == 302

    with sf() as s:
        repo = ChatScopedRoleRepository(s)
        assert repo.get_group_role(chat_id=-100301, telegram_user_id=7104) is None


def test_topic_metadata_update_missing_topic_returns_404(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'groups7.db'}"
    init_db(db_url)

    sf = create_session_factory(db_url)
    with sf() as s:
        s.add(
            TelegramChat(
                chat_id=-100203,
                chat_type="supergroup",
                title="Only Chat",
                username="onlychat",
            )
        )
        s.commit()

    settings = _make_settings(db_url, owner_id=777)
    app = create_flask_app(settings=settings)

    with app.test_client() as client:
        _login(client, "test-secret")
        page = client.get("/groups/-100203")
        token = _extract_csrf_token(page.get_data(as_text=True))
        response = client.post(
            "/groups/-100203/topics/999",
            data={"display_name": "X", "notes": "Y", "topic_soul_text": "X", "enabled": "y", "csrf_token": token},
            follow_redirects=False,
        )

    assert response.status_code == 404


def test_groups_renders_topic_soul_escaped_and_preserves_whitespace(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'groups8.db'}"
    init_db(db_url)
    _seed_chat_topic(db_url, -100204, 91)

    soul_text = "Line1\n<script>alert(1)</script>\nLine3"
    sf = create_session_factory(db_url)
    with sf() as s:
        from amo_bot.db.repositories import TopicAgentMemoryRepository

        TopicAgentMemoryRepository(s).upsert_config(
            scope_type="topic",
            chat_id=-100204,
            topic_id=91,
            user_id=None,
            topic_soul_text=soul_text,
        )

    settings = _make_settings(db_url, owner_id=777)
    app = create_flask_app(settings=settings)

    with app.test_client() as client:
        _login(client, "test-secret")
        response = client.get("/groups")
        html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Line1" not in html
    assert "Line3" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" not in html
    assert "<script>alert(1)</script>" not in html


def test_topic_metadata_toggle_ai_enabled_to_false(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'groups_ai_toggle.db'}"
    init_db(db_url)
    _seed_chat_topic(db_url, -100206, 93)

    sf = create_session_factory(db_url)
    with sf() as s:
        from amo_bot.db.repositories import TopicAgentMemoryRepository

        TopicAgentMemoryRepository(s).upsert_config(
            scope_type="topic",
            chat_id=-100206,
            topic_id=93,
            user_id=None,
            ai_enabled=True,
            response_mode="command",
        )

    settings = _make_settings(db_url, owner_id=777)
    app = create_flask_app(settings=settings)

    with app.test_client() as client:
        _login(client, "test-secret")
        page = client.get("/groups/-100206")
        token = _extract_csrf_token(page.get_data(as_text=True))
        response = client.post(
            "/groups/-100206/topics/93",
            data={
                "display_name": "Ops",
                "notes": "Runbook",
                "topic_soul_text": "Soul",
                "enabled": "y",
                "response_mode": "mention_or_reply",
                "csrf_token": token,
            },
            follow_redirects=False,
        )

    assert response.status_code == 302

    with sf() as s:
        from amo_bot.db.models import TopicAgentConfig

        cfg = s.scalar(
            select(TopicAgentConfig).where(
                TopicAgentConfig.scope_type == "topic",
                TopicAgentConfig.chat_id == -100206,
                TopicAgentConfig.topic_id == 93,
            )
        )
        assert cfg is not None
        assert cfg.ai_enabled is False
        assert cfg.response_mode == "mention_or_reply"


def test_topic_metadata_invalid_response_mode_rejected_without_db_write(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'groups_ai_invalid_mode.db'}"
    init_db(db_url)
    _seed_chat_topic(db_url, -100207, 94)

    sf = create_session_factory(db_url)
    with sf() as s:
        from amo_bot.db.repositories import TopicAgentMemoryRepository

        TopicAgentMemoryRepository(s).upsert_config(
            scope_type="topic",
            chat_id=-100207,
            topic_id=94,
            user_id=None,
            ai_enabled=True,
            response_mode="command",
            topic_soul_text="before",
        )

    settings = _make_settings(db_url, owner_id=777)
    app = create_flask_app(settings=settings)

    with app.test_client() as client:
        _login(client, "test-secret")
        page = client.get("/groups/-100207")
        token = _extract_csrf_token(page.get_data(as_text=True))
        response = client.post(
            "/groups/-100207/topics/94",
            data={
                "display_name": "After",
                "notes": "After",
                "topic_soul_text": "after",
                "enabled": "",
                "ai_enabled": "y",
                "response_mode": "invalid_mode",
                "csrf_token": token,
            },
            follow_redirects=False,
        )

    assert response.status_code == 400

    with sf() as s:
        topic = s.scalar(
            select(TelegramTopic).where(TelegramTopic.chat_id == -100207, TelegramTopic.message_thread_id == 94)
        )
        assert topic is not None
        assert topic.display_name is None
        assert topic.notes is None
        assert topic.enabled is True

        from amo_bot.db.models import TopicAgentConfig

        cfg = s.scalar(
            select(TopicAgentConfig).where(
                TopicAgentConfig.scope_type == "topic",
                TopicAgentConfig.chat_id == -100207,
                TopicAgentConfig.topic_id == 94,
            )
        )
        assert cfg is not None
        assert cfg.ai_enabled is True
        assert cfg.response_mode == "command"
        assert cfg.topic_soul_text == "before"


def test_groups_overview_hides_ai_controls_and_keeps_detail_link(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'groups_ai_render.db'}"
    init_db(db_url)
    _seed_chat_topic(db_url, -100208, 95)

    settings = _make_settings(db_url, owner_id=777)
    app = create_flask_app(settings=settings)

    with app.test_client() as client:
        _login(client, "test-secret")
        response = client.get("/groups")
        html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Topic AI enabled" not in html
    assert "Topic AI response mode" not in html
    assert 'name="ai_enabled"' not in html
    assert 'name="response_mode"' not in html
    assert 'name="topic_soul_text"' not in html
    assert 'href="/groups/-100208#topic-95-heading"' in html


def test_topic_metadata_topic_soul_text_max_length_validation(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'groups9.db'}"
    init_db(db_url)
    _seed_chat_topic(db_url, -100205, 92)

    settings = _make_settings(db_url, owner_id=777)
    app = create_flask_app(settings=settings)

    too_long = "a" * 4001
    with app.test_client() as client:
        _login(client, "test-secret")
        page = client.get("/groups/-100205")
        token = _extract_csrf_token(page.get_data(as_text=True))
        response = client.post(
            "/groups/-100205/topics/92",
            data={
                "display_name": "Ops",
                "notes": "Runbook",
                "topic_soul_text": too_long,
                "enabled": "y",
                "csrf_token": token,
            },
            follow_redirects=False,
        )

    assert response.status_code == 400


def test_groups_language_switch_en(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'groups_lang.db'}"
    app = create_flask_app(settings=_make_settings(db_url))

    with app.test_client() as client:
        with client.session_transaction() as flask_session:
            flask_session["authenticated"] = True
        response = client.get("/groups?lang=en")
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert "Language:" in html
        assert "Topic management disabled: WEBUI_OWNER_TELEGRAM_ID is not set." in html
