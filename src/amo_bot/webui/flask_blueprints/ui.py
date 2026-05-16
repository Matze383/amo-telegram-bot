from __future__ import annotations

from functools import wraps
from typing import Any, Callable, TypeVar, cast

from flask import Blueprint, abort, current_app, redirect, render_template, request, session, url_for
from sqlalchemy import select
from flask_wtf import FlaskForm
from wtforms import BooleanField, SelectField, StringField, TextAreaField
from wtforms.validators import DataRequired, Length

from amo_bot.auth.roles import Role
from amo_bot.config.settings import Settings
from amo_bot.db.models import GROUP_CHAT_TYPES, TopicAgentConfig, User
from amo_bot.db.repositories import (
    ChatScopedRoleRepository,
    ChatSeenUserRepository,
    PRIVATE_CHAT_THRESHOLD_ROLES,
    PrivateChatPolicyRepository,
    ChatTopicRepository,
    TopicAgentMemoryRepository,
    UserRoleRepository,
)

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
ALLOWED_GROUP_ROLES: tuple[str, ...] = tuple(role.value for role in Role if role is not Role.OWNER)
ALLOWED_PRIVATE_THRESHOLD_ROLES: tuple[str, ...] = tuple(role.value for role in PRIVATE_CHAT_THRESHOLD_ROLES)


class UserRoleForm(FlaskForm):
    role = SelectField("Role", validators=[DataRequired()], choices=[(r, r) for r in ALLOWED_ROLES])


class PrivateChatPolicyForm(FlaskForm):
    min_ai_role = SelectField(
        "Minimum private chat role for AI",
        validators=[DataRequired()],
        choices=[(r, r) for r in ALLOWED_PRIVATE_THRESHOLD_ROLES],
    )
    min_general_command_role = SelectField(
        "Minimum private chat role for general commands",
        validators=[DataRequired()],
        choices=[(r, r) for r in ALLOWED_PRIVATE_THRESHOLD_ROLES],
    )
    min_plugin_command_role = SelectField(
        "Minimum private chat role for plugin commands",
        validators=[DataRequired()],
        choices=[(r, r) for r in ALLOWED_PRIVATE_THRESHOLD_ROLES],
    )


class TopicMetadataForm(FlaskForm):
    display_name = StringField("Display Name", validators=[Length(max=255)])
    notes = TextAreaField("Notes", validators=[Length(max=2000)])
    topic_soul_text = TextAreaField("Topic Soul", validators=[Length(max=4000)])
    enabled = BooleanField("Enabled", default=True)
    ai_enabled = BooleanField("Topic AI Enabled", default=False)
    response_mode = SelectField(
        "Topic AI Response Mode",
        validators=[DataRequired()],
        choices=[("mention_or_reply", "mention_or_reply"), ("command", "command")],
        default="mention_or_reply",
    )


class GroupRoleForm(FlaskForm):
    telegram_user_id = SelectField("User", validators=[DataRequired()], coerce=int)
    role = SelectField("Role", validators=[DataRequired()], choices=[(r, r) for r in ALLOWED_GROUP_ROLES])


@ui_bp.get("/dashboard")
@login_required
def dashboard():
    settings: Settings = current_app.extensions["amo.settings"]
    session_factory = current_app.extensions["amo.plugin_service"]._session_factory
    statuses: list[dict[str, Any]] = []
    memory_entries: list[dict[str, Any]] = []
    with session_factory() as db_session:
        repo = TopicAgentMemoryRepository(db_session)

        rows = db_session.scalars(select(TopicAgentConfig).order_by(TopicAgentConfig.scope_type.asc(), TopicAgentConfig.id.asc())).all()
        for row in rows:
            cfg = repo.get_config(
                scope_type=row.scope_type,
                chat_id=row.chat_id,
                topic_id=row.topic_id,
                user_id=row.user_id,
            )
            if cfg is None:
                continue
            statuses.append(
                {
                    "scope": cfg.scope_type,
                    "chat_id": cfg.chat_id,
                    "topic_id": cfg.topic_id,
                    "user_id": cfg.user_id,
                    "ai_enabled": cfg.ai_enabled,
                    "response_mode": cfg.response_mode,
                }
            )

            daily_rows = repo.list_daily_memories(
                scope_type=cfg.scope_type,
                chat_id=cfg.chat_id,
                topic_id=cfg.topic_id,
                user_id=cfg.user_id,
                limit=5,
            )
            daily_dates = [daily.memory_date for daily in daily_rows]

            long_rows = repo.list_long_memories(
                scope_type=cfg.scope_type,
                chat_id=cfg.chat_id,
                topic_id=cfg.topic_id,
                user_id=cfg.user_id,
                active_only=False,
                limit=20,
            )
            long_entries = [
                {
                    "id": long_row.id,
                    "fact_text": long_row.fact_text,
                    "is_active": long_row.is_active,
                }
                for long_row in long_rows
            ]

            memory_entries.append(
                {
                    "scope": cfg.scope_type,
                    "chat_id": cfg.chat_id,
                    "topic_id": cfg.topic_id,
                    "user_id": cfg.user_id,
                    "daily_memory_dates": daily_dates,
                    "long_memories": long_entries,
                }
            )

    statuses.sort(key=lambda item: (item["scope"], item["chat_id"] or 0, item["topic_id"] or 0, item["user_id"] or 0))
    memory_entries.sort(key=lambda item: (item["scope"], item["chat_id"] or 0, item["topic_id"] or 0, item["user_id"] or 0))
    return render_template(
        "dashboard.html",
        topic_agent_statuses=statuses,
        topic_memory_entries=memory_entries,
        owner_mutation_enabled=settings.webui_owner_telegram_id is not None,
    ), 200


@ui_bp.get("/users")
@login_required
def users_page():
    settings = current_app.extensions["amo.settings"]
    session_factory = current_app.extensions["amo.plugin_service"]._session_factory
    with session_factory() as db_session:
        rows = db_session.query(User).order_by(User.telegram_user_id.asc()).all()
        policy = PrivateChatPolicyRepository(db_session).get_policy()
        users = [
            {
                "telegram_user_id": row.telegram_user_id,
                "role": row.role.name,
                "username": row.username,
                "first_name": row.first_name,
                "last_name": row.last_name,
                "last_seen_at": row.last_seen_at,
            }
            for row in rows
        ]

    return render_template(
        "users.html",
        users=users,
        roles=ALLOWED_ROLES,
        private_threshold_roles=ALLOWED_PRIVATE_THRESHOLD_ROLES,
        private_chat_policy={
            "min_ai_role": policy.min_ai_role.value,
            "min_general_command_role": policy.min_general_command_role.value,
            "min_plugin_command_role": policy.min_plugin_command_role.value,
        },
        role_form=UserRoleForm(),
        private_chat_policy_form=PrivateChatPolicyForm(),
        owner_mutation_enabled=settings.webui_owner_telegram_id is not None,
        error_message=request.args.get("error", ""),
    ), 200


@ui_bp.post("/users/private-chat-policy")
@login_required
def update_private_chat_policy():
    form = PrivateChatPolicyForm()
    if not form.validate_on_submit():
        abort(400, description="invalid private chat policy payload")

    settings: Settings = current_app.extensions["amo.settings"]
    if settings.webui_owner_telegram_id is None:
        abort(403, description="WEBUI_OWNER_TELEGRAM_ID not configured; private chat policy mutation is disabled")

    try:
        min_ai_role = PrivateChatPolicyRepository.validate_threshold_role(form.min_ai_role.data or "")
        min_general_command_role = PrivateChatPolicyRepository.validate_threshold_role(
            form.min_general_command_role.data or ""
        )
        min_plugin_command_role = PrivateChatPolicyRepository.validate_threshold_role(
            form.min_plugin_command_role.data or ""
        )
    except ValueError:
        abort(400, description="invalid private chat policy role")

    session_factory = current_app.extensions["amo.plugin_service"]._session_factory
    with session_factory() as db_session:
        repo = PrivateChatPolicyRepository(db_session)
        repo.update_policy(
            min_ai_role=min_ai_role,
            min_general_command_role=min_general_command_role,
            min_plugin_command_role=min_plugin_command_role,
        )

    return redirect(url_for("ui.users_page"), code=302)


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
        memory_repo = TopicAgentMemoryRepository(db_session)
        chats = repo.list_chats()
        users = db_session.query(User).order_by(User.telegram_user_id.asc()).all()
        users_by_id = {row.telegram_user_id: row for row in users}
        scoped_repo = ChatScopedRoleRepository(db_session)
        seen_repo = ChatSeenUserRepository(db_session)
        group_chat_ids = [chat.chat_id for chat in chats if chat.chat_type in GROUP_CHAT_TYPES]

        seen_user_ids_by_chat: dict[int, set[int]] = {}
        known_user_ids: set[int] = set()
        for group_chat_id in group_chat_ids:
            seen_user_ids = set(seen_repo.list_seen_users_for_chat(chat_id=group_chat_id))
            seen_user_ids_by_chat[group_chat_id] = seen_user_ids
            known_user_ids.update(seen_user_ids)

        bulk_group_roles = scoped_repo.list_group_roles_for_users(
            chat_ids=group_chat_ids,
            telegram_user_ids=list(known_user_ids),
        )

        for chat in chats:
            topics = repo.list_topics(chat.chat_id)
            group_user_roles: list[dict[str, Any]] = []
            if chat.chat_type in GROUP_CHAT_TYPES:
                seen_user_ids = seen_user_ids_by_chat.get(chat.chat_id, set())
                chat_role_rows = scoped_repo.list_group_role_users(chat.chat_id)
                assigned_user_ids = {row.telegram_user_id for row in chat_role_rows}
                display_user_ids = sorted(seen_user_ids | assigned_user_ids)

                for user_id in display_user_ids:
                    row = users_by_id.get(user_id)
                    group_role = bulk_group_roles.get((chat.chat_id, user_id))
                    group_user_roles.append(
                        {
                            "telegram_user_id": user_id,
                            "username": row.username if row is not None else None,
                            "first_name": row.first_name if row is not None else None,
                            "last_name": row.last_name if row is not None else None,
                            "role": group_role.value if group_role is not None else "normal",
                            "is_default": group_role is None,
                            "seen_in_chat": user_id in seen_user_ids,
                        }
                    )

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
                            "topic_soul_text": (
                                cfg.topic_soul_text
                                if (
                                    cfg := memory_repo.get_config(
                                        scope_type="topic",
                                        chat_id=chat.chat_id,
                                        topic_id=topic.message_thread_id,
                                        user_id=None,
                                    )
                                )
                                else None
                            ),
                            "ai_enabled": cfg.ai_enabled if cfg else False,
                            "response_mode": cfg.response_mode if cfg and cfg.response_mode else "mention_or_reply",
                        }
                        for topic in topics
                    ],
                    "group_user_roles": group_user_roles,
                }
            )

    group_role_form = GroupRoleForm()
    group_role_form.telegram_user_id.choices = []

    return render_template(
        "groups.html",
        groups=groups,
        topic_form=TopicMetadataForm(),
        group_role_form=group_role_form,
        group_roles=ALLOWED_GROUP_ROLES,
        owner_mutation_enabled=settings.webui_owner_telegram_id is not None,
        error_message=request.args.get("error", ""),
    ), 200


@ui_bp.get("/groups/<chat_id>")
@login_required
def group_detail_page(chat_id: str):
    try:
        parsed_chat_id = int(chat_id)
    except ValueError:
        abort(404, description="invalid chat id")

    session_factory = current_app.extensions["amo.plugin_service"]._session_factory
    group: dict[str, Any] | None = None
    with session_factory() as db_session:
        repo = ChatTopicRepository(db_session)
        memory_repo = TopicAgentMemoryRepository(db_session)
        chat = next((row for row in repo.list_chats() if row.chat_id == parsed_chat_id), None)
        if chat is None:
            abort(404, description="group not found")

        topics = repo.list_topics(chat.chat_id)
        group = {
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
                    "topic_soul_text": (
                        cfg.topic_soul_text
                        if (
                            cfg := memory_repo.get_config(
                                scope_type="topic",
                                chat_id=chat.chat_id,
                                topic_id=topic.message_thread_id,
                                user_id=None,
                            )
                        )
                        else None
                    ),
                    "ai_enabled": cfg.ai_enabled if cfg else False,
                    "response_mode": cfg.response_mode if cfg and cfg.response_mode else "mention_or_reply",
                }
                for topic in topics
            ],
        }

    return render_template(
        "group_detail.html",
        group=group,
        topic_form=TopicMetadataForm(),
        owner_mutation_enabled=current_app.extensions["amo.settings"].webui_owner_telegram_id is not None,
    ), 200


@ui_bp.post("/groups/<chat_id>/roles")
@login_required
def update_group_role(chat_id: str):
    try:
        parsed_chat_id = int(chat_id)
    except ValueError:
        abort(404, description="invalid chat id")

    form = GroupRoleForm()
    session_factory = current_app.extensions["amo.plugin_service"]._session_factory
    with session_factory() as db_session:
        seen_user_ids = set(ChatSeenUserRepository(db_session).list_seen_users_for_chat(chat_id=parsed_chat_id))
        assigned_user_ids = {row.telegram_user_id for row in ChatScopedRoleRepository(db_session).list_group_role_users(parsed_chat_id)}
        allowed_user_ids = sorted(seen_user_ids | assigned_user_ids)
        form.telegram_user_id.choices = [(user_id, str(user_id)) for user_id in allowed_user_ids]

    if not form.validate_on_submit():
        abort(400, description="invalid group role payload")

    role_name = (form.role.data or "").strip().lower()
    if role_name not in ALLOWED_GROUP_ROLES:
        abort(400, description="invalid group role")

    settings: Settings = current_app.extensions["amo.settings"]
    if settings.webui_owner_telegram_id is None:
        abort(403, description="WEBUI_OWNER_TELEGRAM_ID not configured; group role mutation is disabled")

    with session_factory() as db_session:
        chat_repo = ChatTopicRepository(db_session)
        chat = next((row for row in chat_repo.list_chats() if row.chat_id == parsed_chat_id), None)
        if chat is None or chat.chat_type not in GROUP_CHAT_TYPES:
            abort(404, description="group not found")

        repo = ChatScopedRoleRepository(db_session)
        user_id = int(form.telegram_user_id.data)
        if role_name == Role.NORMAL.value:
            repo.clear_group_role(
                chat_id=parsed_chat_id,
                telegram_user_id=user_id,
                actor_telegram_user_id=settings.webui_owner_telegram_id,
                source="webui",
            )
        else:
            repo.set_group_role(
                chat_id=parsed_chat_id,
                telegram_user_id=user_id,
                role=Role(role_name),
                actor_telegram_user_id=settings.webui_owner_telegram_id,
                source="webui",
            )

    return redirect(url_for("ui.groups_page"), code=302)


@ui_bp.post("/memory/long/<int:memory_id>/deactivate")
@login_required
def deactivate_long_memory(memory_id: int):
    settings: Settings = current_app.extensions["amo.settings"]
    if settings.webui_owner_telegram_id is None:
        abort(403, description="WEBUI_OWNER_TELEGRAM_ID not configured; memory mutation is disabled")

    session_factory = current_app.extensions["amo.plugin_service"]._session_factory
    with session_factory() as db_session:
        repo = TopicAgentMemoryRepository(db_session)
        changed = repo.deactivate_long_memory(memory_id=memory_id)
        if not changed:
            abort(404, description="long memory not found")

    return redirect(url_for("ui.dashboard"), code=302)


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
    topic_soul_text = (form.topic_soul_text.data or "").strip() or None
    response_mode = (form.response_mode.data or "").strip() or "mention_or_reply"

    session_factory = current_app.extensions["amo.plugin_service"]._session_factory
    with session_factory() as db_session:
        repo = ChatTopicRepository(db_session)
        memory_repo = TopicAgentMemoryRepository(db_session)
        try:
            repo.update_topic_metadata(
                chat_id=parsed_chat_id,
                message_thread_id=message_thread_id,
                display_name=display_name,
                notes=notes,
                enabled=bool(form.enabled.data),
                actor_telegram_user_id=settings.webui_owner_telegram_id,
            )
            existing = memory_repo.get_config(
                scope_type="topic",
                chat_id=parsed_chat_id,
                topic_id=message_thread_id,
                user_id=None,
            )
            memory_repo.upsert_config(
                scope_type="topic",
                chat_id=parsed_chat_id,
                topic_id=message_thread_id,
                user_id=None,
                ai_enabled=bool(form.ai_enabled.data),
                response_mode=response_mode,
                memory_retention_days=existing.memory_retention_days if existing else 30,
                tools_enabled=existing.tools_enabled if existing else False,
                main_soul_text=existing.main_soul_text if existing else None,
                topic_soul_text=topic_soul_text,
                topic_soul_owner_only_edit=existing.topic_soul_owner_only_edit if existing else True,
            )
        except ValueError:
            abort(404, description="topic not found")

    return redirect(url_for("ui.group_detail_page", chat_id=parsed_chat_id), code=302)
