from __future__ import annotations

from flask import Blueprint, abort, current_app, redirect, render_template, url_for
from flask_wtf import FlaskForm

from amo_bot.plugins.service import ActionContext, PluginPolicyError
from amo_bot.webui.flask_blueprints.ui import login_required

plugins_bp = Blueprint("plugins", __name__)


class PluginMutationForm(FlaskForm):
    pass


@plugins_bp.get("/plugins")
@login_required
def plugins_page():
    plugin_service = current_app.extensions["amo.plugin_service"]
    payload = plugin_service.list_plugins()
    return render_template(
        "plugins.html",
        plugins=payload["plugins"],
        invalid_manifests=payload["invalid_manifests"],
        mutation_form=PluginMutationForm(),
        owner_mutation_enabled=current_app.extensions["amo.settings"].webui_owner_telegram_id is not None,
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


@plugins_bp.post("/plugins/<plugin_name>/worker/<action>")
@login_required
def mutate_worker(plugin_name: str, action: str):
    if action not in {"start", "stop", "restart"}:
        abort(404, description="unknown worker action")
    _validate_mutation_form()
    _owner_actor_id()
    _call_worker(plugin_name, action)
    return redirect(url_for("plugins.plugins_page"), code=302)
