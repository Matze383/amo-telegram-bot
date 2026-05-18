from __future__ import annotations

import hmac
import time

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for
from flask_wtf import FlaskForm
from wtforms import PasswordField, SubmitField
from wtforms.validators import DataRequired

from amo_bot.db.repositories import AuthAuditRepository
from amo_bot.webui.i18n import resolve_lang, translate


auth_bp = Blueprint("auth", __name__)


class LoginAttemptTracker:
    def __init__(self, *, base_delay_seconds: float, max_delay_seconds: float, max_keys: int = 1024) -> None:
        self._base_delay_seconds = float(base_delay_seconds)
        self._max_delay_seconds = float(max_delay_seconds)
        self._max_keys = max(1, int(max_keys))
        self._failures_by_key: dict[str, int] = {}

    def next_delay_seconds(self, key: str) -> float:
        if key not in self._failures_by_key and len(self._failures_by_key) >= self._max_keys:
            oldest_key = next(iter(self._failures_by_key))
            self._failures_by_key.pop(oldest_key, None)

        failures = self._failures_by_key.get(key, 0) + 1
        self._failures_by_key[key] = failures
        delay = self._base_delay_seconds * (2 ** (failures - 1))
        return min(delay, self._max_delay_seconds)

    def reset(self, key: str) -> None:
        self._failures_by_key.pop(key, None)


def _sleep_delay(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)


class LoginForm(FlaskForm):
    password = PasswordField("Passwort", validators=[DataRequired()])
    submit = SubmitField("Login")


class LogoutForm(FlaskForm):
    submit = SubmitField("Logout")


def _is_password_secure(password: str | None) -> bool:
    value = (password or "").strip()
    return bool(value) and value != "change_me"


def _get_client_key() -> str:
    return request.remote_addr or "unknown"


def _get_login_attempt_tracker() -> LoginAttemptTracker:
    tracker = current_app.extensions.get("amo.login_attempt_tracker")
    if tracker is None:
        settings = current_app.extensions["amo.settings"]
        tracker = LoginAttemptTracker(
            base_delay_seconds=settings.webui_login_delay_base_seconds,
            max_delay_seconds=settings.webui_login_delay_max_seconds,
        )
        current_app.extensions["amo.login_attempt_tracker"] = tracker
    return tracker


def _write_auth_audit(*, event_type: str, remote_addr: str | None) -> None:
    session_factory = current_app.extensions.get("amo.session_factory")
    if session_factory is None:
        return
    with session_factory() as db_session:
        AuthAuditRepository(db_session).write_login_event(event_type=event_type, remote_addr=remote_addr)


@auth_bp.get("/login")
def login_page():
    form = LoginForm()
    return render_template("login.html", form=form, insecure_password=False), 200


@auth_bp.post("/login")
def login_submit():
    form = LoginForm()
    configured = current_app.extensions["amo.settings"].webui_password

    if not _is_password_secure(configured):
        return render_template(
            "login.html",
            form=form,
            insecure_password=True,
            error_message=translate("login.disabled", lang=resolve_lang()),
        ), 503

    if not form.validate_on_submit():
        return render_template("login.html", form=form, insecure_password=False), 400

    key = _get_client_key()
    if not hmac.compare_digest(form.password.data or "", configured or ""):
        delay_seconds = _get_login_attempt_tracker().next_delay_seconds(key)
        delay_fn = current_app.extensions.get("amo.login_delay_fn", _sleep_delay)
        delay_fn(delay_seconds)
        _write_auth_audit(event_type="webui_login_failure", remote_addr=request.remote_addr)
        flash(translate("login.invalid_password", lang=resolve_lang()), "error")
        return render_template("login.html", form=form, insecure_password=False), 401

    _get_login_attempt_tracker().reset(key)
    _write_auth_audit(event_type="webui_login_success", remote_addr=request.remote_addr)
    session.clear()
    session["authenticated"] = True
    session.permanent = True
    return redirect(url_for("ui.dashboard"), code=302)


@auth_bp.post("/logout")
def logout_submit():
    form = LogoutForm()
    if not form.validate_on_submit():
        abort(400, description="invalid csrf token")

    session.clear()
    return redirect(url_for("auth.login_page"), code=302)


@auth_bp.get("/logout")
def logout_get():
    session.clear()
    return redirect(url_for("auth.login_page"), code=302)
