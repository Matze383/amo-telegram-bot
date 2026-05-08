from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import select
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from amo_bot.auth.roles import Role
from amo_bot.db.models import AuditEvent, DbRole, Plugin, TelegramChat, TelegramTopic, User

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


class UserRoleRepository:
    """Minimal DB service for user-role lookup/set operations."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_user_role(self, telegram_user_id: int) -> Role | None:
        user = self._session.scalar(select(User).where(User.telegram_user_id == telegram_user_id))
        if user is None:
            return None
        return Role(user.role.name)

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
            row.telegram_topic_name = telegram_topic_name
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
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_plugins(self) -> list[PluginStatus]:
        rows = self._session.scalars(select(Plugin).order_by(Plugin.name.asc())).all()
        return [PluginStatus(name=row.name, enabled=bool(row.enabled)) for row in rows]

    def get_status(self, plugin_name: str) -> PluginStatus | None:
        row = self._session.scalar(select(Plugin).where(Plugin.name == plugin_name))
        if row is None:
            return None
        return PluginStatus(name=row.name, enabled=bool(row.enabled))

    def _upsert_from_manifest(self, manifest: PluginManifest) -> Plugin:
        row = self._session.scalar(select(Plugin).where(Plugin.name == manifest.name))
        manifest_json = manifest.model_dump_json()
        if row is None:
            row = Plugin(name=manifest.name, version=manifest.version, enabled=0, manifest_json=manifest_json)
            self._session.add(row)
            self._session.flush()
            return row

        changed = False
        if row.version != manifest.version:
            row.version = manifest.version
            changed = True
        if row.manifest_json != manifest_json:
            row.manifest_json = manifest_json
            changed = True
        if changed:
            self._session.flush()
        return row

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
        self._session.add(
            AuditEvent(
                actor_telegram_user_id=actor_telegram_user_id,
                event_type="plugin_deactivate",
                payload_json=json.dumps({"plugin_name": plugin_name}),
            )
        )
        self._session.commit()
        return True
