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
from amo_bot.plugins.worker_runtime import WorkerPluginManager
from amo_bot.telegram.client import TelegramClient
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
    worker_manager: object | None = None,
) -> Flask:
    app_settings = settings or get_settings()

    effective_secure_cookie = (
        app_settings.webui_session_cookie_secure
        if app_settings.webui_session_cookie_secure is not None
        else (app_settings.webui_public_mode or app_settings.webui_require_https)
    )
    if app_settings.webui_public_mode and not (app_settings.webui_require_https and effective_secure_cookie):
        raise ValueError(
            "WEBUI_PUBLIC_MODE=true requires WEBUI_REQUIRE_HTTPS=true and secure session cookies. "
            "Set WEBUI_SESSION_COOKIE_SECURE=true (or leave unset with WEBUI_REQUIRE_HTTPS=true)."
        )

    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=(app_settings.webui_password or "").strip() or _derive_secret_key(app_settings.webui_password),
        PERMANENT_SESSION_LIFETIME=timedelta(seconds=max(1, app_settings.webui_session_ttl_seconds)),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=effective_secure_cookie,
    )

    csrf.init_app(app)

    # Ensure latest DB schema exists for WebUI usage (idempotent, non-destructive).
    init_db(app_settings.database_url)

    session_factory = create_session_factory(app_settings.database_url)
    plugins = plugin_service or PluginService(
        loader=PluginLoader(app_settings.amo_plugin_dir),
        session_factory=session_factory,
    )

    if worker_manager is None:
        tg = TelegramClient(token=app_settings.bot_token, base_url=app_settings.telegram_api_base)

        async def send_text(chat_id: int, text: str) -> object:
            return await tg.send_message(chat_id=chat_id, text=text)

        async def reply_text(chat_id: int, message_id: int, text: str) -> object:
            return await tg.send_message(chat_id=chat_id, text=text, reply_to_message_id=message_id)

        worker_manager = WorkerPluginManager(
            loader=PluginLoader(app_settings.amo_plugin_dir),
            session_factory=session_factory,
            send_message=send_text,
            reply=reply_text,
        )

    app.extensions["amo.settings"] = app_settings
    app.extensions["amo.plugin_service"] = plugins
    app.extensions["amo.worker_manager"] = worker_manager

    @app.before_request
    def _sliding_session_ttl() -> None:
        # Sliding TTL is only applied once a real authenticated session exists.
        if session.get("authenticated") is True:
            session.permanent = True
            session.modified = True

    @app.after_request
    def _security_headers(response):  # type: ignore[no-untyped-def]
        # style-src allows inline styles for existing server-rendered templates.
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "object-src 'none'; "
            "base-uri 'self'; "
            "form-action 'self'; "
            "frame-ancestors 'none'; "
            "style-src 'self' 'unsafe-inline'"
        )
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        if app_settings.webui_require_https or effective_secure_cookie:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

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
