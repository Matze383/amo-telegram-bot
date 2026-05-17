from __future__ import annotations

import asyncio
import importlib.util
import inspect
import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from sqlalchemy.orm import sessionmaker

from amo_bot.auth.roles import Role
from amo_bot.db.models import AuditEvent
from amo_bot.db.repositories import PluginPolicyOverrideRepository, PluginRepository
from amo_bot.plugins.loader import PluginLoader
from amo_bot.plugins.manifest import PluginManifest
from amo_bot.telegram.image_media_store import TelegramImageMediaStore
from amo_bot.telegram.update_parser import TelegramAttachment
from amo_bot.plugins.policy_overrides import evaluate_effective_policy, resolve_effective_policy

logger = logging.getLogger(__name__)

SendMessageFn = Callable[[int, str], Awaitable[object]]
ReplyFn = Callable[[int, int, str, int | None], Awaitable[object]]


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
    message_thread_id: int | None = None
    attachments: tuple[TelegramAttachment, ...] = ()


@dataclass(slots=True, frozen=True)
class PluginCommandContext:
    plugin_id: str
    run_id: str
    trigger_type: str
    chat_id: int
    message_id: int
    message_thread_id: int | None
    user_id: int
    role: Role
    command_name: str
    argument: str | None
    attachments: tuple[dict[str, Any], ...] = ()
    reply_to_image: dict[str, Any] | None = None


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
        return await self._reply(chat_id, message_id, text_clean[:4000], None)


class PluginCommandExecutor:
    def __init__(
        self,
        *,
        loader: PluginLoader,
        session_factory: sessionmaker,
        send_message: SendMessageFn,
        reply: ReplyFn,
        timeout_seconds: float = 2.0,
        image_media_store: TelegramImageMediaStore | None = None,
        enable_image_attachments: bool = False,
    ) -> None:
        self._loader = loader
        self._session_factory = session_factory
        self._send_message = send_message
        self._reply = reply
        self._timeout_seconds = timeout_seconds
        self._image_media_store = image_media_store
        self._enable_image_attachments = enable_image_attachments

    async def execute(self, *, actor: CommandActor, invocation: CommandInvocation) -> None:
        manifest = self._find_manifest_for_command(invocation.command_name)
        if manifest is None:
            return

        run_id = str(uuid.uuid4())
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

        try:
            with self._session_factory() as session:
                override = PluginPolicyOverrideRepository(session).get_snapshot(plugin_name=manifest.name)
        except Exception:
            logger.exception("plugin policy override lookup failed plugin=%s", manifest.name)
            return

        effective_policy = resolve_effective_policy(manifest=manifest, override=override)
        policy_eval = evaluate_effective_policy(
            actor_role=actor.role,
            effective_policy=effective_policy,
            chat_id=invocation.chat_id,
            message_thread_id=invocation.message_thread_id,
        )
        if not policy_eval.allowed:
            self._write_audit(
                event_type="plugin_command_denied",
                actor_telegram_user_id=actor.telegram_user_id,
                payload={
                    "plugin_name": manifest.name,
                    "command": invocation.command_name,
                    "reason": policy_eval.deny_reason,
                    "run_id": run_id,
                },
            )
            return

        attachment_context = await self._build_attachment_context(invocation=invocation)
        reply_to_image = self._resolve_reply_to_image(invocation=invocation, attachments=attachment_context)

        context = PluginCommandContext(
            plugin_id=manifest.name,
            run_id=run_id,
            trigger_type="command",
            chat_id=invocation.chat_id,
            message_id=invocation.message_id,
            message_thread_id=invocation.message_thread_id,
            user_id=actor.telegram_user_id,
            role=actor.role,
            command_name=invocation.command_name,
            argument=invocation.argument,
            attachments=attachment_context,
            reply_to_image=reply_to_image,
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


    async def _build_attachment_context(self, *, invocation: CommandInvocation) -> tuple[dict[str, Any], ...]:
        if not self._enable_image_attachments or not invocation.attachments:
            return ()

        contexts: list[dict[str, Any]] = []
        for attachment in invocation.attachments:
            if attachment.type_hint not in {"image", "image_document"}:
                continue
            context: dict[str, Any] = {
                "source_kind": attachment.source_kind,
                "type_hint": attachment.type_hint,
                "file_id": attachment.file_id,
                "file_unique_id": attachment.file_unique_id,
                "width": attachment.width,
                "height": attachment.height,
                "size": attachment.size,
            }
            mime_type = getattr(attachment, "mime_type", None)
            if isinstance(mime_type, str) and mime_type:
                context["mime_type"] = mime_type
            if self._image_media_store is not None:
                try:
                    media_result = await self._image_media_store.download_image(attachment=attachment)
                except Exception:
                    media_result = None
                if media_result is not None and media_result.ok:
                    context["media_ref"] = {
                        "reason_code": media_result.reason_code,
                        "mime_type": media_result.mime_type,
                        "bytes_stored": media_result.bytes_stored,
                    }
                elif media_result is not None:
                    context["download_reason_code"] = media_result.reason_code
                    if media_result.reason_code in {"deny_attachment_size", "deny_file_size", "deny_stream_size"}:
                        context["size_limit_exceeded"] = True
            contexts.append(context)
        return tuple(contexts)

    def _resolve_reply_to_image(
        self,
        *,
        invocation: CommandInvocation,
        attachments: tuple[dict[str, Any], ...],
    ) -> dict[str, Any] | None:
        if self._normalize_command(invocation.command_name) != "analyze_image":
            return None
        if not attachments:
            return {
                "ok": False,
                "reason_code": "missing_image",
            }
        first = attachments[0]
        media_ref = first.get("media_ref") if isinstance(first, dict) else None
        if isinstance(media_ref, dict):
            return {
                "ok": True,
                "media_ref": media_ref,
                "type_hint": first.get("type_hint"),
                "width": first.get("width"),
                "height": first.get("height"),
                "size": first.get("size"),
            }

        reason_code = "invalid_image"
        if isinstance(first, dict):
            if first.get("size_limit_exceeded") is True:
                reason_code = "oversize"
            elif first.get("download_reason_code") == "deny_mime":
                reason_code = "invalid_type"
            elif isinstance(first.get("mime_type"), str) and first.get("mime_type"):
                reason_code = "invalid_type"
        return {
            "ok": False,
            "reason_code": reason_code,
        }

    def _normalize_command(self, command: str) -> str:
        normalized = command.strip().casefold()
        if not normalized:
            return ""
        return normalized[1:] if normalized.startswith("/") else normalized

    def _find_manifest_for_command(self, command_name: str) -> PluginManifest | None:
        discovery = self._loader.discover()
        command = self._normalize_command(command_name)
        for manifest in discovery.valid:
            commands = {self._normalize_command(item) for item in manifest.commands}
            if command and command in commands:
                return manifest
        return None

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
