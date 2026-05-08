from __future__ import annotations

from functools import wraps
from typing import Any, Callable, TypeVar, cast

from flask import Blueprint, redirect, render_template, session, url_for

F = TypeVar("F", bound=Callable[..., Any])

ui_bp = Blueprint("ui", __name__)


def login_required(view: F) -> F:
    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any):
        if session.get("authenticated") is not True:
            return redirect(url_for("auth.login_page"), code=302)
        return view(*args, **kwargs)

    return cast(F, wrapped)


@ui_bp.get("/")
def index_redirect():
    if session.get("authenticated") is True:
        return redirect(url_for("ui.dashboard"), code=302)
    return redirect(url_for("auth.login_page"), code=302)


@ui_bp.get("/dashboard")
@login_required
def dashboard():
    return render_template("dashboard.html"), 200
