from __future__ import annotations

from flask import Flask

from .auth import auth_bp
from .health import health_bp
from .plugins import plugins_bp
from .ui import ui_bp


def register_blueprints(app: Flask) -> None:
    """Register Flask blueprints for WebUI routes."""
    app.register_blueprint(health_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(ui_bp)
    app.register_blueprint(plugins_bp)
