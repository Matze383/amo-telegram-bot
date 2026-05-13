from __future__ import annotations

from flask import Blueprint, abort, current_app, redirect, render_template, request, url_for
from flask_wtf import FlaskForm

from amo_bot.auth.roles import Role
from amo_bot.db.base import create_session_factory
from amo_bot.db.repositories import PluginPolicyOverrideRepository
from amo_bot.plugins.service import ActionContext, PluginPolicyError
from amo_bot.webui.flask_blueprints.ui import login_required

plugins_bp = Blueprint("plugins", __name__)


class PluginMutationForm(FlaskForm):
    pass


_ALLOWED_ROLE_VALUES = {Role.OWNER.value, Role.ADMIN.value, Role.VIP.value, Role.NORMAL.value}
_ALLOWED_ROLES_MODES = {"inherit", "override"}
_ALLOWED_SCOPE_MODES = {"inherit", "allow", "deny"}


def _validate_and_collect_policy_override_payload() -> tuple[str, list[Role], str, str]:
    roles_mode = (request.form.get("roles_mode") or "inherit").strip().lower()
    private_mode = (request.form.get("private_mode") or "inherit").strip().lower()
    groups_mode = (request.form.get("groups_mode") or "inherit").strip().lower()

    if roles_mode not in _ALLOWED_ROLES_MODES:
        abort(400, description="invalid roles mode")
    if private_mode not in _ALLOWED_SCOPE_MODES:
        abort(400, description="invalid private mode")
    if groups_mode not in _ALLOWED_SCOPE_MODES:
        abort(400, description="invalid group mode")

    selected_roles = [item.strip().lower() for item in request.form.getlist("required_roles") if item.strip()]
    if any(item not in _ALLOWED_ROLE_VALUES for item in selected_roles):
        abort(400, description="invalid required role")

    deduped_roles = [Role.OWNER, Role.ADMIN, Role.VIP, Role.NORMAL]
    normalized_roles = [role for role in deduped_roles if role.value in set(selected_roles)]
    return roles_mode, normalized_roles, private_mode, groups_mode


def _save_policy_override(plugin_name: str, *, roles_mode: str, required_roles: list[Role], private_mode: str, groups_mode: str) -> None:
    session_factory = create_session_factory(current_app.extensions["amo.settings"].database_url)
    with session_factory() as session:
        PluginPolicyOverrideRepository(session).upsert_override(
            plugin_name=plugin_name,
            roles_mode=roles_mode,
            required_roles=required_roles,
            private_mode=private_mode,
            groups_mode=groups_mode,
            topics_mode="inherit",
        )


@plugins_bp.get("/plugins")
@login_required
def plugins_page():
    plugin_service = current_app.extensions["amo.plugin_service"]
    payload = plugin_service.list_plugins()

    session_factory = create_session_factory(current_app.extensions["amo.settings"].database_url)
    policy_overrides: dict[str, dict[str, str | list[str]]] = {}
    with session_factory() as session:
        repository = PluginPolicyOverrideRepository(session)
        for plugin in payload["plugins"]:
            plugin_name = plugin["name"] if isinstance(plugin, dict) else plugin.name
            snapshot = repository.get_snapshot(plugin_name=plugin_name)
            policy_overrides[plugin_name] = {
                "roles_mode": snapshot.roles_mode if snapshot else "inherit",
                "required_roles": [role.value for role in snapshot.required_roles] if snapshot else [],
                "private_mode": snapshot.private_mode if snapshot else "inherit",
                "groups_mode": snapshot.groups_mode if snapshot else "inherit",
            }

    return render_template(
        "plugins.html",
        plugins=payload["plugins"],
        invalid_manifests=payload["invalid_manifests"],
        mutation_form=PluginMutationForm(),
        owner_mutation_enabled=current_app.extensions["amo.settings"].webui_owner_telegram_id is not None,
        policy_overrides=policy_overrides,
    ), 200


def _owner_actor_id() -> int:
    settings = current_app.extensions["amo.settings"]
    if settings.webui_owner_telegram_id is None:
        abort(403, description="WEBUI_OWNER_TELEGRAM_ID not configured; plugin mutation is disabled")
    return settings.webui_owner_telegram_id


def _validate_mutation_form() -> None:
    if not PluginMutationForm().validate_on_submit():
        abort(400, description="invalid plugin mutation payload")


@plugins_bp.post("/plugins/<plugin_name>/enable")
@login_required
def enable_plugin(plugin_name: str):
    _validate_mutation_form()
    actor_id = _owner_actor_id()
    plugin_service = current_app.extensions["amo.plugin_service"]
    try:
        plugin_service.activate(plugin_name, context=ActionContext.WEBUI, actor_telegram_user_id=actor_id)
    except PluginPolicyError:
        abort(403, description="plugin activation denied")
    except ValueError:
        abort(404, description="plugin not found")
    return redirect(url_for("plugins.plugins_page"), code=302)


@plugins_bp.post("/plugins/<plugin_name>/disable")
@login_required
def disable_plugin(plugin_name: str):
    _validate_mutation_form()
    actor_id = _owner_actor_id()
    plugin_service = current_app.extensions["amo.plugin_service"]
    try:
        manager = current_app.extensions.get("amo.worker_manager")
        if manager is not None:
            stop_worker = getattr(manager, "stop_sync", None) or getattr(manager, "stop", None)
            if stop_worker is not None:
                stop_result = stop_worker(plugin_name)
                if hasattr(stop_result, "__await__"):
                    abort(503, description="worker manager must expose sync methods for Flask routes")
        plugin_service.deactivate(plugin_name, context=ActionContext.WEBUI, actor_telegram_user_id=actor_id)
    except PluginPolicyError:
        abort(403, description="plugin deactivation denied")
    except ValueError:
        abort(404, description="plugin not found")
    return redirect(url_for("plugins.plugins_page"), code=302)


def _call_worker(plugin_name: str, action: str) -> bool:
    manager = current_app.extensions.get("amo.worker_manager")
    if manager is None:
        abort(503, description="worker manager not configured")
    method = getattr(manager, f"{action}_sync", None) or getattr(manager, action, None)
    if method is None:
        abort(503, description="worker manager action unavailable")
    result = method(plugin_name)
    if hasattr(result, "__await__"):
        abort(503, description="worker manager must expose sync methods for Flask routes")
    return bool(result)


@plugins_bp.post("/plugins/<plugin_name>/policy")
@login_required
def save_plugin_policy(plugin_name: str):
    _validate_mutation_form()
    _owner_actor_id()

    roles_mode, required_roles, private_mode, groups_mode = _validate_and_collect_policy_override_payload()
    _save_policy_override(
        plugin_name,
        roles_mode=roles_mode,
        required_roles=required_roles,
        private_mode=private_mode,
        groups_mode=groups_mode,
    )
    return redirect(url_for("plugins.plugins_page"), code=302)


@plugins_bp.post("/plugins/<plugin_name>/worker/<action>")
@login_required
def mutate_worker(plugin_name: str, action: str):
    if action not in {"start", "stop", "restart"}:
        abort(404, description="unknown worker action")
    _validate_mutation_form()
    _owner_actor_id()
    _call_worker(plugin_name, action)
    return redirect(url_for("plugins.plugins_page"), code=302)
