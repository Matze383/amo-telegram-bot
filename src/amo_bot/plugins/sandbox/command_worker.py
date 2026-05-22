from __future__ import annotations

import asyncio
import importlib.util
import inspect
from dataclasses import asdict
from pathlib import Path
from typing import Any

from amo_bot.auth.roles import Role
from amo_bot.plugins.command_runtime import PluginCommandContext
from amo_bot.plugins.sandbox.command_protocol import (
    CommandError,
    CommandExecuteRequestV1,
    CommandExecuteResponseV1,
    CommandOp,
    CommandProtocolError,
)


class CommandWorkerError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _sanitize_error_message(message: str, *, fallback: str = "command execution failed") -> str:
    clean = (message or "").strip().replace("\n", " ")
    if not clean:
        clean = fallback
    clean = clean[:220]
    if "traceback" in clean.lower():
        return fallback
    return clean


def _resolve_plugin_entry(*, plugins_root: Path, plugin_entry: str) -> Path:
    if plugin_entry.startswith("/") or plugin_entry.startswith("\\"):
        raise CommandWorkerError("invalid_plugin_entry", "plugin entry not allowed")
    candidate = (plugins_root / plugin_entry).resolve()
    root_resolved = plugins_root.resolve()
    if root_resolved == candidate or root_resolved not in candidate.parents:
        raise CommandWorkerError("invalid_plugin_entry", "plugin entry not allowed")
    if candidate.name != "main.py" or not candidate.is_file():
        raise CommandWorkerError("plugin_not_found", "plugin entry not found")
    return candidate


class _RecordingHostAPI:
    def __init__(self, *, permissions: set[str], max_ops: int, max_text_len: int) -> None:
        self._permissions = permissions
        self._max_ops = max_ops
        self._max_text_len = max_text_len
        self._ops: list[CommandOp] = []

    def _require_permission(self, permission: str, operation: str) -> None:
        if permission not in self._permissions:
            raise CommandWorkerError(
                "permission_denied",
                f"operation '{operation}' requires capability '{permission}'",
            )

    async def send_message(self, chat_id: int, text: str) -> dict[str, object]:
        self._require_permission("send_message", "send_message")
        if not isinstance(chat_id, int):
            raise ValueError("chat_id must be int")
        text_clean = (text or "").strip()
        self._append_op(CommandOp(op="send_message", chat_id=chat_id, text=text_clean))
        return {"ok": True}

    async def reply(self, chat_id: int, message_id: int, text: str) -> dict[str, object]:
        self._require_permission("send_message", "reply")
        if not isinstance(chat_id, int) or not isinstance(message_id, int):
            raise ValueError("chat_id and message_id must be int")
        text_clean = (text or "").strip()
        self._append_op(CommandOp(op="reply", chat_id=chat_id, message_id=message_id, text=text_clean))
        return {"ok": True}

    async def send_photo(
        self,
        chat_id: int,
        file_path: str,
        caption: str = "",
        *,
        message_thread_id: int | None = None,
        mime_type: str | None = None,
    ) -> dict[str, object]:
        self._require_permission("send_message", "send_photo")
        if not isinstance(chat_id, int):
            raise ValueError("chat_id must be int")
        self._append_op(
            CommandOp(
                op="send_photo",
                chat_id=chat_id,
                text=(caption or "").strip(),
                message_thread_id=message_thread_id,
                file_path=file_path,
                mime_type=mime_type,
            )
        )
        return {"ok": True}

    async def send_document(
        self,
        chat_id: int,
        file_path: str,
        caption: str = "",
        *,
        message_thread_id: int | None = None,
        mime_type: str | None = None,
    ) -> dict[str, object]:
        self._require_permission("send_message", "send_document")
        if not isinstance(chat_id, int):
            raise ValueError("chat_id must be int")
        self._append_op(
            CommandOp(
                op="send_document",
                chat_id=chat_id,
                text=(caption or "").strip(),
                message_thread_id=message_thread_id,
                file_path=file_path,
                mime_type=mime_type,
            )
        )
        return {"ok": True}

    def _append_op(self, op: CommandOp) -> None:
        if len(self._ops) >= self._max_ops:
            raise CommandWorkerError("limits_exceeded", "maximum operation count exceeded")
        if not op.text:
            raise ValueError("text must not be empty")
        if len(op.text) > self._max_text_len:
            raise CommandWorkerError("limits_exceeded", "maximum text length exceeded")
        self._ops.append(op)

    @property
    def ops(self) -> tuple[CommandOp, ...]:
        return tuple(self._ops)


def _build_context(request: CommandExecuteRequestV1) -> PluginCommandContext:
    return PluginCommandContext(
        plugin_id=request.plugin_id or "",
        run_id=request.context.run_id,
        trigger_type=request.context.trigger_type,
        chat_id=request.context.chat_id,
        message_id=request.context.message_id,
        message_thread_id=request.context.message_thread_id,
        user_id=request.context.user_id,
        role=Role(request.context.role),
        command_name=request.command_name,
        argument=request.argument,
        attachments=tuple(dict(item) for item in request.context.attachments),
        reply_to_image=request.context.reply_to_image,
    )


def _load_handler(module_path: Path):
    module_name = f"amo_plugin_worker_{module_path.parent.name}"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise CommandWorkerError("plugin_load_error", "unable to load plugin module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    handler = getattr(module, "handle_command", None)
    if handler is None or not callable(handler):
        raise CommandWorkerError("plugin_load_error", "plugin handle_command(context, host_api) missing")
    if not inspect.iscoroutinefunction(handler):
        raise CommandWorkerError("plugin_load_error", "plugin handle_command must be async")
    return handler


async def execute_command_request(
    request_payload: dict[str, Any] | CommandExecuteRequestV1,
    *,
    plugins_root: str | Path,
) -> dict[str, Any]:
    try:
        request = (
            request_payload
            if isinstance(request_payload, CommandExecuteRequestV1)
            else CommandExecuteRequestV1.from_dict(request_payload)
        )
    except CommandProtocolError:
        return {
            "action": "command.execute.v1",
            "request_id": request_payload.get("request_id", "unknown") if isinstance(request_payload, dict) else "unknown",
            "ok": False,
            "ops": [],
            "error": {"code": "invalid_request", "message": "invalid command request"},
            "metrics": None,
        }

    try:
        if request.plugin_entry is None:
            raise CommandWorkerError("invalid_plugin_entry", "plugin entry not allowed")
        module_path = _resolve_plugin_entry(plugins_root=Path(plugins_root), plugin_entry=request.plugin_entry)
        handler = _load_handler(module_path)
        context = _build_context(request)
        host_api = _RecordingHostAPI(
            permissions=set(request.permissions),
            max_ops=request.limits.max_ops,
            max_text_len=request.limits.max_text_len,
        )
        await handler(context, host_api)
        response = CommandExecuteResponseV1(
            action="command.execute.v1",
            request_id=request.request_id,
            ok=True,
            ops=host_api.ops,
            error=None,
            metrics=None,
        )
        return response.to_dict()
    except CommandWorkerError as exc:
        response = CommandExecuteResponseV1(
            action="command.execute.v1",
            request_id=request.request_id,
            ok=False,
            ops=(),
            error=CommandError(code=exc.code, message=_sanitize_error_message(exc.message)),
            metrics=None,
        )
        return response.to_dict()
    except Exception as exc:
        response = CommandExecuteResponseV1(
            action="command.execute.v1",
            request_id=request.request_id,
            ok=False,
            ops=(),
            error=CommandError(code="runtime_error", message=_sanitize_error_message(str(exc))),
            metrics=None,
        )
        return response.to_dict()


def execute_command_request_sync(
    request_payload: dict[str, Any] | CommandExecuteRequestV1,
    *,
    plugins_root: str | Path,
) -> dict[str, Any]:
    return asyncio.run(execute_command_request(request_payload, plugins_root=plugins_root))
