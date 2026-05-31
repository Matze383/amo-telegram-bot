from __future__ import annotations

import asyncio
import importlib.util
import inspect
import json
import logging
import mimetypes
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from amo_bot.plugins.sandbox.command_protocol import CommandError, CommandExecuteRequestV1, CommandOp

from sqlalchemy.orm import sessionmaker

from amo_bot.auth.roles import Role
from amo_bot.core.logging import duration_timer, log_event, masked_id
from amo_bot.db.models import AuditEvent
from amo_bot.db.repositories import PluginPolicyOverrideRepository, PluginRepository
from amo_bot.plugins.loader import PluginLoader
from amo_bot.plugins.manifest import PluginManifest
from amo_bot.telegram.image_media_store import TelegramImageMediaStore
from amo_bot.telegram.update_parser import TelegramAttachment
from amo_bot.plugins.policy_overrides import evaluate_effective_policy, resolve_effective_policy
from amo_bot.ai.image_analyze_orchestrator import ImageAnalyzeOrchestrator, ImageAnalyzeOrchestratorRequest

logger = logging.getLogger(__name__)
_COMPONENT = "plugin.runtime"

SendMessageFn = Callable[[int, str], Awaitable[object]]
ReplyFn = Callable[[int, int, str, int | None], Awaitable[object]]
ReplyMarkupFn = Callable[[int, int, str, dict[str, Any], int | None], Awaitable[object]]
SendPhotoFn = Callable[[int, str, str, int | None], Awaitable[object]]
SendDocumentFn = Callable[[int, str, str, int | None, str | None], Awaitable[object]]
AnswerCallbackFn = Callable[[str, str | None], Awaitable[object]]


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

    callback_data: str | None = None
    callback_query_id: str | None = None

    @property
    def thread_id(self) -> int | None:
        return self.message_thread_id


class PluginCapabilityError(RuntimeError):
    pass


class PluginHostAPI:
    def __init__(
        self,
        *,
        send_message: SendMessageFn,
        reply: ReplyFn,
        answer_callback: AnswerCallbackFn | None = None,
        required_permissions: set[str] | None = None,
    ) -> None:
        self._send_message = send_message
        self._reply = reply
        self._answer_callback = answer_callback
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

    async def reply(self, chat_id: int, message_id: int, text: str | dict[str, Any]) -> object:
        self._require_permission("send_message", "reply")
        if not isinstance(chat_id, int) or not isinstance(message_id, int):
            raise ValueError("chat_id and message_id must be int")

        text_payload: str | None = None
        if isinstance(text, dict):
            text_payload = text.get("text")
            if not isinstance(text_payload, str) or not text_payload.strip():
                alt = text.get("message")
                text_payload = alt if isinstance(alt, str) else None
        elif isinstance(text, str):
            text_payload = text

        text_clean = (text_payload or "").strip()
        if not text_clean:
            raise ValueError("text must not be empty")
        return await self._reply(chat_id, message_id, text_clean[:4000], None)

    async def answer_callback_query(self, callback_query_id: str, text: str | None = None) -> object:
        self._require_permission("send_message", "answer_callback_query")
        if self._answer_callback is None:
            raise RuntimeError("answer_callback transport not configured")
        callback_id_clean = (callback_query_id or "").strip()
        if not callback_id_clean:
            raise ValueError("callback_query_id must not be empty")
        text_clean = (text or "").strip() or None
        if isinstance(text_clean, str):
            text_clean = text_clean[:200]
        return await self._answer_callback(callback_id_clean, text_clean)


class PluginCommandExecutor:
    def __init__(
        self,
        *,
        loader: PluginLoader,
        session_factory: sessionmaker,
        send_message: SendMessageFn,
        reply: ReplyFn,
        send_photo: SendPhotoFn | None = None,
        send_document: SendDocumentFn | None = None,
        reply_markup: ReplyMarkupFn | None = None,
        timeout_seconds: float = 2.0,
        image_media_store: TelegramImageMediaStore | None = None,
        enable_image_attachments: bool = False,
        image_analyze_provider: Any | None = None,
    ) -> None:
        self._loader = loader
        self._session_factory = session_factory
        self._send_message = send_message
        self._reply = reply
        self._send_photo = send_photo
        self._send_document = send_document
        self._reply_markup = reply_markup
        self._timeout_seconds = timeout_seconds
        self._image_media_store = image_media_store
        self._enable_image_attachments = enable_image_attachments
        self._image_analyze_orchestrator = ImageAnalyzeOrchestrator(
            provider=image_analyze_provider,
            session_factory=session_factory,
            role_daily_quota={
                Role.IGNORE: 0,
                Role.OWNER: None,
                Role.ADMIN: None,
                Role.VIP: 5,
                Role.NORMAL: 2,
            },
            max_image_bytes=8 * 1024 * 1024,
            allowed_mime_types={"image/jpeg", "image/png", "image/webp", "image/gif", "image/*"},
        )

    async def analyze_image_automatically(self, *, actor: CommandActor, invocation: CommandInvocation) -> bool:
        if not invocation.attachments:
            log_event(
                logger, logging.INFO,
                event="auto_image.skipped",
                component=_COMPONENT,
                chat_id=invocation.chat_id,
                message_id=invocation.message_id,
                message_thread_id=invocation.message_thread_id,
                user_id=actor.telegram_user_id,
                command="auto_image",
                extra={"reason": "no_attachments", "role": actor.role.value},
            )
            return False

        attachment_context = await self._build_attachment_context(invocation=invocation)
        reply_to_image = self._resolve_image_from_attachments(attachments=attachment_context)
        log_event(
            logger, logging.INFO,
            event="auto_image.gate_input",
            component=_COMPONENT,
            chat_id=invocation.chat_id,
            message_id=invocation.message_id,
            message_thread_id=invocation.message_thread_id,
            user_id=actor.telegram_user_id,
            command="auto_image",
            extra={
                "attachment_count": len(invocation.attachments),
                "context_count": len(attachment_context),
                "image_ok": bool(reply_to_image and reply_to_image.get("ok") is True),
                "reason_code": (reply_to_image or {}).get("reason_code"),
                "role": actor.role.value,
            },
        )
        log_event(
            logger, logging.INFO,
            event="auto_image.decision",
            component=_COMPONENT,
            chat_id=invocation.chat_id,
            message_id=invocation.message_id,
            message_thread_id=invocation.message_thread_id,
            user_id=actor.telegram_user_id,
            command="auto_image",
            extra={
                "decision": "invoked",
                "attachment_count": len(invocation.attachments),
                "role": actor.role.value,
            },
        )
        gate_result = await self._image_analyze_orchestrator.evaluate_and_maybe_invoke_provider_async(
            request=ImageAnalyzeOrchestratorRequest(
                user_id=actor.telegram_user_id,
                role=actor.role,
                chat_id=invocation.chat_id,
                message_thread_id=invocation.message_thread_id,
                command=invocation.command_name,
                provider="fake",
                reply_to_image=reply_to_image,
                image_ok=bool(reply_to_image and reply_to_image.get("ok") is True),
                image_reason_code=(reply_to_image or {}).get("reason_code"),
                prompt=invocation.argument or "",
            ),
        )
        log_event(
            logger, logging.INFO,
            event="auto_image.gate_result",
            component=_COMPONENT,
            chat_id=invocation.chat_id,
            message_id=invocation.message_id,
            message_thread_id=invocation.message_thread_id,
            user_id=actor.telegram_user_id,
            command="auto_image",
            extra={
                "allowed": gate_result.allowed,
                "outcome": gate_result.outcome,
                "provider_called": gate_result.provider_called,
                "count": gate_result.count,
            },
        )
        if not gate_result.allowed or gate_result.provider_result is None:
            if gate_result.outcome == "topic_disabled":
                await self._reply(
                    invocation.chat_id,
                    invocation.message_id,
                    "Bild empfangen, aber Bildanalyse ist in diesem Topic aktuell nicht aktiviert.",
                    invocation.message_thread_id,
                )
                return True
            if gate_result.outcome in {"role_disabled", "quota_exceeded", "invalid_image", "missing_image", "oversize", "invalid_type"}:
                await self._reply(
                    invocation.chat_id,
                    invocation.message_id,
                    "Bild empfangen, aber ich kann es aktuell nicht analysieren.",
                    invocation.message_thread_id,
                )
                return True
            if gate_result.outcome in {"provider_timeout", "provider_error", "provider_empty"}:
                await self._reply(
                    invocation.chat_id,
                    invocation.message_id,
                    "Bild empfangen, aber Bildanalyse ist aktuell nicht verfügbar oder nicht konfiguriert.",
                    invocation.message_thread_id,
                )
                return True
            return bool(reply_to_image and reply_to_image.get("ok") is True)

        await self._reply(
            invocation.chat_id,
            invocation.message_id,
            gate_result.provider_result.summary,
            invocation.message_thread_id,
        )
        return True

    async def execute(self, *, actor: CommandActor, invocation: CommandInvocation) -> bool:
        manifest = self._find_manifest_for_command(invocation.command_name)
        if manifest is None:
            return False

        run_id = str(uuid.uuid4())
        try:
            with self._session_factory() as session:
                repo = PluginRepository(session)
                status = repo.get_status(manifest.name)
        except Exception:
            logger.exception("plugin status lookup failed plugin=%s", manifest.name)
            return True

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
            log_event(
                logger, logging.INFO,
                event="plugin.command.skipped",
                component=_COMPONENT,
                chat_id=invocation.chat_id,
                message_id=invocation.message_id,
                message_thread_id=invocation.message_thread_id,
                user_id=actor.telegram_user_id,
                command=invocation.command_name,
                extra={"plugin_id": manifest.name, "run_id": run_id, "outcome": "plugin_disabled"},
            )
            return True

        try:
            with self._session_factory() as session:
                override = PluginPolicyOverrideRepository(session).get_snapshot(plugin_name=manifest.name)
        except Exception:
            logger.exception("plugin policy override lookup failed plugin=%s", manifest.name)
            return True

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
            log_event(
                logger, logging.INFO,
                event="plugin.command.denied",
                component=_COMPONENT,
                chat_id=invocation.chat_id,
                message_id=invocation.message_id,
                message_thread_id=invocation.message_thread_id,
                user_id=actor.telegram_user_id,
                command=invocation.command_name,
                extra={"plugin_id": manifest.name, "run_id": run_id, "outcome": policy_eval.deny_reason},
            )
            return True

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

        if self._normalize_command(invocation.command_name) == "analyze_image":
            gate_result = await self._image_analyze_orchestrator.evaluate_and_maybe_invoke_provider_async(
                request=ImageAnalyzeOrchestratorRequest(
                    user_id=actor.telegram_user_id,
                    role=actor.role,
                    chat_id=invocation.chat_id,
                    message_thread_id=invocation.message_thread_id,
                    command=invocation.command_name,
                    provider="fake",
                    reply_to_image=reply_to_image,
                    image_ok=bool(reply_to_image and reply_to_image.get("ok") is True),
                    image_reason_code=(reply_to_image or {}).get("reason_code"),
                    prompt=invocation.argument or "",
                ),
            )
            if not gate_result.allowed:
                self._write_audit(
                    event_type="plugin_command_denied",
                    actor_telegram_user_id=actor.telegram_user_id,
                    payload={
                        "plugin_name": manifest.name,
                        "command": invocation.command_name,
                        "reason": gate_result.outcome,
                        "run_id": run_id,
                    },
                )
                log_event(
                    logger, logging.INFO,
                    event="plugin.command.denied",
                    component=_COMPONENT,
                    chat_id=invocation.chat_id,
                    message_id=invocation.message_id,
                    message_thread_id=invocation.message_thread_id,
                    user_id=actor.telegram_user_id,
                    command=invocation.command_name,
                    extra={"plugin_id": manifest.name, "run_id": run_id, "outcome": gate_result.outcome},
                )
                return True

        self._write_audit(
            event_type="plugin_command_start",
            actor_telegram_user_id=actor.telegram_user_id,
            payload={
                "plugin_name": manifest.name,
                "command": invocation.command_name,
                "run_id": run_id,
            },
        )
        log_event(
            logger, logging.INFO,
            event="plugin.command.start",
            component=_COMPONENT,
            chat_id=invocation.chat_id,
            message_id=invocation.message_id,
            message_thread_id=invocation.message_thread_id,
            user_id=actor.telegram_user_id,
            command=invocation.command_name,
            extra={"plugin_id": manifest.name, "run_id": run_id},
        )

        timing: dict[str, Any] = {}
        with duration_timer(timing):
            try:
                await self._execute_via_sandbox(manifest=manifest, context=context)
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
                log_event(
                    logger, logging.WARNING,
                    event="plugin.command.timeout",
                    component=_COMPONENT,
                    chat_id=invocation.chat_id,
                    message_id=invocation.message_id,
                    message_thread_id=invocation.message_thread_id,
                    user_id=actor.telegram_user_id,
                    command=invocation.command_name,
                    extra={
                        "plugin_id": manifest.name,
                        "run_id": run_id,
                        "timeout_seconds": self._timeout_seconds,
                        "outcome": "timeout",
                    },
                )
                return True
            except Exception as exc:
                logger.exception("plugin command failed plugin=%s command=%s", manifest.name, invocation.command_name)
                payload = {
                    "plugin_name": manifest.name,
                    "command": invocation.command_name,
                    "run_id": run_id,
                    "error": str(exc),
                }
                sandbox_error_code = getattr(exc, "sandbox_error_code", None)
                if isinstance(sandbox_error_code, str) and sandbox_error_code.strip():
                    payload["error_code"] = sandbox_error_code.strip()
                self._write_audit(
                    event_type="plugin_command_error",
                    actor_telegram_user_id=actor.telegram_user_id,
                    payload=payload,
                )
                log_event(
                    logger, logging.ERROR,
                    event="plugin.command.error",
                    component=_COMPONENT,
                    chat_id=invocation.chat_id,
                    message_id=invocation.message_id,
                    message_thread_id=invocation.message_thread_id,
                    user_id=actor.telegram_user_id,
                    command=invocation.command_name,
                    extra={
                        "plugin_id": manifest.name,
                        "run_id": run_id,
                        "error": str(exc),
                        "error_code": sandbox_error_code if isinstance(sandbox_error_code, str) else None,
                        "outcome": "error",
                    },
                )
                return True

        duration_ms = timing.get("duration_ms", 0)
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
        log_event(
            logger, logging.INFO,
            event="plugin.command.success",
            component=_COMPONENT,
            chat_id=invocation.chat_id,
            message_id=invocation.message_id,
            message_thread_id=invocation.message_thread_id,
            user_id=actor.telegram_user_id,
            command=invocation.command_name,
            duration_ms=duration_ms,
            extra={"plugin_id": manifest.name, "run_id": run_id, "outcome": "success"},
        )
        return True


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
                    logger.exception(
                        "auto_image attachment download failed chat_id=%s message_thread_id=%s message_id=%s source_kind=%s type_hint=%s",
                        invocation.chat_id,
                        invocation.message_thread_id,
                        invocation.message_id,
                        attachment.source_kind,
                        attachment.type_hint,
                    )
                    media_result = None
                if media_result is not None and media_result.ok:
                    context["media_ref"] = {
                        "reason_code": media_result.reason_code,
                        "mime_type": media_result.mime_type,
                        "bytes_stored": media_result.bytes_stored,
                    }
                    file_path = getattr(media_result, "file_path", None)
                    if isinstance(file_path, str) and file_path:
                        context["_file_path"] = file_path
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
        return self._resolve_image_from_attachments(attachments=attachments)

    def _resolve_image_from_attachments(self, *, attachments: tuple[dict[str, Any], ...]) -> dict[str, Any]:
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
                "file_unique_id": first.get("file_unique_id"),
                "_file_path": first.get("_file_path"),
                "width": first.get("width"),
                "height": first.get("height"),
                "size": first.get("size"),
            }

        reason_code = "invalid_image"
        if isinstance(first, dict):
            if first.get("size_limit_exceeded") is True:
                reason_code = "oversize"
            elif str(first.get("download_reason_code") or "").startswith("deny_mime"):
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

    async def execute_callback(
        self,
        *,
        actor: CommandActor,
        callback_data: str,
        callback_query_id: str,
        chat_id: int,
        user_id: int,
        message_id: int | None = None,
        message_thread_id: int | None = None,
        answer_callback: AnswerCallbackFn | None = None,
    ) -> bool:
        if not callback_data.startswith("yt_rss:"):
            return False

        manifest = self._find_manifest_for_callback(callback_data)
        if manifest is None:
            return False

        run_id = str(uuid.uuid4())
        context_payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_thread_id": message_thread_id,
            "message_id": message_id,
            "user_id": user_id,
            "role": actor.role,
            "callback_data": callback_data,
            "callback_query_id": callback_query_id,
            "trigger_type": "callback",
            "run_id": run_id,
            "plugin_id": manifest.name,
            "command_name": "callback",
            "argument": None,
            "attachments": (),
            "reply_to_image": None,
        }

        plugin_dir = Path(self._loader.plugins_dir) / manifest.name
        module_path = plugin_dir / "main.py"
        if not module_path.exists():
            logger.warning("plugin callback entrypoint missing plugin=%s", manifest.name)
            return True

        module_name = f"amo_plugin_{manifest.name}_callback"
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            logger.warning("plugin callback load failed plugin=%s", manifest.name)
            return True

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        callback_handler = getattr(module, "handle_callback", None)
        if callback_handler is None or not callable(callback_handler):
            logger.info("plugin callback missing handler plugin=%s", manifest.name)
            return True
        if not inspect.iscoroutinefunction(callback_handler):
            logger.warning("plugin callback handler must be async plugin=%s", manifest.name)
            return True

        host_api = PluginHostAPI(
            send_message=self._send_message,
            reply=self._reply,
            answer_callback=answer_callback,
            required_permissions=set(manifest.required_permissions),
        )

        callback_context = PluginCommandContext(**context_payload)

        try:
            handled = await asyncio.wait_for(callback_handler(callback_context, host_api), timeout=self._timeout_seconds)
        except asyncio.TimeoutError:
            logger.warning("plugin callback timeout plugin=%s callback=%s", manifest.name, callback_data)
            log_event(
                logger, logging.WARNING,
                event="plugin.callback.timeout",
                component=_COMPONENT,
                extra={"plugin_id": manifest.name, "run_id": run_id},
            )
            return True
        except Exception:
            logger.exception("plugin callback failed plugin=%s callback=%s", manifest.name, callback_data)
            log_event(
                logger, logging.ERROR,
                event="plugin.callback.error",
                component=_COMPONENT,
                extra={"plugin_id": manifest.name, "run_id": run_id},
            )
            return True

        if handled is None:
            return True
        return bool(handled)

    def _find_manifest_for_command(self, command_name: str) -> PluginManifest | None:
        discovery = self._loader.discover()
        command = self._normalize_command(command_name)
        for manifest in discovery.valid:
            commands = {self._normalize_command(item) for item in manifest.commands}
            if command and command in commands:
                return manifest
        return None

    def _find_manifest_for_callback(self, callback_data: str) -> PluginManifest | None:
        if not callback_data.startswith("yt_rss:"):
            return None
        discovery = self._loader.discover()
        for manifest in discovery.valid:
            if manifest.name == "yt_rss":
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

    async def _execute_via_sandbox(self, *, manifest: PluginManifest, context: PluginCommandContext) -> None:
        request = CommandExecuteRequestV1.from_dict(
            {
                "action": "command.execute.v1",
                "request_id": context.run_id,
                "plugin_id": None,
                "plugin_entry": f"{manifest.name}/main.py",
                "command_name": context.command_name,
                "argument": context.argument,
                "context": {
                    "chat_id": context.chat_id,
                    "message_id": context.message_id,
                    "message_thread_id": context.message_thread_id,
                    "user_id": context.user_id,
                    "role": context.role.value,
                    "trigger_type": context.trigger_type,
                    "run_id": context.run_id,
                    "attachments": list(context.attachments),
                    "reply_to_image": context.reply_to_image,
                },
                "permissions": list(manifest.required_permissions),
                "limits": {
                    "timeout_ms": max(1, int(self._timeout_seconds * 1000)),
                    "max_ops": 16,
                    "max_text_len": 4000,
                },
            }
        )
        from amo_bot.plugins.sandbox.command_worker import execute_command_request

        response = await asyncio.wait_for(
            execute_command_request(request, plugins_root=self._loader.plugins_dir),
            timeout=self._timeout_seconds,
        )
        if not response.get("ok", False):
            error_obj = response.get("error")
            if isinstance(error_obj, dict):
                command_error = CommandError(
                    code=str(error_obj.get("code") or "runtime_error"),
                    message=str(error_obj.get("message") or "command execution failed"),
                )
            else:
                command_error = CommandError(code="runtime_error", message="command execution failed")
            sandbox_exc = RuntimeError(command_error.message)
            sandbox_exc.__dict__["sandbox_error_code"] = command_error.code
            raise sandbox_exc

        for op_payload in response.get("ops", []):
            op = CommandOp.from_dict(op_payload, max_text_len=request.limits.max_text_len)
            if op.op == "send_message":
                await self._send_message(op.chat_id, op.text)
            elif op.op == "reply":
                if op.message_id is None:
                    raise RuntimeError("sandbox reply operation missing message_id")
                if op.reply_markup is not None and self._reply_markup is not None:
                    await self._reply_markup(
                        op.chat_id,
                        op.text,
                        op.reply_markup,
                        op.message_thread_id,
                    )
                else:
                    await self._reply(
                        op.chat_id,
                        op.message_id,
                        op.text,
                        op.message_thread_id,
                    )
            elif op.op in {"send_photo", "send_document"}:
                if op.chat_id is None or op.file_path is None:
                    raise RuntimeError("sandbox file send operation missing fields")
                safe_path = self._validate_send_file_path(op.file_path)
                selected = self._select_send_method(op=safe_path, forced_op=op.op, mime_type=op.mime_type)
                thread_id = op.message_thread_id
                if selected == "photo":
                    if self._send_photo is None:
                        raise RuntimeError("send_photo transport not configured")
                    await self._send_photo(op.chat_id, str(safe_path), op.text, thread_id)
                else:
                    if self._send_document is None:
                        raise RuntimeError("send_document transport not configured")
                    await self._send_document(op.chat_id, str(safe_path), op.text, thread_id, op.mime_type)

    def _validate_send_file_path(self, file_path: str) -> Path:
        candidate = Path(file_path)
        if not candidate.is_absolute():
            raise RuntimeError("invalid file path")
        resolved = candidate.resolve(strict=True)
        if not resolved.is_file():
            raise RuntimeError("invalid file path")
        return resolved

    def _select_send_method(self, *, op: Path, forced_op: str, mime_type: str | None) -> str:
        if forced_op == "send_photo":
            return "photo"
        normalized = (mime_type or mimetypes.guess_type(op.name)[0] or "").lower()
        if normalized.startswith("image/"):
            return "photo"
        return "document"

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
