from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import select
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
    TopicAgentConfig,
    TopicAiSession,
    TopicDailyMemory,
    TopicLongMemory,
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
        allowed_topics: list[tuple[int, int]] | None = None,
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

        if allowed_topics is not None:
            self._session.query(PluginPolicyAllowedTopic).filter(PluginPolicyAllowedTopic.override_id == row.id).delete()
            deduped_topics = sorted(set(allowed_topics), key=lambda item: (item[0], item[1]))
            for chat_id, message_thread_id in deduped_topics:
                self._session.add(
                    PluginPolicyAllowedTopic(
                        override_id=row.id,
                        chat_id=chat_id,
                        message_thread_id=message_thread_id,
                    )
                )

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
    LEGACY_ACTIVATION_PENDING = "activation_pending"

    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert_from_manifest(self, manifest: PluginManifest) -> None:
        row = self._session.scalar(select(Plugin).where(Plugin.name == manifest.name))
        now = datetime.now(timezone.utc)
        if row is None:
            row = Plugin(
                name=manifest.name,
                version=manifest.version,
                enabled=0,
                activation_status=self.LEGACY_ACTIVATION_PENDING,
                manifest_json=manifest.model_dump_json(),
            )
            self._session.add(row)
            self._session.commit()
            return

        row.version = manifest.version
        row.manifest_json = manifest.model_dump_json()
        if not row.activation_status:
            row.activation_status = self.LEGACY_ACTIVATION_PENDING
        self._session.commit()

    def sync_discovered(self, manifests: Iterable[PluginManifest]) -> None:
        for manifest in manifests:
            row = self._session.scalar(select(Plugin).where(Plugin.name == manifest.name))
            if row is None:
                self._session.add(
                    Plugin(
                        name=manifest.name,
                        version=manifest.version,
                        enabled=0,
                        activation_status=self.LEGACY_ACTIVATION_PENDING,
                        manifest_json=manifest.model_dump_json(),
                    )
                )
                continue

            row.version = manifest.version
            row.manifest_json = manifest.model_dump_json()
            if not row.activation_status:
                row.activation_status = self.LEGACY_ACTIVATION_PENDING

        self._session.commit()

    def activate(self, plugin_name: str, *, actor_telegram_user_id: int | None = None) -> bool:
        row = self._session.scalar(select(Plugin).where(Plugin.name == plugin_name))
        if row is None:
            raise ValueError("plugin not found")

        changed = bool(not row.enabled or row.activation_status != "active")
        row.enabled = 1
        row.activation_status = "active"

        if changed:
            self._session.add(
                AuditEvent(
                    actor_telegram_user_id=actor_telegram_user_id,
                    event_type="plugin_activate",
                    payload_json=json.dumps({"plugin_name": plugin_name}),
                )
            )

        self._session.commit()
        return changed

    def deactivate(self, plugin_name: str, *, actor_telegram_user_id: int | None = None) -> bool:
        row = self._session.scalar(select(Plugin).where(Plugin.name == plugin_name))
        if row is None:
            raise ValueError("plugin not found")

        changed = bool(row.enabled)
        row.enabled = 0
        row.activation_status = self.LEGACY_ACTIVATION_PENDING

        if changed:
            self._session.add(
                AuditEvent(
                    actor_telegram_user_id=actor_telegram_user_id,
                    event_type="plugin_deactivate",
                    payload_json=json.dumps({"plugin_name": plugin_name}),
                )
            )

        self._session.commit()
        return changed

    def set_worker_state(
        self,
        *,
        plugin_name: str,
        state: str,
        heartbeat_at: datetime,
        restart_count: int,
        next_restart_at: datetime | None,
        last_error: str | None,
    ) -> None:
        row = self._session.scalar(select(Plugin).where(Plugin.name == plugin_name))
        if row is None:
            return
        row.worker_state = state
        row.worker_last_heartbeat_at = heartbeat_at
        row.worker_restart_count = restart_count
        row.worker_next_restart_at = next_restart_at
        row.worker_last_error = last_error
        row.updated_at = datetime.now(timezone.utc)
        self._session.commit()

    def set_run_state(
        self,
        *,
        plugin_name: str,
        last_run_at: datetime | None,
        last_status: str | None,
        next_run_at: datetime | None,
    ) -> None:
        row = self._session.scalar(select(Plugin).where(Plugin.name == plugin_name))
        if row is None:
            return
        row.last_run_at = last_run_at
        row.last_status = last_status
        row.next_run_at = next_run_at
        row.updated_at = datetime.now(timezone.utc)
        self._session.commit()

    def mark_scheduled_result(
        self,
        *,
        plugin_name: str,
        ran_at: datetime,
        status: str,
        next_run_at: datetime | None,
        actor_telegram_user_id: int | None = None,
        error: str | None = None,
    ) -> None:
        row = self._session.scalar(select(Plugin).where(Plugin.name == plugin_name))
        if row is None:
            return

        ran_at_naive = ran_at.replace(tzinfo=None) if ran_at.tzinfo is not None else ran_at
        next_run_naive = next_run_at.replace(tzinfo=None) if next_run_at is not None and next_run_at.tzinfo is not None else next_run_at

        row.last_run_at = ran_at_naive
        row.last_status = status
        row.next_run_at = next_run_naive
        row.updated_at = datetime.now(timezone.utc)

        payload: dict[str, object] = {
            "plugin_name": plugin_name,
            "status": status,
            "run_at": ran_at_naive.isoformat(),
            "next_run_at": next_run_naive.isoformat() if next_run_naive is not None else None,
        }
        if error is not None:
            payload["error"] = error

        self._session.add(
            AuditEvent(
                actor_telegram_user_id=actor_telegram_user_id,
                event_type="plugin_schedule_run",
                payload_json=json.dumps(payload),
            )
        )
        self._session.commit()

    def mark_worker_state(
        self,
        *,
        plugin_name: str,
        state: str,
        heartbeat_at: datetime,
        next_restart_at: datetime | None,
        last_error: str | None,
        increment_restart_count: bool = False,
    ) -> None:
        row = self._session.scalar(select(Plugin).where(Plugin.name == plugin_name))
        if row is None:
            return
        restart_count = row.worker_restart_count + 1 if increment_restart_count else row.worker_restart_count
        self.set_worker_state(
            plugin_name=plugin_name,
            state=state,
            heartbeat_at=heartbeat_at,
            restart_count=restart_count,
            next_restart_at=next_restart_at,
            last_error=last_error,
        )

    def get_status(self, plugin_name: str) -> PluginStatus | None:
        row = self._session.scalar(select(Plugin).where(Plugin.name == plugin_name))
        if row is None:
            return None
        return PluginStatus(
            name=row.name,
            enabled=row.enabled,
            activation_status=self._resolve_activation_status(row.name),
            worker_state=row.worker_state,
            worker_last_heartbeat_at=row.worker_last_heartbeat_at,
            worker_restart_count=row.worker_restart_count,
            worker_next_restart_at=row.worker_next_restart_at,
            worker_last_error=row.worker_last_error,
            last_run_at=row.last_run_at,
            last_status=row.last_status,
            next_run_at=row.next_run_at,
        )

    def list_due_scheduled_plugins(self, *, now: datetime) -> list[Plugin]:
        now_naive = now.replace(tzinfo=None) if now.tzinfo is not None else now
        rows = self._session.scalars(select(Plugin).order_by(Plugin.name.asc())).all()
        due: list[Plugin] = []
        for row in rows:
            if not row.enabled:
                continue
            if row.next_run_at is not None and row.next_run_at > now_naive:
                continue
            due.append(row)
        return due

    def list_plugins(self) -> list[Plugin]:
        return self._session.scalars(select(Plugin).order_by(Plugin.name.asc())).all()

    def list_statuses(self) -> list[PluginStatus]:
        rows = self._session.scalars(select(Plugin).order_by(Plugin.name.asc())).all()
        statuses: list[PluginStatus] = []
        for row in rows:
            activation_status = self._resolve_activation_status(row.name)
            statuses.append(
                PluginStatus(
                    name=row.name,
                    enabled=row.enabled,
                    activation_status=activation_status,
                    worker_state=row.worker_state,
                    worker_last_heartbeat_at=row.worker_last_heartbeat_at,
                    worker_restart_count=row.worker_restart_count,
                    worker_next_restart_at=row.worker_next_restart_at,
                    worker_last_error=row.worker_last_error,
                    last_run_at=row.last_run_at,
                    last_status=row.last_status,
                    next_run_at=row.next_run_at,
                )
            )
        return statuses

    def _resolve_activation_status(self, plugin_name: str) -> str:
        latest_request = self._session.scalar(
            select(PluginActivationRequest)
            .where(PluginActivationRequest.plugin_name == plugin_name)
            .order_by(PluginActivationRequest.requested_at.desc(), PluginActivationRequest.id.desc())
        )
        if latest_request is None:
            return "activation_pending"
        if latest_request.status == "pending":
            return "activation_pending"
        if latest_request.status == "approved":
            return "approved"
        if latest_request.status == "rejected":
            return "rejected"
        if latest_request.status == "blocked":
            return "blocked"
        return "activation_pending"

    def create_activation_request(
        self,
        plugin_name: str,
        *,
        actor_telegram_user_id: int | None,
        reason: str | None = None,
    ) -> PluginActivationRequestStatus:
        if actor_telegram_user_id is None:
            raise ValueError("actor required")
        return self.request_activation(
            plugin_name=plugin_name,
            requested_by_telegram_user_id=actor_telegram_user_id,
            reason=reason,
        )

    def request_activation(
        self,
        *,
        plugin_name: str,
        requested_by_telegram_user_id: int,
        reason: str | None,
    ) -> PluginActivationRequestStatus:
        plugin = self._session.scalar(select(Plugin).where(Plugin.name == plugin_name))
        if plugin is None:
            raise ValueError("plugin not found")

        existing_pending = self._session.scalar(
            select(PluginActivationRequest)
            .where(
                PluginActivationRequest.plugin_name == plugin_name,
                PluginActivationRequest.status == "pending",
            )
            .order_by(PluginActivationRequest.requested_at.desc(), PluginActivationRequest.id.desc())
        )
        if existing_pending is not None:
            return self._to_activation_request_status(existing_pending)

        request = PluginActivationRequest(
            plugin_name=plugin_name,
            status="pending",
            requested_by_telegram_user_id=requested_by_telegram_user_id,
            reason=reason,
        )
        self._session.add(request)
        self._session.commit()
        self._session.refresh(request)
        return self._to_activation_request_status(request)

    def get_activation_request(self, request_id: int) -> PluginActivationRequestStatus | None:
        request = self._session.scalar(select(PluginActivationRequest).where(PluginActivationRequest.id == request_id))
        if request is None:
            return None
        return self._to_activation_request_status(request)

    def resolve_activation_request(
        self,
        request_id: int,
        *,
        status: str,
        actor_telegram_user_id: int | None,
    ) -> bool:
        if actor_telegram_user_id is None:
            raise ValueError("actor required")
        resolved = self._resolve_activation_request_status(
            request_id=request_id,
            decision=status,
            resolved_by_telegram_user_id=actor_telegram_user_id,
            reason=None,
        )
        if resolved.plugin_name:
            plugin = self._session.scalar(select(Plugin).where(Plugin.name == resolved.plugin_name))
            if plugin is None:
                raise ValueError("plugin not found")
            event_type: str | None = None
            if resolved.status == "approved":
                plugin.enabled = 1
                plugin.activation_status = "active"
                event_type = "plugin_activation_request_approved"
                self._session.add(
                    AuditEvent(
                        actor_telegram_user_id=actor_telegram_user_id,
                        event_type="plugin_activate",
                        payload_json=json.dumps({"plugin_name": resolved.plugin_name}),
                    )
                )
            elif resolved.status in {"rejected", "blocked"}:
                plugin.enabled = 0
                plugin.activation_status = self.LEGACY_ACTIVATION_PENDING
                event_type = f"plugin_activation_request_{resolved.status}"
            if event_type is not None:
                self._session.add(
                    AuditEvent(
                        actor_telegram_user_id=actor_telegram_user_id,
                        event_type=event_type,
                        payload_json=json.dumps({"plugin_name": resolved.plugin_name, "request_id": request_id}),
                    )
                )
        self._session.commit()
        return True

    def _resolve_activation_request_status(
        self,
        *,
        request_id: int,
        decision: str,
        resolved_by_telegram_user_id: int,
        reason: str | None,
    ) -> PluginActivationRequestStatus:
        normalized_decision = decision.strip().lower()
        if normalized_decision not in {"approved", "rejected", "blocked"}:
            raise ValueError("invalid decision")

        request = self._session.scalar(select(PluginActivationRequest).where(PluginActivationRequest.id == request_id))
        if request is None:
            raise ValueError("request not found")

        request.status = normalized_decision
        request.resolved_by_telegram_user_id = resolved_by_telegram_user_id
        request.resolved_at = datetime.now(timezone.utc)
        if reason is not None:
            request.reason = reason

        self._session.commit()
        self._session.refresh(request)
        return self._to_activation_request_status(request)

    def list_activation_requests(
        self,
        *,
        plugin_name: str | None = None,
        status: str | None = None,
        limit: int = 20,
    ) -> list[PluginActivationRequestStatus]:
        normalized_status = None
        if status is not None:
            normalized_status = status.strip().lower()
            if normalized_status not in self.ACTIVATION_REQUEST_STATUSES:
                raise ValueError("invalid status")

        query = select(PluginActivationRequest)
        if plugin_name is not None:
            query = query.where(PluginActivationRequest.plugin_name == plugin_name)
        if normalized_status is not None:
            query = query.where(PluginActivationRequest.status == normalized_status)

        safe_limit = max(1, min(limit, 100))
        rows = self._session.scalars(
            query.order_by(PluginActivationRequest.requested_at.desc(), PluginActivationRequest.id.desc()).limit(safe_limit)
        ).all()
        return [self._to_activation_request_status(row) for row in rows]

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


class AuthAuditRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def write_login_event(self, *, event_type: str, remote_addr: str | None) -> None:
        self.log(
            actor_telegram_user_id=None,
            event_type=event_type,
            payload={"remote_addr": remote_addr},
        )

    def log(
        self,
        *,
        actor_telegram_user_id: int | None,
        event_type: str,
        payload: dict[str, object],
    ) -> None:
        self._session.add(
            AuditEvent(
                actor_telegram_user_id=actor_telegram_user_id,
                event_type=event_type,
                payload_json=json.dumps(payload),
            )
        )
        self._session.commit()


@dataclass(slots=True)
class TopicAgentConfigRecord:
    scope_type: str
    chat_id: int | None
    topic_id: int | None
    user_id: int | None
    ai_enabled: bool
    response_mode: str
    memory_retention_days: int
    tools_enabled: bool
    topic_soul_text: str | None
    topic_soul_owner_only_edit: bool


@dataclass(slots=True)
class TopicDailyMemoryRecord:
    scope_type: str
    chat_id: int | None
    topic_id: int | None
    user_id: int | None
    memory_date: str
    summary_text: str
    tokens_estimate: int


@dataclass(slots=True)
class TopicLongMemoryRecord:
    id: int
    scope_type: str
    chat_id: int | None
    topic_id: int | None
    user_id: int | None
    fact_text: str
    is_active: bool
    source_daily_memory_id: int | None


@dataclass(slots=True)
class TopicAiSessionRecord:
    scope_type: str
    chat_id: int | None
    topic_id: int | None
    user_id: int | None
    session_payload: dict[str, object]


class TopicAgentMemoryRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert_config(
        self,
        *,
        scope_type: str,
        chat_id: int | None = None,
        topic_id: int | None = None,
        user_id: int | None = None,
        ai_enabled: bool = False,
        response_mode: str = "command",
        memory_retention_days: int = 30,
        tools_enabled: bool = False,
        topic_soul_text: str | None = None,
        topic_soul_owner_only_edit: bool = True,
    ) -> TopicAgentConfigRecord:
        row = self._session.scalar(
            select(TopicAgentConfig).where(
                TopicAgentConfig.scope_type == scope_type,
                TopicAgentConfig.chat_id == chat_id,
                TopicAgentConfig.topic_id == topic_id,
                TopicAgentConfig.user_id == user_id,
            )
        )
        if row is None:
            row = TopicAgentConfig(
                scope_type=scope_type,
                chat_id=chat_id,
                topic_id=topic_id,
                user_id=user_id,
            )
            self._session.add(row)

        row.ai_enabled = ai_enabled
        row.response_mode = response_mode
        row.memory_retention_days = memory_retention_days
        row.tools_enabled = tools_enabled
        row.topic_soul_text = topic_soul_text
        row.topic_soul_owner_only_edit = topic_soul_owner_only_edit
        self._session.commit()
        self._session.refresh(row)
        return self._to_config_record(row)

    def get_config(
        self,
        *,
        scope_type: str,
        chat_id: int | None = None,
        topic_id: int | None = None,
        user_id: int | None = None,
    ) -> TopicAgentConfigRecord | None:
        row = self._session.scalar(
            select(TopicAgentConfig).where(
                TopicAgentConfig.scope_type == scope_type,
                TopicAgentConfig.chat_id == chat_id,
                TopicAgentConfig.topic_id == topic_id,
                TopicAgentConfig.user_id == user_id,
            )
        )
        if row is None:
            return None
        return self._to_config_record(row)

    def upsert_daily_memory(
        self,
        *,
        scope_type: str,
        memory_date: str,
        summary_text: str,
        tokens_estimate: int,
        chat_id: int | None = None,
        topic_id: int | None = None,
        user_id: int | None = None,
    ) -> TopicDailyMemoryRecord:
        row = self._session.scalar(
            select(TopicDailyMemory).where(
                TopicDailyMemory.scope_type == scope_type,
                TopicDailyMemory.chat_id == chat_id,
                TopicDailyMemory.topic_id == topic_id,
                TopicDailyMemory.user_id == user_id,
                TopicDailyMemory.memory_date == memory_date,
            )
        )
        if row is None:
            row = TopicDailyMemory(
                scope_type=scope_type,
                chat_id=chat_id,
                topic_id=topic_id,
                user_id=user_id,
                memory_date=memory_date,
            )
            self._session.add(row)

        row.summary_text = summary_text
        row.tokens_estimate = tokens_estimate
        self._session.commit()
        self._session.refresh(row)
        return self._to_daily_record(row)

    def get_daily_memory(
        self,
        *,
        scope_type: str,
        memory_date: str,
        chat_id: int | None = None,
        topic_id: int | None = None,
        user_id: int | None = None,
    ) -> TopicDailyMemoryRecord | None:
        row = self._session.scalar(
            select(TopicDailyMemory).where(
                TopicDailyMemory.scope_type == scope_type,
                TopicDailyMemory.chat_id == chat_id,
                TopicDailyMemory.topic_id == topic_id,
                TopicDailyMemory.user_id == user_id,
                TopicDailyMemory.memory_date == memory_date,
            )
        )
        if row is None:
            return None
        return self._to_daily_record(row)

    def list_daily_memories(
        self,
        *,
        scope_type: str,
        chat_id: int | None = None,
        topic_id: int | None = None,
        user_id: int | None = None,
        limit: int = 30,
    ) -> list[TopicDailyMemoryRecord]:
        safe_limit = max(1, min(limit, 365))
        rows = self._session.scalars(
            select(TopicDailyMemory)
            .where(
                TopicDailyMemory.scope_type == scope_type,
                TopicDailyMemory.chat_id == chat_id,
                TopicDailyMemory.topic_id == topic_id,
                TopicDailyMemory.user_id == user_id,
            )
            .order_by(TopicDailyMemory.memory_date.desc())
            .limit(safe_limit)
        ).all()
        return [self._to_daily_record(row) for row in rows]

    def create_long_memory(
        self,
        *,
        scope_type: str,
        fact_text: str,
        chat_id: int | None = None,
        topic_id: int | None = None,
        user_id: int | None = None,
        source_daily_memory_id: int | None = None,
    ) -> TopicLongMemoryRecord:
        row = TopicLongMemory(
            scope_type=scope_type,
            chat_id=chat_id,
            topic_id=topic_id,
            user_id=user_id,
            fact_text=fact_text,
            is_active=True,
            source_daily_memory_id=source_daily_memory_id,
        )
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return self._to_long_record(row)

    def list_long_memories(
        self,
        *,
        scope_type: str,
        chat_id: int | None = None,
        topic_id: int | None = None,
        user_id: int | None = None,
        active_only: bool = True,
        limit: int = 100,
    ) -> list[TopicLongMemoryRecord]:
        safe_limit = max(1, min(limit, 1000))
        query = select(TopicLongMemory).where(
            TopicLongMemory.scope_type == scope_type,
            TopicLongMemory.chat_id == chat_id,
            TopicLongMemory.topic_id == topic_id,
            TopicLongMemory.user_id == user_id,
        )
        if active_only:
            query = query.where(TopicLongMemory.is_active.is_(True))
        rows = self._session.scalars(query.order_by(TopicLongMemory.id.desc()).limit(safe_limit)).all()
        return [self._to_long_record(row) for row in rows]

    def deactivate_long_memory(self, *, memory_id: int) -> bool:
        row = self._session.scalar(select(TopicLongMemory).where(TopicLongMemory.id == memory_id))
        if row is None:
            return False
        if row.is_active:
            row.is_active = False
            self._session.commit()
        return True

    def upsert_ai_session(
        self,
        *,
        scope_type: str,
        session_payload: dict[str, object],
        chat_id: int | None = None,
        topic_id: int | None = None,
        user_id: int | None = None,
        last_message_at: datetime | None = None,
    ) -> TopicAiSessionRecord:
        row = self._session.scalar(
            select(TopicAiSession).where(
                TopicAiSession.scope_type == scope_type,
                TopicAiSession.chat_id == chat_id,
                TopicAiSession.topic_id == topic_id,
                TopicAiSession.user_id == user_id,
            )
        )
        if row is None:
            row = TopicAiSession(
                scope_type=scope_type,
                chat_id=chat_id,
                topic_id=topic_id,
                user_id=user_id,
            )
            self._session.add(row)

        row.session_payload_json = json.dumps(session_payload)
        row.last_message_at = last_message_at
        self._session.commit()
        self._session.refresh(row)
        return self._to_session_record(row)

    def get_ai_session(
        self,
        *,
        scope_type: str,
        chat_id: int | None = None,
        topic_id: int | None = None,
        user_id: int | None = None,
    ) -> TopicAiSessionRecord | None:
        row = self._session.scalar(
            select(TopicAiSession).where(
                TopicAiSession.scope_type == scope_type,
                TopicAiSession.chat_id == chat_id,
                TopicAiSession.topic_id == topic_id,
                TopicAiSession.user_id == user_id,
            )
        )
        if row is None:
            return None
        return self._to_session_record(row)

    @staticmethod
    def _to_config_record(row: TopicAgentConfig) -> TopicAgentConfigRecord:
        return TopicAgentConfigRecord(
            scope_type=row.scope_type,
            chat_id=row.chat_id,
            topic_id=row.topic_id,
            user_id=row.user_id,
            ai_enabled=row.ai_enabled,
            response_mode=row.response_mode,
            memory_retention_days=row.memory_retention_days,
            tools_enabled=row.tools_enabled,
            topic_soul_text=row.topic_soul_text,
            topic_soul_owner_only_edit=row.topic_soul_owner_only_edit,
        )

    @staticmethod
    def _to_daily_record(row: TopicDailyMemory) -> TopicDailyMemoryRecord:
        return TopicDailyMemoryRecord(
            scope_type=row.scope_type,
            chat_id=row.chat_id,
            topic_id=row.topic_id,
            user_id=row.user_id,
            memory_date=row.memory_date,
            summary_text=row.summary_text,
            tokens_estimate=row.tokens_estimate,
        )

    @staticmethod
    def _to_long_record(row: TopicLongMemory) -> TopicLongMemoryRecord:
        return TopicLongMemoryRecord(
            id=row.id,
            scope_type=row.scope_type,
            chat_id=row.chat_id,
            topic_id=row.topic_id,
            user_id=row.user_id,
            fact_text=row.fact_text,
            is_active=row.is_active,
            source_daily_memory_id=row.source_daily_memory_id,
        )

    @staticmethod
    def _to_session_record(row: TopicAiSession) -> TopicAiSessionRecord:
        payload: dict[str, object]
        try:
            raw = json.loads(row.session_payload_json or "{}")
            payload = raw if isinstance(raw, dict) else {}
        except json.JSONDecodeError:
            payload = {}

        return TopicAiSessionRecord(
            scope_type=row.scope_type,
            chat_id=row.chat_id,
            topic_id=row.topic_id,
            user_id=row.user_id,
            session_payload=payload,
        )
