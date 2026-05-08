from __future__ import annotations

import hashlib
from datetime import timedelta

from flask import Flask, jsonify, session
from flask_wtf import CSRFProtect

from amo_bot.config.settings import Settings, get_settings
from amo_bot.db.base import create_session_factory
from amo_bot.db.init_db import init_db
from amo_bot.plugins.loader import PluginLoader
from amo_bot.plugins.service import PluginService
from amo_bot.webui.flask_blueprints import register_blueprints

csrf = CSRFProtect()


def _derive_secret_key(password: str | None) -> str:
    """Defensive fallback key derivation from configured webui password."""
    seed = (password or "change_me").encode("utf-8")
    return hashlib.sha256(seed).hexdigest()


def create_flask_app(
    *,
    settings: Settings | None = None,
    plugin_service: PluginService | None = None,
) -> Flask:
    app_settings = settings or get_settings()

    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=(app_settings.webui_password or "").strip() or _derive_secret_key(app_settings.webui_password),
        PERMANENT_SESSION_LIFETIME=timedelta(seconds=max(1, app_settings.webui_session_ttl_seconds)),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )

    csrf.init_app(app)

    # Ensure latest DB schema exists for WebUI usage (idempotent, non-destructive).
    init_db(app_settings.database_url)

    session_factory = create_session_factory(app_settings.database_url)
    plugins = plugin_service or PluginService(
        loader=PluginLoader(app_settings.amo_plugin_dir),
        session_factory=session_factory,
    )

    app.extensions["amo.settings"] = app_settings
    app.extensions["amo.plugin_service"] = plugins

    @app.before_request
    def _sliding_session_ttl() -> None:
        # Sliding TTL is only applied once a real authenticated session exists.
        if session.get("authenticated") is True:
            session.permanent = True
            session.modified = True

    @app.errorhandler(400)
    @app.errorhandler(401)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(500)
    def _json_error(error):  # type: ignore[no-untyped-def]
        code = getattr(error, "code", 500)
        description = getattr(error, "description", "internal server error")
        return jsonify({"error": description, "status": code}), code

    register_blueprints(app)
    return app
