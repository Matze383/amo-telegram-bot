from __future__ import annotations

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for
from flask_wtf import FlaskForm
from wtforms import PasswordField, SubmitField
from wtforms.validators import DataRequired


auth_bp = Blueprint("auth", __name__)


class LoginForm(FlaskForm):
    password = PasswordField("Passwort", validators=[DataRequired()])
    submit = SubmitField("Login")


class LogoutForm(FlaskForm):
    submit = SubmitField("Logout")


def _is_password_secure(password: str | None) -> bool:
    value = (password or "").strip()
    return bool(value) and value != "change_me"


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
            error_message="Login deaktiviert: WEBUI_PASSWORD fehlt oder ist unsicher (change_me).",
        ), 503

    if not form.validate_on_submit():
        return render_template("login.html", form=form, insecure_password=False), 400

    if form.password.data != configured:
        flash("Ungültiges Passwort.", "error")
        return render_template("login.html", form=form, insecure_password=False), 401

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
