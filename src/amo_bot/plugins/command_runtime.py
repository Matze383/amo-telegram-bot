from __future__ import annotations

import asyncio
import importlib.util
import inspect
import json
import logging
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from sqlalchemy.orm import sessionmaker

from amo_bot.auth.roles import Role
from amo_bot.db.models import AuditEvent
from amo_bot.db.repositories import PluginRepository
from amo_bot.plugins.loader import PluginLoader
from amo_bot.plugins.manifest import PluginManifest

logger = logging.getLogger(__name__)

SendMessageFn = Callable[[int, str], Awaitable[object]]
ReplyFn = Callable[[int, int, str], Awaitable[object]]


@dataclass(slots=True, frozen=True)
class CommandActor:
    telegram_user_id: int
    role: Role


@dataclass(slots=True, frozen=True)
class CommandInvocation:
    command_name: str
    argument: str | None
    chat_id: int
    message_id: int


@dataclass(slots=True, frozen=True)
class PluginCommandContext:
    plugin_id: str
    run_id: str
    trigger_type: str
    chat_id: int
    message_id: int
    user_id: int
    role: Role
    command_name: str
    argument: str | None


class PluginCapabilityError(RuntimeError):
    pass


class PluginHostAPI:
    def __init__(
        self,
        *,
        send_message: SendMessageFn,
        reply: ReplyFn,
        required_permissions: set[str] | None = None,
    ) -> None:
        self._send_message = send_message
        self._reply = reply
        self._required_permissions = required_permissions

    def _require_permission(self, permission: str, operation: str) -> None:
        if self._required_permissions is not None and permission not in self._required_permissions:
            raise PluginCapabilityError(f"operation '{operation}' requires capability '{permission}'")

    async def send_message(self, chat_id: int, text: str) -> object:
        self._require_permission("send_message", "send_message")
        if not isinstance(chat_id, int):
            raise ValueError("chat_id must be int")
        text_clean = (text or "").strip()
        if not text_clean:
            raise ValueError("text must not be empty")
        return await self._send_message(chat_id, text_clean[:4000])

    async def reply(self, chat_id: int, message_id: int, text: str) -> object:
        self._require_permission("send_message", "reply")
        if not isinstance(chat_id, int) or not isinstance(message_id, int):
            raise ValueError("chat_id and message_id must be int")
        text_clean = (text or "").strip()
        if not text_clean:
            raise ValueError("text must not be empty")
        return await self._reply(chat_id, message_id, text_clean[:4000])


class PluginCommandExecutor:
    def __init__(
        self,
        *,
        loader: PluginLoader,
        session_factory: sessionmaker,
        send_message: SendMessageFn,
        reply: ReplyFn,
        timeout_seconds: float = 2.0,
    ) -> None:
        self._loader = loader
        self._session_factory = session_factory
        self._send_message = send_message
        self._reply = reply
        self._timeout_seconds = timeout_seconds

    async def execute(self, *, actor: CommandActor, invocation: CommandInvocation) -> None:
        manifest = self._find_manifest_for_command(invocation.command_name)
        if manifest is None:
            return

        run_id = str(uuid.uuid4())
        if not self._is_role_allowed(manifest, actor.role):
            self._write_audit(
                event_type="plugin_command_denied",
                actor_telegram_user_id=actor.telegram_user_id,
                payload={
                    "plugin_name": manifest.name,
                    "command": invocation.command_name,
                    "reason": "role_denied",
                    "run_id": run_id,
                },
            )
            return

        try:
            with self._session_factory() as session:
                repo = PluginRepository(session)
                status = repo.get_status(manifest.name)
        except Exception:
            logger.exception("plugin status lookup failed plugin=%s", manifest.name)
            return

        if status is None or not status.enabled:
            self._write_audit(
                event_type="plugin_command_skipped",
                actor_telegram_user_id=actor.telegram_user_id,
                payload={
                    "plugin_name": manifest.name,
                    "command": invocation.command_name,
                    "reason": "plugin_disabled",
                    "run_id": run_id,
                },
            )
            return

        context = PluginCommandContext(
            plugin_id=manifest.name,
            run_id=run_id,
            trigger_type="command",
            chat_id=invocation.chat_id,
            message_id=invocation.message_id,
            user_id=actor.telegram_user_id,
            role=actor.role,
            command_name=invocation.command_name,
            argument=invocation.argument,
        )

        self._write_audit(
            event_type="plugin_command_start",
            actor_telegram_user_id=actor.telegram_user_id,
            payload={
                "plugin_name": manifest.name,
                "command": invocation.command_name,
                "run_id": run_id,
            },
        )

        start = time.monotonic()
        try:
            host_api = PluginHostAPI(
                send_message=self._send_message,
                reply=self._reply,
                required_permissions=set(manifest.required_permissions),
            )
            handler = self._load_handler(manifest)
            await asyncio.wait_for(handler(context, host_api), timeout=self._timeout_seconds)
        except asyncio.TimeoutError:
            self._write_audit(
                event_type="plugin_command_timeout",
                actor_telegram_user_id=actor.telegram_user_id,
                payload={
                    "plugin_name": manifest.name,
                    "command": invocation.command_name,
                    "run_id": run_id,
                    "timeout_seconds": self._timeout_seconds,
                },
            )
            return
        except Exception as exc:
            logger.exception("plugin command failed plugin=%s command=%s", manifest.name, invocation.command_name)
            self._write_audit(
                event_type="plugin_command_error",
                actor_telegram_user_id=actor.telegram_user_id,
                payload={
                    "plugin_name": manifest.name,
                    "command": invocation.command_name,
                    "run_id": run_id,
                    "error": str(exc),
                },
            )
            return

        duration_ms = int((time.monotonic() - start) * 1000)
        self._write_audit(
            event_type="plugin_command_success",
            actor_telegram_user_id=actor.telegram_user_id,
            payload={
                "plugin_name": manifest.name,
                "command": invocation.command_name,
                "run_id": run_id,
                "duration_ms": duration_ms,
            },
        )

    def _find_manifest_for_command(self, command_name: str) -> PluginManifest | None:
        discovery = self._loader.discover()
        command = command_name.casefold()
        for manifest in discovery.valid:
            commands = {item.casefold() for item in manifest.commands}
            if command in commands:
                return manifest
        return None

    @staticmethod
    def _is_role_allowed(manifest: PluginManifest, role: Role) -> bool:
        if role is Role.IGNORE:
            return False
        if not manifest.required_roles:
            return True
        return role.value in set(manifest.required_roles)

    def _load_handler(self, manifest: PluginManifest) -> Callable[[PluginCommandContext, PluginHostAPI], Awaitable[Any]]:
        plugin_dir = Path(self._loader.plugins_dir) / manifest.name
        module_path = plugin_dir / "main.py"
        if not module_path.exists():
            raise RuntimeError("plugin entrypoint main.py not found")

        module_name = f"amo_plugin_{manifest.name}"
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            raise RuntimeError("unable to load plugin module")

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        handler = getattr(module, "handle_command", None)
        if handler is None or not callable(handler):
            raise RuntimeError("plugin handle_command(context, host_api) missing")

        if not inspect.iscoroutinefunction(handler):
            raise RuntimeError("plugin handle_command must be async")

        return handler

    def _write_audit(self, *, event_type: str, actor_telegram_user_id: int, payload: dict[str, Any]) -> None:
        with self._session_factory() as session:
            session.add(
                AuditEvent(
                    actor_telegram_user_id=actor_telegram_user_id,
                    event_type=event_type,
                    payload_json=json.dumps(payload, ensure_ascii=False),
                )
            )
            session.commit()
