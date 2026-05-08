from __future__ import annotations

from functools import wraps
from typing import Any, Callable, TypeVar, cast

from flask import Blueprint, abort, current_app, redirect, render_template, request, session, url_for
from flask_wtf import FlaskForm
from wtforms import BooleanField, SelectField, StringField, TextAreaField
from wtforms.validators import DataRequired

from amo_bot.auth.roles import Role
from amo_bot.config.settings import Settings
from amo_bot.db.models import User
from amo_bot.db.repositories import ChatTopicRepository, UserRoleRepository

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


ALLOWED_ROLES: tuple[str, ...] = tuple(role.value for role in Role)


class UserRoleForm(FlaskForm):
    role = SelectField("Role", validators=[DataRequired()], choices=[(r, r) for r in ALLOWED_ROLES])


class TopicMetadataForm(FlaskForm):
    display_name = StringField("Display Name")
    notes = TextAreaField("Notes")
    enabled = BooleanField("Enabled", default=True)


@ui_bp.get("/dashboard")
@login_required
def dashboard():
    return render_template("dashboard.html"), 200


@ui_bp.get("/users")
@login_required
def users_page():
    settings = current_app.extensions["amo.settings"]
    session_factory = current_app.extensions["amo.plugin_service"]._session_factory
    with session_factory() as db_session:
        rows = db_session.query(User).order_by(User.telegram_user_id.asc()).all()
        users = [{"telegram_user_id": row.telegram_user_id, "role": row.role.name} for row in rows]

    return render_template(
        "users.html",
        users=users,
        roles=ALLOWED_ROLES,
        role_form=UserRoleForm(),
        owner_mutation_enabled=settings.webui_owner_telegram_id is not None,
        error_message=request.args.get("error", ""),
    ), 200


@ui_bp.post("/users/<int:telegram_user_id>/role")
@login_required
def update_user_role(telegram_user_id: int):
    form = UserRoleForm()
    if not form.validate_on_submit():
        abort(400, description="invalid role payload")

    role_name = (form.role.data or "").strip().lower()
    if role_name not in ALLOWED_ROLES:
        abort(400, description="invalid role")

    settings: Settings = current_app.extensions["amo.settings"]
    if settings.webui_owner_telegram_id is None:
        abort(403, description="WEBUI_OWNER_TELEGRAM_ID not configured; role mutation is disabled")

    session_factory = current_app.extensions["amo.plugin_service"]._session_factory
    with session_factory() as db_session:
        repo = UserRoleRepository(db_session)
        repo.set_user_role(
            actor_telegram_user_id=settings.webui_owner_telegram_id,
            target_telegram_user_id=telegram_user_id,
            role=Role(role_name),
        )

    return redirect(url_for("ui.users_page"), code=302)


@ui_bp.get("/groups")
@login_required
def groups_page():
    settings = current_app.extensions["amo.settings"]
    session_factory = current_app.extensions["amo.plugin_service"]._session_factory
    groups: list[dict[str, Any]] = []
    with session_factory() as db_session:
        repo = ChatTopicRepository(db_session)
        chats = repo.list_chats()
        for chat in chats:
            topics = repo.list_topics(chat.chat_id)
            groups.append(
                {
                    "chat_id": chat.chat_id,
                    "chat_type": chat.chat_type,
                    "title": chat.title,
                    "username": chat.username,
                    "last_seen_at": chat.last_seen_at,
                    "updated_at": chat.updated_at,
                    "topics": [
                        {
                            "message_thread_id": topic.message_thread_id,
                            "telegram_topic_name": topic.telegram_topic_name,
                            "display_name": topic.display_name,
                            "notes": topic.notes,
                            "enabled": topic.enabled,
                        }
                        for topic in topics
                    ],
                }
            )

    return render_template(
        "groups.html",
        groups=groups,
        topic_form=TopicMetadataForm(),
        owner_mutation_enabled=settings.webui_owner_telegram_id is not None,
        error_message=request.args.get("error", ""),
    ), 200


@ui_bp.post("/groups/<chat_id>/topics/<int:message_thread_id>")
@login_required
def update_topic_metadata(chat_id: str, message_thread_id: int):
    try:
        parsed_chat_id = int(chat_id)
    except ValueError:
        abort(404, description="invalid chat id")
    form = TopicMetadataForm()
    if not form.validate_on_submit():
        abort(400, description="invalid topic metadata payload")

    settings: Settings = current_app.extensions["amo.settings"]
    if settings.webui_owner_telegram_id is None:
        abort(403, description="WEBUI_OWNER_TELEGRAM_ID not configured; topic mutation is disabled")

    display_name = (form.display_name.data or "").strip() or None
    notes = (form.notes.data or "").strip() or None

    session_factory = current_app.extensions["amo.plugin_service"]._session_factory
    with session_factory() as db_session:
        repo = ChatTopicRepository(db_session)
        try:
            repo.update_topic_metadata(
                chat_id=parsed_chat_id,
                message_thread_id=message_thread_id,
                display_name=display_name,
                notes=notes,
                enabled=bool(form.enabled.data),
                actor_telegram_user_id=settings.webui_owner_telegram_id,
            )
        except ValueError:
            abort(404, description="topic not found")

    return redirect(url_for("ui.groups_page"), code=302)
