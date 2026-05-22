from __future__ import annotations

import json

from sqlalchemy import select

from amo_bot.auth.roles import ROLE_ACCESS_RANK, Role
from amo_bot.db.base import create_session_factory
from amo_bot.db.init_db import init_db
from amo_bot.db.models import AuditEvent, Plugin, PluginActivationRequest
from amo_bot.db.repositories import PluginRepository
from amo_bot.plugins.manifest import PluginManifest
from amo_bot.plugins.loader import PluginLoader
from amo_bot.plugins.service import ActionContext, PluginPolicy, PluginPolicyError, PluginService
from amo_bot.plugins.settings_validation import PluginSettingsValidator, redact_plugin_settings


def _manifest(name: str = "demo") -> dict[str, object]:
    return {
        "name": name,
        "version": "1.0.0",
        "commands": ["/demo"],
        "required_roles": ["admin"],
        "required_permissions": ["send_message"],
    }


def test_discovery_supports_plugin_json_and_manifest_fallback(tmp_path) -> None:
    plugins_dir = tmp_path / "plugins"
    plugin1 = plugins_dir / "a_plugin"
    plugin1.mkdir(parents=True)
    (plugin1 / "plugin.json").write_text(json.dumps(_manifest("plugin-a")), encoding="utf-8")

    plugin2 = plugins_dir / "b_plugin"
    plugin2.mkdir(parents=True)
    (plugin2 / "manifest.json").write_text(json.dumps(_manifest("plugin-b")), encoding="utf-8")

    loader = PluginLoader(str(plugins_dir))
    result = loader.discover()

    assert sorted(m.name for m in result.valid) == ["plugin-a", "plugin-b"]
    assert result.invalid == []


def test_invalid_manifest_is_reported_without_crash(tmp_path) -> None:
    plugins_dir = tmp_path / "plugins"
    plugin_bad = plugins_dir / "broken"
    plugin_bad.mkdir(parents=True)
    (plugin_bad / "plugin.json").write_text('{"name": "broken"}', encoding="utf-8")

    loader = PluginLoader(str(plugins_dir))
    result = loader.discover()

    assert result.valid == []
    assert len(result.invalid) == 1
    assert result.invalid[0].plugin_dir == "broken"
    assert "invalid manifest" in result.invalid[0].error


def test_manifest_with_entrypoint_is_rejected_for_mvp(tmp_path) -> None:
    plugins_dir = tmp_path / "plugins"
    plugin_bad = plugins_dir / "badentry"
    plugin_bad.mkdir(parents=True)
    (plugin_bad / "plugin.json").write_text(
        json.dumps(
            {
                "name": "badentry",
                "version": "1.0",
                "entrypoint": "plugin.main:run",
                "commands": ["/demo"],
                "required_roles": ["owner"],
            }
        ),
        encoding="utf-8",
    )

    loader = PluginLoader(str(plugins_dir))
    result = loader.discover()

    assert result.valid == []
    assert len(result.invalid) == 1


def test_manifest_commands_validation_blocks_empty_values(tmp_path) -> None:
    plugins_dir = tmp_path / "plugins"
    plugin_bad = plugins_dir / "badcmd"
    plugin_bad.mkdir(parents=True)
    (plugin_bad / "plugin.json").write_text(
        json.dumps(
            {
                "name": "badcmd",
                "version": "1.0",
                "commands": ["", "   "],
                "required_roles": ["owner"],
            }
        ),
        encoding="utf-8",
    )

    loader = PluginLoader(str(plugins_dir))
    result = loader.discover()

    assert result.valid == []
    assert len(result.invalid) == 1


def test_activate_deactivate_persist_and_write_audit(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'plugins.db'}"
    init_db(db_url)

    plugins_dir = tmp_path / "plugins"
    pdir = plugins_dir / "demo"
    pdir.mkdir(parents=True)
    (pdir / "plugin.json").write_text(json.dumps(_manifest("demo")), encoding="utf-8")

    sf = create_session_factory(db_url)
    service = PluginService(loader=PluginLoader(str(plugins_dir)), session_factory=sf)

    initial = service.list_plugins()
    assert initial["plugins"][0]["activation_status"] == "activation_pending"

    changed_on = service.activate("demo", context=ActionContext.WEBUI, actor_telegram_user_id=42)
    changed_off = service.deactivate("demo", context=ActionContext.WEBUI, actor_telegram_user_id=42)

    assert changed_on is True
    assert changed_off is True

    with sf() as session:
        plugin = session.scalar(select(Plugin).where(Plugin.name == "demo"))
        assert plugin is not None
        assert bool(plugin.enabled) is False
        assert plugin.activation_status == "activation_pending"
        events = session.scalars(
            select(AuditEvent).where(AuditEvent.event_type.in_(["plugin_activate", "plugin_deactivate"]))
        ).all()

    assert len(events) == 2


def test_sync_discovered_preserves_existing_active_status(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'plugins.db'}"
    init_db(db_url)

    plugins_dir = tmp_path / "plugins"
    pdir = plugins_dir / "demo"
    pdir.mkdir(parents=True)
    (pdir / "plugin.json").write_text(json.dumps(_manifest("demo")), encoding="utf-8")

    sf = create_session_factory(db_url)
    manifest = PluginManifest.model_validate_json((pdir / "plugin.json").read_text(encoding="utf-8"))

    with sf() as session:
        repo = PluginRepository(session)
        repo.sync_discovered([manifest])
        changed_on = repo.activate("demo", actor_telegram_user_id=1)
        assert changed_on is True

    with sf() as session:
        repo = PluginRepository(session)
        repo.sync_discovered([manifest])

    with sf() as session:
        plugin = session.scalar(select(Plugin).where(Plugin.name == "demo"))
        assert plugin is not None
        assert bool(plugin.enabled) is True
        assert plugin.activation_status == "active"


def test_sync_discovered_backfills_empty_activation_status_to_pending(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'plugins.db'}"
    init_db(db_url)

    plugins_dir = tmp_path / "plugins"
    pdir = plugins_dir / "demo"
    pdir.mkdir(parents=True)
    (pdir / "plugin.json").write_text(json.dumps(_manifest("demo")), encoding="utf-8")

    sf = create_session_factory(db_url)
    manifest = PluginManifest.model_validate_json((pdir / "plugin.json").read_text(encoding="utf-8"))

    with sf() as session:
        repo = PluginRepository(session)
        repo.sync_discovered([manifest])
        plugin = session.scalar(select(Plugin).where(Plugin.name == "demo"))
        assert plugin is not None
        plugin.activation_status = ""
        session.commit()

    with sf() as session:
        repo = PluginRepository(session)
        repo.sync_discovered([manifest])

    with sf() as session:
        plugin = session.scalar(select(Plugin).where(Plugin.name == "demo"))
        assert plugin is not None
        assert plugin.activation_status == "activation_pending"


def test_activate_allows_non_rss_plugin_without_rss_fetch_permission(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'plugins_non_rss_perm.db'}"
    init_db(db_url)

    plugins_dir = tmp_path / "plugins"
    pdir = plugins_dir / "demo"
    pdir.mkdir(parents=True)
    manifest = _manifest("demo")
    manifest["required_permissions"] = ["send_message"]
    (pdir / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")

    sf = create_session_factory(db_url)
    service = PluginService(loader=PluginLoader(str(plugins_dir)), session_factory=sf)

    changed = service.activate("demo", context=ActionContext.WEBUI, actor_telegram_user_id=1)
    assert changed is True


def test_activate_requires_rss_fetch_permission_for_rss_behavior(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'plugins_rss_perm.db'}"
    init_db(db_url)

    plugins_dir = tmp_path / "plugins"
    pdir = plugins_dir / "rss_demo"
    pdir.mkdir(parents=True)
    manifest = _manifest("rss_demo")
    manifest["required_permissions"] = ["send_message"]
    manifest["settings_schema"] = {
        "feed_sources": {"type": "text", "default": "https://example.com/rss.xml"},
        "poll_interval_seconds": {"type": "number", "default": 300},
    }
    (pdir / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")

    sf = create_session_factory(db_url)
    service = PluginService(loader=PluginLoader(str(plugins_dir)), session_factory=sf)

    try:
        service.activate("rss_demo", context=ActionContext.WEBUI, actor_telegram_user_id=1)
    except PluginPolicyError as exc:
        assert "rss.fetch permission" in str(exc)
    else:
        raise AssertionError("activation without rss.fetch must be blocked for rss behavior")


def test_activate_and_deactivate_blocked_for_telegram_context(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'plugins.db'}"
    init_db(db_url)

    plugins_dir = tmp_path / "plugins"
    pdir = plugins_dir / "demo"
    pdir.mkdir(parents=True)
    (pdir / "plugin.json").write_text(json.dumps(_manifest("demo")), encoding="utf-8")

    sf = create_session_factory(db_url)
    service = PluginService(loader=PluginLoader(str(plugins_dir)), session_factory=sf)

    try:
        service.activate("demo", context=ActionContext.TELEGRAM, actor_telegram_user_id=1)
    except PluginPolicyError:
        pass
    else:
        raise AssertionError("telegram activate must be blocked")

    try:
        service.deactivate("demo", context=ActionContext.TELEGRAM, actor_telegram_user_id=1)
    except PluginPolicyError:
        pass
    else:
        raise AssertionError("telegram deactivate must be blocked")


def test_activation_request_created_pending(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'plugins.db'}"
    init_db(db_url)

    plugins_dir = tmp_path / "plugins"
    pdir = plugins_dir / "demo"
    pdir.mkdir(parents=True)
    (pdir / "plugin.json").write_text(json.dumps(_manifest("demo")), encoding="utf-8")

    sf = create_session_factory(db_url)
    service = PluginService(loader=PluginLoader(str(plugins_dir)), session_factory=sf)

    request = service.request_activation(
        "demo",
        context=ActionContext.WEBUI,
        actor_telegram_user_id=42,
        reason="operator requested enable",
    )

    assert request.status == "pending"
    assert request.plugin_name == "demo"

    with sf() as session:
        db_request = session.scalar(select(PluginActivationRequest).where(PluginActivationRequest.id == request.id))
        plugin = session.scalar(select(Plugin).where(Plugin.name == "demo"))
        assert db_request is not None
        assert db_request.status == "pending"
        assert db_request.reason == "operator requested enable"
        assert plugin is not None
        assert bool(plugin.enabled) is False
        assert plugin.activation_status == "activation_pending"


def test_activation_request_approve_explicit_path_activates_via_controlled_method(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'plugins.db'}"
    init_db(db_url)

    plugins_dir = tmp_path / "plugins"
    pdir = plugins_dir / "demo"
    pdir.mkdir(parents=True)
    (pdir / "plugin.json").write_text(json.dumps(_manifest("demo")), encoding="utf-8")

    sf = create_session_factory(db_url)
    service = PluginService(loader=PluginLoader(str(plugins_dir)), session_factory=sf)
    request = service.request_activation("demo", context=ActionContext.WEBUI, actor_telegram_user_id=42)

    changed = service.approve_activation_request(
        request.id,
        context=ActionContext.WEBUI,
        actor_telegram_user_id=777,
    )

    assert changed is True
    with sf() as session:
        plugin = session.scalar(select(Plugin).where(Plugin.name == "demo"))
        db_request = session.scalar(select(PluginActivationRequest).where(PluginActivationRequest.id == request.id))
        events = [row.event_type for row in session.scalars(select(AuditEvent)).all()]

    assert plugin is not None
    assert bool(plugin.enabled) is True
    assert plugin.activation_status == "active"
    assert db_request is not None
    assert db_request.status == "approved"
    assert db_request.resolved_by_telegram_user_id == 777
    assert "plugin_activation_request_approved" in events
    assert "plugin_activate" in events


def test_activation_request_reject_does_not_activate(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'plugins.db'}"
    init_db(db_url)

    plugins_dir = tmp_path / "plugins"
    pdir = plugins_dir / "demo"
    pdir.mkdir(parents=True)
    (pdir / "plugin.json").write_text(json.dumps(_manifest("demo")), encoding="utf-8")

    sf = create_session_factory(db_url)
    service = PluginService(loader=PluginLoader(str(plugins_dir)), session_factory=sf)
    request = service.request_activation("demo", context=ActionContext.WEBUI, actor_telegram_user_id=42)

    changed = service.reject_activation_request(request.id, context=ActionContext.WEBUI, actor_telegram_user_id=777)

    assert changed is True
    with sf() as session:
        plugin = session.scalar(select(Plugin).where(Plugin.name == "demo"))
        db_request = session.scalar(select(PluginActivationRequest).where(PluginActivationRequest.id == request.id))

    assert plugin is not None
    assert bool(plugin.enabled) is False
    assert plugin.activation_status == "activation_pending"
    assert db_request is not None
    assert db_request.status == "rejected"


def test_activation_request_blocked_does_not_activate(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'plugins.db'}"
    init_db(db_url)

    plugins_dir = tmp_path / "plugins"
    pdir = plugins_dir / "demo"
    pdir.mkdir(parents=True)
    (pdir / "plugin.json").write_text(json.dumps(_manifest("demo")), encoding="utf-8")

    sf = create_session_factory(db_url)
    service = PluginService(loader=PluginLoader(str(plugins_dir)), session_factory=sf)
    request = service.request_activation("demo", context=ActionContext.WEBUI, actor_telegram_user_id=42)

    changed = service.block_activation_request(request.id, context=ActionContext.WEBUI, actor_telegram_user_id=777)

    assert changed is True
    with sf() as session:
        plugin = session.scalar(select(Plugin).where(Plugin.name == "demo"))
        db_request = session.scalar(select(PluginActivationRequest).where(PluginActivationRequest.id == request.id))

    assert plugin is not None
    assert bool(plugin.enabled) is False
    assert plugin.activation_status == "activation_pending"
    assert db_request is not None
    assert db_request.status == "blocked"


def test_activation_request_pending_not_auto_activated_by_discovery_sync(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'plugins.db'}"
    init_db(db_url)

    plugins_dir = tmp_path / "plugins"
    pdir = plugins_dir / "demo"
    pdir.mkdir(parents=True)
    (pdir / "plugin.json").write_text(json.dumps(_manifest("demo")), encoding="utf-8")

    sf = create_session_factory(db_url)
    service = PluginService(loader=PluginLoader(str(plugins_dir)), session_factory=sf)
    request = service.request_activation("demo", context=ActionContext.WEBUI, actor_telegram_user_id=42)

    payload = service.list_plugins()
    assert payload["plugins"][0]["enabled"] is False
    assert payload["plugins"][0]["activation_status"] == "activation_pending"

    with sf() as session:
        manifest = PluginManifest.model_validate_json((pdir / "plugin.json").read_text(encoding="utf-8"))
        PluginRepository(session).sync_discovered([manifest])

    with sf() as session:
        plugin = session.scalar(select(Plugin).where(Plugin.name == "demo"))
        db_request = session.scalar(select(PluginActivationRequest).where(PluginActivationRequest.id == request.id))

    assert plugin is not None
    assert bool(plugin.enabled) is False
    assert plugin.activation_status == "activation_pending"
    assert db_request is not None
    assert db_request.status == "pending"


def test_policy_role_rank_order_is_central_and_checkable() -> None:
    assert ROLE_ACCESS_RANK[Role.OWNER] > ROLE_ACCESS_RANK[Role.ADMIN]
    assert ROLE_ACCESS_RANK[Role.ADMIN] > ROLE_ACCESS_RANK[Role.VIP]
    assert ROLE_ACCESS_RANK[Role.VIP] > ROLE_ACCESS_RANK[Role.NORMAL]
    assert Role.IGNORE not in ROLE_ACCESS_RANK


def test_effective_min_role_plugin_min_vip_denies_normal() -> None:
    assert PluginPolicy.effective_min_role(["vip"]) is Role.VIP
    assert PluginPolicy.is_role_allowed(actor_role=Role.NORMAL, plugin_required_roles=["vip"]) is False
    assert PluginPolicy.is_role_allowed(actor_role=Role.VIP, plugin_required_roles=["vip"]) is True


def test_effective_min_role_admin_restriction_vip_denies_normal() -> None:
    assert PluginPolicy.effective_min_role([], admin_restriction=Role.VIP) is Role.VIP
    assert PluginPolicy.is_role_allowed(
        actor_role=Role.NORMAL,
        plugin_required_roles=[],
        admin_restriction=Role.VIP,
    ) is False
    assert PluginPolicy.is_role_allowed(
        actor_role=Role.VIP,
        plugin_required_roles=[],
        admin_restriction=Role.VIP,
    ) is True


def test_effective_min_role_owner_plugin_only_owner_allowed() -> None:
    assert PluginPolicy.effective_min_role(["owner"]) is Role.OWNER
    assert PluginPolicy.is_role_allowed(actor_role=Role.ADMIN, plugin_required_roles=["owner"]) is False
    assert PluginPolicy.is_role_allowed(actor_role=Role.OWNER, plugin_required_roles=["owner"]) is True


def test_effective_min_role_cannot_loosen_below_plugin_minimum() -> None:
    assert PluginPolicy.effective_min_role(["vip"], admin_restriction=Role.NORMAL) is Role.VIP
    assert PluginPolicy.is_role_allowed(
        actor_role=Role.NORMAL,
        plugin_required_roles=["vip"],
        admin_restriction=Role.NORMAL,
    ) is False


def test_effective_min_role_owner_global_allowed() -> None:
    assert PluginPolicy.is_role_allowed(
        actor_role=Role.OWNER,
        plugin_required_roles=["admin"],
        admin_restriction=Role.VIP,
    ) is True
    assert PluginPolicy.is_role_allowed(actor_role=Role.IGNORE, plugin_required_roles=[]) is False


def test_effective_min_role_group_admin_scope_uses_resolved_role() -> None:
    def resolve_role_for_group(*, actor_global_role: Role, admin_group_id: int | None, target_group_id: int) -> Role:
        if actor_global_role is Role.OWNER:
            return Role.OWNER
        if actor_global_role is Role.ADMIN and admin_group_id == target_group_id:
            return Role.ADMIN
        if actor_global_role is Role.VIP:
            return Role.VIP
        return Role.NORMAL

    plugin_required_roles = ["admin"]

    owner_in_any_group = resolve_role_for_group(actor_global_role=Role.OWNER, admin_group_id=None, target_group_id=10)
    assert PluginPolicy.is_role_allowed(actor_role=owner_in_any_group, plugin_required_roles=plugin_required_roles) is True

    admin_in_own_group = resolve_role_for_group(actor_global_role=Role.ADMIN, admin_group_id=10, target_group_id=10)
    assert PluginPolicy.is_role_allowed(actor_role=admin_in_own_group, plugin_required_roles=plugin_required_roles) is True

    admin_in_other_group = resolve_role_for_group(actor_global_role=Role.ADMIN, admin_group_id=10, target_group_id=20)
    assert PluginPolicy.is_role_allowed(actor_role=admin_in_other_group, plugin_required_roles=plugin_required_roles) is False

    vip_in_group = resolve_role_for_group(actor_global_role=Role.VIP, admin_group_id=None, target_group_id=10)
    assert PluginPolicy.is_role_allowed(actor_role=vip_in_group, plugin_required_roles=plugin_required_roles) is False

    normal_in_group = resolve_role_for_group(actor_global_role=Role.NORMAL, admin_group_id=None, target_group_id=10)
    assert PluginPolicy.is_role_allowed(actor_role=normal_in_group, plugin_required_roles=plugin_required_roles) is False


def test_settings_schema_validation_types_required_ranges_and_select_options() -> None:
    manifest = PluginManifest.model_validate(
        {
            "name": "settings-demo",
            "version": "1.0.0",
            "commands": ["/demo"],
            "required_roles": ["admin"],
            "settings_schema": {
                "label": {"type": "text", "required": True, "pattern": "^[a-z]+$"},
                "retries": {"type": "number", "min": 1, "max": 3, "required": True},
                "enabled": {"type": "bool", "default": True},
                "mode": {"type": "select", "options": ["safe", "fast"], "required": True},
                "api_key": {"type": "secret", "required": True},
            },
        }
    )

    valid = PluginSettingsValidator.validate(
        settings_schema=manifest.settings_schema,
        values={"label": "alpha", "retries": 2, "mode": "safe", "api_key": "topsecret"},
    )
    assert valid.is_valid is True

    missing_required = PluginSettingsValidator.validate(
        settings_schema=manifest.settings_schema,
        values={"label": "alpha", "retries": 2, "mode": "safe"},
    )
    assert missing_required.is_valid is False
    assert any(err.setting == "api_key" and "required" in err.message for err in missing_required.errors)

    number_min = PluginSettingsValidator.validate(
        settings_schema=manifest.settings_schema,
        values={"label": "alpha", "retries": 0, "mode": "safe", "api_key": "topsecret"},
    )
    assert number_min.is_valid is False
    assert any(err.setting == "retries" and ">=" in err.message for err in number_min.errors)

    number_max = PluginSettingsValidator.validate(
        settings_schema=manifest.settings_schema,
        values={"label": "alpha", "retries": 9, "mode": "safe", "api_key": "topsecret"},
    )
    assert number_max.is_valid is False
    assert any(err.setting == "retries" and "<=" in err.message for err in number_max.errors)

    select_invalid = PluginSettingsValidator.validate(
        settings_schema=manifest.settings_schema,
        values={"label": "alpha", "retries": 2, "mode": "invalid", "api_key": "topsecret"},
    )
    assert select_invalid.is_valid is False
    assert any(err.setting == "mode" and "configured options" in err.message for err in select_invalid.errors)


def test_secret_redaction_masks_secret_values() -> None:
    manifest = PluginManifest.model_validate(
        {
            "name": "secret-demo",
            "version": "1.0.0",
            "commands": ["/demo"],
            "required_roles": ["admin"],
            "settings_schema": {
                "token": {"type": "secret", "required": True},
                "mode": {"type": "select", "options": ["safe", "fast"], "default": "safe"},
            },
        }
    )

    redacted = redact_plugin_settings(
        settings_schema=manifest.settings_schema,
        values={"token": "very-secret-token", "mode": "fast"},
    )
    assert redacted["token"] == "***"
    assert redacted["mode"] == "fast"
    assert "very-secret-token" not in str(redacted)


def test_plugin_min_role_invalid_required_role_raises_policy_error() -> None:
    try:
        PluginPolicy.plugin_min_role(["admin", "not-a-role"])
    except PluginPolicyError as exc:
        assert "invalid required role in plugin policy" in str(exc)
        assert "not-a-role" in str(exc)
    else:
        raise AssertionError("invalid required role must raise PluginPolicyError")


def test_activate_blocks_when_required_settings_missing(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'plugins.db'}"
    init_db(db_url)

    plugins_dir = tmp_path / "plugins"
    pdir = plugins_dir / "demo"
    pdir.mkdir(parents=True)
    manifest_payload = _manifest("demo")
    manifest_payload["settings_schema"] = {"api_key": {"type": "secret", "required": True}}
    (pdir / "plugin.json").write_text(json.dumps(manifest_payload), encoding="utf-8")

    sf = create_session_factory(db_url)
    service = PluginService(loader=PluginLoader(str(plugins_dir)), session_factory=sf)

    try:
        service.activate("demo", context=ActionContext.WEBUI, actor_telegram_user_id=42)
    except PluginPolicyError as exc:
        assert "settings validation failed" in str(exc)
        assert "api_key" in str(exc)
    else:
        raise AssertionError("activation must be blocked when required plugin settings are missing")


def test_plugin_policy_override_repository_upsert_allowed_group_ids_dedup_replace_and_clear(tmp_path) -> None:
    from amo_bot.auth.roles import Role
    from amo_bot.db.base import create_session_factory
    from amo_bot.db.repositories import PluginPolicyOverrideRepository
    from amo_bot.db.init_db import init_db

    db_url = f"sqlite:///{tmp_path / 'plugins_allowed_groups.db'}"
    init_db(db_url)
    sf = create_session_factory(db_url)

    with sf() as session:
        repo = PluginPolicyOverrideRepository(session)
        repo.upsert_override(
            plugin_name="scope",
            roles_mode="inherit",
            required_roles=[],
            private_mode="inherit",
            groups_mode="allow",
            topics_mode="inherit",
            allowed_group_ids=[-2002, -1001, -2002],
        )
        snap1 = repo.get_snapshot(plugin_name="scope")
        assert snap1 is not None
        assert snap1.allowed_group_ids == [-2002, -1001]

        repo.upsert_override(
            plugin_name="scope",
            roles_mode="override",
            required_roles=[Role.ADMIN],
            private_mode="deny",
            groups_mode="allow",
            topics_mode="inherit",
            allowed_group_ids=[-3003],
        )
        snap2 = repo.get_snapshot(plugin_name="scope")
        assert snap2 is not None
        assert snap2.allowed_group_ids == [-3003]

        repo.upsert_override(
            plugin_name="scope",
            roles_mode="override",
            required_roles=[Role.ADMIN],
            private_mode="deny",
            groups_mode="allow",
            topics_mode="inherit",
            allowed_group_ids=[],
        )
        snap3 = repo.get_snapshot(plugin_name="scope")
        assert snap3 is not None
        assert snap3.allowed_group_ids == []


def test_plugin_policy_override_repository_upsert_allowed_topics_dedup_replace_and_clear(tmp_path) -> None:
    from amo_bot.auth.roles import Role
    from amo_bot.db.base import create_session_factory
    from amo_bot.db.repositories import PluginPolicyOverrideRepository
    from amo_bot.db.init_db import init_db

    db_url = f"sqlite:///{tmp_path / 'plugins_allowed_topics.db'}"
    init_db(db_url)
    sf = create_session_factory(db_url)

    with sf() as session:
        repo = PluginPolicyOverrideRepository(session)
        repo.upsert_override(
            plugin_name="scope",
            roles_mode="inherit",
            required_roles=[Role.ADMIN],
            private_mode="inherit",
            groups_mode="inherit",
            topics_mode="allow",
            allowed_topics=[(-1001, 20), (-1001, 10), (-1001, 20), (-2002, 5)],
        )
        snap1 = repo.get_snapshot(plugin_name="scope")
        assert snap1 is not None
        assert snap1.allowed_topics == [(-2002, 5), (-1001, 10), (-1001, 20)]

        repo.upsert_override(
            plugin_name="scope",
            roles_mode="inherit",
            required_roles=[Role.ADMIN],
            private_mode="inherit",
            groups_mode="inherit",
            topics_mode="allow",
            allowed_topics=[(-3003, 7)],
        )
        snap2 = repo.get_snapshot(plugin_name="scope")
        assert snap2 is not None
        assert snap2.allowed_topics == [(-3003, 7)]

        repo.upsert_override(
            plugin_name="scope",
            roles_mode="inherit",
            required_roles=[Role.ADMIN],
            private_mode="inherit",
            groups_mode="inherit",
            topics_mode="allow",
            allowed_topics=[],
        )
        snap3 = repo.get_snapshot(plugin_name="scope")
        assert snap3 is not None
        assert snap3.allowed_topics == []
