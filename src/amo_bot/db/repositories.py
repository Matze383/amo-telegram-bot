from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import select
from datetime import datetime, timezone
from collections.abc import Iterable
from sqlalchemy.orm import Session

from amo_bot.auth.roles import Role
from amo_bot.consent import ConsentService
from amo_bot.db.models import (
    AuditEvent,
    ChatSeenUser,
    ChatUserRole,
    DbRole,
    Plugin,
    PluginActivationRequest,
    PluginPolicyAllowedGroup,
    PluginPolicyAllowedTopic,
    PluginPolicyOverride,
    TelegramChat,
    TelegramTopic,
    User,
)

if TYPE_CHECKING:
    from amo_bot.plugins.manifest import PluginManifest


@dataclass(slots=True)
class RoleChangeResult:
    changed: bool
    previous_role: Role | None
    new_role: Role


@dataclass(slots=True)
class PluginStatus:
    name: str
    enabled: bool
    activation_status: str = "activation_pending"
    worker_state: str | None = None
    worker_last_heartbeat_at: datetime | None = None
    worker_restart_count: int = 0
    worker_next_restart_at: datetime | None = None
    worker_last_error: str | None = None
    last_run_at: datetime | None = None
    last_status: str | None = None
    next_run_at: datetime | None = None


@dataclass(slots=True)
class PluginActivationRequestStatus:
    id: int
    plugin_name: str
    status: str
    requested_by_telegram_user_id: int | None = None
    resolved_by_telegram_user_id: int | None = None
    reason: str | None = None
    requested_at: datetime | None = None
    resolved_at: datetime | None = None


@dataclass(slots=True)
class PluginPolicyOverrideSnapshot:
    plugin_name: str
    roles_mode: str
    required_roles: list[Role]
    private_mode: str
    groups_mode: str
    topics_mode: str
    allowed_group_ids: list[int]
    allowed_topics: list[tuple[int, int]]


class PluginPolicyOverrideRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_snapshot(self, *, plugin_name: str) -> PluginPolicyOverrideSnapshot | None:
        row = self._session.scalar(select(PluginPolicyOverride).where(PluginPolicyOverride.plugin_name == plugin_name))
        if row is None:
            return None

        required_roles: list[Role] = []
        if row.required_roles_json:
            try:
                raw_roles = json.loads(row.required_roles_json)
            except json.JSONDecodeError:
                raw_roles = []
            if isinstance(raw_roles, list):
                for item in raw_roles:
                    if not isinstance(item, str):
                        continue
                    try:
                        required_roles.append(Role(item))
                    except ValueError:
                        continue

        allowed_group_ids = self._session.scalars(
            select(PluginPolicyAllowedGroup.chat_id)
            .where(PluginPolicyAllowedGroup.override_id == row.id)
            .order_by(PluginPolicyAllowedGroup.chat_id.asc())
        ).all()
        allowed_topics = self._session.execute(
            select(PluginPolicyAllowedTopic.chat_id, PluginPolicyAllowedTopic.message_thread_id)
            .where(PluginPolicyAllowedTopic.override_id == row.id)
            .order_by(PluginPolicyAllowedTopic.chat_id.asc(), PluginPolicyAllowedTopic.message_thread_id.asc())
        ).all()

        return PluginPolicyOverrideSnapshot(
            plugin_name=row.plugin_name,
            roles_mode=row.roles_mode,
            required_roles=required_roles,
            private_mode=row.private_mode,
            groups_mode=row.groups_mode,
            topics_mode=row.topics_mode,
            allowed_group_ids=list(allowed_group_ids),
            allowed_topics=[(int(chat_id), int(message_thread_id)) for chat_id, message_thread_id in allowed_topics],
        )

    def upsert_override(
        self,
        *,
        plugin_name: str,
        roles_mode: str,
        required_roles: list[Role],
        private_mode: str,
        groups_mode: str,
        topics_mode: str,
        allowed_group_ids: list[int] | None = None,
    ) -> None:
        row = self._session.scalar(select(PluginPolicyOverride).where(PluginPolicyOverride.plugin_name == plugin_name))
        required_roles_json = json.dumps([role.value for role in required_roles])

        if row is None:
            row = PluginPolicyOverride(
                plugin_name=plugin_name,
                roles_mode=roles_mode,
                required_roles_json=required_roles_json,
                private_mode=private_mode,
                groups_mode=groups_mode,
                topics_mode=topics_mode,
            )
            self._session.add(row)
            self._session.flush()
        else:
            row.roles_mode = roles_mode
            row.required_roles_json = required_roles_json
            row.private_mode = private_mode
            row.groups_mode = groups_mode
            row.topics_mode = topics_mode

        if allowed_group_ids is not None:
            self._session.query(PluginPolicyAllowedGroup).filter(PluginPolicyAllowedGroup.override_id == row.id).delete()
            deduped_group_ids = sorted(set(allowed_group_ids))
            for chat_id in deduped_group_ids:
                self._session.add(PluginPolicyAllowedGroup(override_id=row.id, chat_id=chat_id))

        self._session.commit()


class UserRoleRepository:
    """Minimal DB service for user-role lookup/set operations."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_user_role(self, telegram_user_id: int) -> Role | None:
        user = self._session.scalar(select(User).where(User.telegram_user_id == telegram_user_id))
        if user is None:
            return None
        return Role(user.role.name)

    def get_user_by_telegram_id(self, telegram_user_id: int) -> User | None:
        return self._session.scalar(select(User).where(User.telegram_user_id == telegram_user_id))

    def upsert_discovered_user(
        self,
        *,
        telegram_user_id: int,
        username: str | None,
        first_name: str | None,
        last_name: str | None,
        seen_at: datetime | None = None,
    ) -> User:
        seen = seen_at or datetime.now(timezone.utc)
        user = self._session.scalar(select(User).where(User.telegram_user_id == telegram_user_id))
        is_new_user = user is None

        if user is None:
            normal_role = self._session.scalar(select(DbRole).where(DbRole.name == Role.NORMAL.value))
            if normal_role is None:
                raise ValueError("role not found in db: normal")
            user = User(
                telegram_user_id=telegram_user_id,
                username=username,
                first_name=first_name,
                last_name=last_name,
                first_seen_at=seen,
                last_seen_at=seen,
                role_id=normal_role.id,
            )
            self._session.add(user)
        else:
            user.username = username
            user.first_name = first_name
            user.last_name = last_name
            user.last_seen_at = seen

        if is_new_user:
            ConsentService().ensure_pending_for_new_user(user, now=seen)

        self._session.commit()
        return user

    def bootstrap_owner_from_settings(self, *, owner_telegram_user_id: int | None) -> bool:
        """Ensure configured owner exists and has owner role.

        Returns True if a role/user change was applied, else False.
        """
        if owner_telegram_user_id is None:
            return False

        result = self.set_user_role(
            actor_telegram_user_id=owner_telegram_user_id,
            target_telegram_user_id=owner_telegram_user_id,
            role=Role.OWNER,
        )
        return result.changed

    def set_user_role(
        self,
        *,
        actor_telegram_user_id: int | None,
        target_telegram_user_id: int,
        role: Role,
    ) -> RoleChangeResult:
        role_row = self._session.scalar(select(DbRole).where(DbRole.name == role.value))
        if role_row is None:
            raise ValueError(f"role not found in db: {role.value}")

        user = self._session.scalar(select(User).where(User.telegram_user_id == target_telegram_user_id))
        previous_role: Role | None = None
        changed = False

        if user is None:
            user = User(telegram_user_id=target_telegram_user_id, role_id=role_row.id)
            self._session.add(user)
            changed = True
        else:
            previous_role = Role(user.role.name)
            if user.role_id != role_row.id:
                user.role_id = role_row.id
                changed = True

        if changed:
            event = AuditEvent(
                actor_telegram_user_id=actor_telegram_user_id,
                event_type="role_set",
                payload_json=json.dumps(
                    {
                        "target_telegram_user_id": target_telegram_user_id,
                        "previous_role": previous_role.value if previous_role else None,
                        "new_role": role.value,
                    }
                ),
            )
            self._session.add(event)

        self._session.commit()

        return RoleChangeResult(changed=changed, previous_role=previous_role, new_role=role)


class ChatScopedRoleRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_group_role(self, *, chat_id: int, telegram_user_id: int) -> Role | None:
        user = self._session.scalar(select(User).where(User.telegram_user_id == telegram_user_id))
        if user is None:
            return None
        row = self._session.scalar(
            select(ChatUserRole).where(ChatUserRole.chat_id == chat_id, ChatUserRole.user_id == user.id)
        )
        if row is None:
            return None
        return Role(row.role.name)

    def set_group_role(
        self,
        *,
        chat_id: int,
        telegram_user_id: int,
        role: Role,
        actor_telegram_user_id: int | None = None,
        source: str | None = None,
        changed_at: datetime | None = None,
    ) -> RoleChangeResult:
        user = self._session.scalar(select(User).where(User.telegram_user_id == telegram_user_id))
        if user is None:
            normal_role = self._session.scalar(select(DbRole).where(DbRole.name == Role.NORMAL.value))
            if normal_role is None:
                raise ValueError("role not found in db: normal")
            user = User(telegram_user_id=telegram_user_id, role_id=normal_role.id)
            self._session.add(user)
            self._session.flush()

        role_row = self._session.scalar(select(DbRole).where(DbRole.name == role.value))
        if role_row is None:
            raise ValueError(f"role not found in db: {role.value}")

        row = self._session.scalar(
            select(ChatUserRole).where(ChatUserRole.chat_id == chat_id, ChatUserRole.user_id == user.id)
        )
        previous_role: Role | None = None
        changed = False
        if row is None:
            row = ChatUserRole(chat_id=chat_id, user_id=user.id, role_id=role_row.id)
            self._session.add(row)
            changed = True
        else:
            previous_role = Role(row.role.name)
            if row.role_id != role_row.id:
                row.role_id = role_row.id
                row.updated_at = changed_at or datetime.now(timezone.utc)
                changed = True

        if changed:
            self._session.add(
                AuditEvent(
                    actor_telegram_user_id=actor_telegram_user_id,
                    event_type="group_role_set",
                    payload_json=json.dumps(
                        {
                            "chat_id": chat_id,
                            "target_telegram_user_id": telegram_user_id,
                            "previous_role": previous_role.value if previous_role else None,
                            "new_role": role.value,
                            "source": source,
                        }
                    ),
                )
            )

        self._session.commit()
        return RoleChangeResult(changed=changed, previous_role=previous_role, new_role=role)

    def list_group_role_users(self, chat_id: int) -> list[User]:
        return self._session.scalars(
            select(User)
            .join(ChatUserRole, ChatUserRole.user_id == User.id)
            .where(ChatUserRole.chat_id == chat_id)
            .order_by(User.telegram_user_id.asc())
        ).all()

    def list_group_roles_for_users(
        self,
        *,
        chat_ids: Iterable[int],
        telegram_user_ids: Iterable[int],
    ) -> dict[tuple[int, int], Role]:
        chat_id_list = list(chat_ids)
        telegram_user_id_list = list(telegram_user_ids)
        if not chat_id_list or not telegram_user_id_list:
            return {}

        user_rows = self._session.scalars(
            select(User).where(User.telegram_user_id.in_(telegram_user_id_list))
        ).all()
        if not user_rows:
            return {}

        user_id_to_telegram_user_id = {row.id: row.telegram_user_id for row in user_rows}
        scoped_rows = self._session.scalars(
            select(ChatUserRole).where(
                ChatUserRole.chat_id.in_(chat_id_list),
                ChatUserRole.user_id.in_(user_id_to_telegram_user_id.keys()),
            )
        ).all()

        result: dict[tuple[int, int], Role] = {}
        for row in scoped_rows:
            telegram_user_id = user_id_to_telegram_user_id.get(row.user_id)
            if telegram_user_id is None:
                continue
            result[(row.chat_id, telegram_user_id)] = Role(row.role.name)
        return result

    def clear_group_role(
        self,
        *,
        chat_id: int,
        telegram_user_id: int,
        actor_telegram_user_id: int | None = None,
        source: str | None = None,
    ) -> bool:
        user = self._session.scalar(select(User).where(User.telegram_user_id == telegram_user_id))
        if user is None:
            return False
        row = self._session.scalar(
            select(ChatUserRole).where(ChatUserRole.chat_id == chat_id, ChatUserRole.user_id == user.id)
        )
        if row is None:
            return False
        previous_role = Role(row.role.name)
        self._session.delete(row)
        self._session.add(
            AuditEvent(
                actor_telegram_user_id=actor_telegram_user_id,
                event_type="group_role_clear",
                payload_json=json.dumps(
                    {
                        "chat_id": chat_id,
                        "target_telegram_user_id": telegram_user_id,
                        "previous_role": previous_role.value,
                        "new_role": Role.NORMAL.value,
                        "source": source,
                    }
                ),
            )
        )
        self._session.commit()
        return True


class ChatSeenUserRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    @staticmethod
    def _ensure_utc(dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    @classmethod
    def _normalize_row_timestamps(cls, row: ChatSeenUser) -> ChatSeenUser:
        row.first_seen_at = cls._ensure_utc(row.first_seen_at)
        row.last_seen_at = cls._ensure_utc(row.last_seen_at)
        return row

    def mark_seen(self, *, chat_id: int, telegram_user_id: int, seen_at: datetime | None = None) -> ChatSeenUser:
        seen = self._ensure_utc(seen_at or datetime.now(timezone.utc))
        row = self._session.scalar(
            select(ChatSeenUser).where(
                ChatSeenUser.chat_id == chat_id,
                ChatSeenUser.telegram_user_id == telegram_user_id,
            )
        )
        if row is None:
            row = ChatSeenUser(
                chat_id=chat_id,
                telegram_user_id=telegram_user_id,
                first_seen_at=seen,
                last_seen_at=seen,
            )
            self._session.add(row)
        else:
            row.last_seen_at = seen

        self._session.commit()
        self._session.refresh(row)
        return self._normalize_row_timestamps(row)

    def list_seen_users_for_chat(self, *, chat_id: int) -> list[int]:
        rows = self._session.scalars(
            select(ChatSeenUser.telegram_user_id)
            .where(ChatSeenUser.chat_id == chat_id)
            .order_by(ChatSeenUser.telegram_user_id.asc())
        ).all()
        return list(rows)


class ChatTopicRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert_chat(
        self,
        chat_id: int,
        chat_type: str,
        title: str | None = None,
        username: str | None = None,
        seen_at: datetime | None = None,
    ) -> TelegramChat:
        seen = seen_at or datetime.now(timezone.utc)
        row = self._session.scalar(select(TelegramChat).where(TelegramChat.chat_id == chat_id))
        if row is None:
            row = TelegramChat(
                chat_id=chat_id,
                chat_type=chat_type,
                title=title,
                username=username,
                first_seen_at=seen,
                last_seen_at=seen,
                updated_at=seen,
            )
            self._session.add(row)
        else:
            row.chat_type = chat_type
            row.title = title
            row.username = username
            row.last_seen_at = seen
            row.updated_at = seen

        self._session.commit()
        return row

    def upsert_topic(
        self,
        chat_id: int,
        message_thread_id: int,
        telegram_topic_name: str | None = None,
        seen_at: datetime | None = None,
    ) -> TelegramTopic:
        seen = seen_at or datetime.now(timezone.utc)
        row = self._session.scalar(
            select(TelegramTopic).where(
                TelegramTopic.chat_id == chat_id,
                TelegramTopic.message_thread_id == message_thread_id,
            )
        )
        if row is None:
            row = TelegramTopic(
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                telegram_topic_name=telegram_topic_name,
                first_seen_at=seen,
                last_seen_at=seen,
                updated_at=seen,
            )
            self._session.add(row)
        else:
            cleaned_name = telegram_topic_name.strip() if isinstance(telegram_topic_name, str) else None
            if cleaned_name:
                row.telegram_topic_name = cleaned_name
            row.last_seen_at = seen
            row.updated_at = seen

        self._session.commit()
        return row

    def list_chats(self) -> list[TelegramChat]:
        return self._session.scalars(select(TelegramChat).order_by(TelegramChat.chat_id.asc())).all()

    def list_topics(self, chat_id: int) -> list[TelegramTopic]:
        return self._session.scalars(
            select(TelegramTopic)
            .where(TelegramTopic.chat_id == chat_id)
            .order_by(TelegramTopic.message_thread_id.asc())
        ).all()

    def update_topic_metadata(
        self,
        chat_id: int,
        message_thread_id: int,
        display_name: str | None = None,
        notes: str | None = None,
        enabled: bool = True,
        actor_telegram_user_id: int | None = None,
    ) -> TelegramTopic:
        topic = self._session.scalar(
            select(TelegramTopic).where(
                TelegramTopic.chat_id == chat_id,
                TelegramTopic.message_thread_id == message_thread_id,
            )
        )
        if topic is None:
            raise ValueError("topic not found")

        topic.display_name = display_name
        topic.notes = notes
        topic.enabled = enabled
        topic.updated_at = datetime.now(timezone.utc)

        self._session.add(
            AuditEvent(
                actor_telegram_user_id=actor_telegram_user_id,
                event_type="topic_metadata_update",
                payload_json=json.dumps(
                    {
                        "chat_id": chat_id,
                        "message_thread_id": message_thread_id,
                        "display_name": display_name,
                        "notes": notes,
                        "enabled": enabled,
                    }
                ),
            )
        )

        self._session.commit()
        return topic


class PluginRepository:
    ACTIVATION_REQUEST_STATUSES = {"pending", "approved", "rejected", "blocked"}

    def __init__(self, session: Session) -> None:
        self._session = session

    @staticmethod
    def _to_status(row: Plugin) -> PluginStatus:
        return PluginStatus(
            name=row.name,
            enabled=bool(row.enabled),
            activation_status=(row.activation_status or "activation_pending"),
            worker_state=row.worker_state,
            worker_last_heartbeat_at=row.worker_last_heartbeat_at,
            worker_restart_count=int(row.worker_restart_count or 0),
            worker_next_restart_at=row.worker_next_restart_at,
            worker_last_error=row.worker_last_error,
            last_run_at=row.last_run_at,
            last_status=row.last_status,
            next_run_at=row.next_run_at,
        )

    @staticmethod
    def _to_activation_request_status(row: PluginActivationRequest) -> PluginActivationRequestStatus:
        return PluginActivationRequestStatus(
            id=row.id,
            plugin_name=row.plugin_name,
            status=row.status,
            requested_by_telegram_user_id=row.requested_by_telegram_user_id,
            resolved_by_telegram_user_id=row.resolved_by_telegram_user_id,
            reason=row.reason,
            requested_at=row.requested_at,
            resolved_at=row.resolved_at,
        )

    def list_plugins(self) -> list[PluginStatus]:
        rows = self._session.scalars(select(Plugin).order_by(Plugin.name.asc())).all()
        return [self._to_status(row) for row in rows]

    def get_status(self, plugin_name: str) -> PluginStatus | None:
        row = self._session.scalar(select(Plugin).where(Plugin.name == plugin_name))
        if row is None:
            return None
        return self._to_status(row)

    def create_activation_request(
        self,
        plugin_name: str,
        *,
        actor_telegram_user_id: int | None,
        reason: str | None = None,
    ) -> PluginActivationRequestStatus:
        plugin = self._session.scalar(select(Plugin).where(Plugin.name == plugin_name))
        if plugin is None:
            raise ValueError("plugin not found")

        if bool(plugin.enabled) or plugin.activation_status == "active":
            raise ValueError("plugin already active")

        request = PluginActivationRequest(
            plugin_name=plugin_name,
            status="pending",
            requested_by_telegram_user_id=actor_telegram_user_id,
            reason=reason,
        )
        self._session.add(request)
        self._session.add(
            AuditEvent(
                actor_telegram_user_id=actor_telegram_user_id,
                event_type="plugin_activation_request_created",
                payload_json=json.dumps({"plugin_name": plugin_name, "status": "pending"}),
            )
        )
        self._session.commit()
        return self._to_activation_request_status(request)

    def get_activation_request(self, request_id: int) -> PluginActivationRequestStatus | None:
        row = self._session.get(PluginActivationRequest, request_id)
        if row is None:
            return None
        return self._to_activation_request_status(row)

    def resolve_activation_request(
        self,
        request_id: int,
        *,
        status: str,
        actor_telegram_user_id: int | None,
    ) -> bool:
        if status not in {"approved", "rejected", "blocked"}:
            raise ValueError("invalid activation request status")

        request = self._session.get(PluginActivationRequest, request_id)
        if request is None:
            raise ValueError("activation request not found")
        if request.status != "pending":
            return False

        plugin = self._session.scalar(select(Plugin).where(Plugin.name == request.plugin_name))
        if plugin is None:
            raise ValueError("plugin not found")

        request.status = status
        request.resolved_by_telegram_user_id = actor_telegram_user_id
        request.resolved_at = datetime.now(timezone.utc)
        self._session.add(
            AuditEvent(
                actor_telegram_user_id=actor_telegram_user_id,
                event_type=f"plugin_activation_request_{status}",
                payload_json=json.dumps(
                    {"plugin_name": request.plugin_name, "activation_request_id": request.id, "status": status}
                ),
            )
        )

        if status == "approved":
            # Approval is the explicit hand-off into the existing activation path.
            return self.activate(request.plugin_name, actor_telegram_user_id=actor_telegram_user_id)

        if not bool(plugin.enabled):
            plugin.activation_status = "activation_pending"
        self._session.commit()
        return True

    def _upsert_from_manifest(self, manifest: PluginManifest) -> Plugin:
        row = self._session.scalar(select(Plugin).where(Plugin.name == manifest.name))
        manifest_json = manifest.model_dump_json()
        if row is None:
            row = Plugin(name=manifest.name, version=manifest.version, enabled=0, manifest_json=manifest_json)
            if manifest.schedule:
                row.next_run_at = datetime.now(timezone.utc)
            self._session.add(row)
            self._session.flush()
            return row

        changed = False
        if row.version != manifest.version:
            row.version = manifest.version
            changed = True
        if not (row.activation_status or "").strip():
            row.activation_status = "activation_pending"
            changed = True
        if row.manifest_json != manifest_json:
            row.manifest_json = manifest_json
            changed = True
        if changed:
            self._session.flush()
        return row

    def list_due_scheduled_plugins(self, *, now: datetime) -> list[Plugin]:
        rows = self._session.scalars(
            select(Plugin)
            .where(Plugin.enabled == 1)
            .where(Plugin.next_run_at.is_not(None))
            .where(Plugin.next_run_at <= now)
            .order_by(Plugin.name.asc())
        ).all()
        from amo_bot.plugins.manifest import PluginManifest

        return [row for row in rows if PluginManifest.model_validate_json(row.manifest_json).schedule is not None]

    def mark_scheduled_result(
        self,
        *,
        plugin_name: str,
        status: str,
        next_run_at: datetime,
        ran_at: datetime,
    ) -> None:
        row = self._session.scalar(select(Plugin).where(Plugin.name == plugin_name))
        if row is None:
            raise ValueError("plugin not found")
        row.last_run_at = ran_at
        row.last_status = status
        row.next_run_at = next_run_at
        self._session.commit()

    def mark_worker_state(
        self,
        *,
        plugin_name: str,
        state: str,
        heartbeat_at: datetime | None = None,
        next_restart_at: datetime | None = None,
        last_error: str | None = None,
        increment_restart_count: bool = False,
    ) -> None:
        row = self._session.scalar(select(Plugin).where(Plugin.name == plugin_name))
        if row is None:
            raise ValueError("plugin not found")
        row.worker_state = state
        row.worker_last_heartbeat_at = heartbeat_at
        row.worker_next_restart_at = next_restart_at
        row.worker_last_error = last_error
        if increment_restart_count:
            row.worker_restart_count = int(row.worker_restart_count or 0) + 1
        self._session.commit()

    def sync_discovered(self, manifests: list[PluginManifest]) -> None:
        for manifest in manifests:
            self._upsert_from_manifest(manifest)
        self._session.commit()

    def activate(self, plugin_name: str, *, actor_telegram_user_id: int | None) -> bool:
        row = self._session.scalar(select(Plugin).where(Plugin.name == plugin_name))
        if row is None:
            raise ValueError("plugin not found")
        if bool(row.enabled):
            return False

        row.enabled = 1
        row.activation_status = "active"
        self._session.add(
            AuditEvent(
                actor_telegram_user_id=actor_telegram_user_id,
                event_type="plugin_activate",
                payload_json=json.dumps({"plugin_name": plugin_name}),
            )
        )
        self._session.commit()
        return True

    def deactivate(self, plugin_name: str, *, actor_telegram_user_id: int | None) -> bool:
        row = self._session.scalar(select(Plugin).where(Plugin.name == plugin_name))
        if row is None:
            raise ValueError("plugin not found")
        if not bool(row.enabled):
            return False

        row.enabled = 0
        row.activation_status = "activation_pending"
        self._session.add(
            AuditEvent(
                actor_telegram_user_id=actor_telegram_user_id,
                event_type="plugin_deactivate",
                payload_json=json.dumps({"plugin_name": plugin_name}),
            )
        )
        self._session.commit()
        return True


class AuthAuditRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def write_login_event(self, *, event_type: str, remote_addr: str | None) -> None:
        self._session.add(
            AuditEvent(
                actor_telegram_user_id=None,
                event_type=event_type,
                payload_json=json.dumps({"remote_addr": remote_addr or "unknown"}),
            )
        )
        self._session.commit()
