from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_ACTION = "command.execute.v1"
_MAX_PLUGIN_ENTRY_LEN = 200
_MAX_COMMAND_NAME_LEN = 64
_MAX_ARGUMENT_LEN = 2000
_MAX_ATTACHMENTS = 10
_MAX_ATTACHMENT_URL_LEN = 2000
_MAX_ERROR_CODE_LEN = 64
_MAX_ERROR_MESSAGE_LEN = 240
_ALLOWED_HOST_OPS = frozenset({"send_message", "reply"})


class CommandProtocolError(ValueError):
    pass


def _require_non_empty_str(value: object, field: str, *, max_len: int | None = None) -> str:
    if not isinstance(value, str):
        raise CommandProtocolError(field)
    clean = value.strip()
    if not clean:
        raise CommandProtocolError(field)
    if max_len is not None and len(clean) > max_len:
        raise CommandProtocolError(field)
    return clean


def _require_int(value: object, field: str, *, min_value: int | None = None) -> int:
    if not isinstance(value, int):
        raise CommandProtocolError(field)
    if min_value is not None and value < min_value:
        raise CommandProtocolError(field)
    return value


def _validate_plugin_entry(value: object) -> str:
    entry = _require_non_empty_str(value, "plugin_entry", max_len=_MAX_PLUGIN_ENTRY_LEN)
    lowered = entry.lower()
    if entry.startswith("/") or entry.startswith("\\"):
        raise CommandProtocolError("plugin_entry")
    if ":" in entry.split("/", 1)[0]:
        raise CommandProtocolError("plugin_entry")
    if ".." in entry or lowered.startswith("~"):
        raise CommandProtocolError("plugin_entry")
    return entry


@dataclass(slots=True, frozen=True)
class CommandLimits:
    timeout_ms: int
    max_ops: int
    max_text_len: int

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CommandLimits":
        return cls(
            timeout_ms=_require_int(data.get("timeout_ms"), "limits.timeout_ms", min_value=1),
            max_ops=_require_int(data.get("max_ops"), "limits.max_ops", min_value=1),
            max_text_len=_require_int(data.get("max_text_len"), "limits.max_text_len", min_value=1),
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "timeout_ms": self.timeout_ms,
            "max_ops": self.max_ops,
            "max_text_len": self.max_text_len,
        }


@dataclass(slots=True, frozen=True)
class CommandContext:
    chat_id: int
    message_id: int
    message_thread_id: int | None
    user_id: int
    role: str
    trigger_type: str
    run_id: str
    attachments: tuple[dict[str, Any], ...]
    reply_to_image: dict[str, Any] | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CommandContext":
        attachments_raw = data.get("attachments", ())
        if not isinstance(attachments_raw, list):
            raise CommandProtocolError("context.attachments")
        if len(attachments_raw) > _MAX_ATTACHMENTS:
            raise CommandProtocolError("context.attachments")

        attachments: list[dict[str, Any]] = []
        for item in attachments_raw:
            if not isinstance(item, dict):
                raise CommandProtocolError("context.attachments")
            if "url" in item and item["url"] is not None:
                _require_non_empty_str(item["url"], "context.attachments.url", max_len=_MAX_ATTACHMENT_URL_LEN)
            attachments.append(item)

        thread_id_raw = data.get("message_thread_id")
        thread_id = None
        if thread_id_raw is not None:
            thread_id = _require_int(thread_id_raw, "context.message_thread_id", min_value=1)

        reply_to_image = data.get("reply_to_image")
        if reply_to_image is not None and not isinstance(reply_to_image, dict):
            raise CommandProtocolError("context.reply_to_image")

        return cls(
            chat_id=_require_int(data.get("chat_id"), "context.chat_id"),
            message_id=_require_int(data.get("message_id"), "context.message_id", min_value=1),
            message_thread_id=thread_id,
            user_id=_require_int(data.get("user_id"), "context.user_id", min_value=1),
            role=_require_non_empty_str(data.get("role"), "context.role", max_len=64),
            trigger_type=_require_non_empty_str(data.get("trigger_type"), "context.trigger_type", max_len=64),
            run_id=_require_non_empty_str(data.get("run_id"), "context.run_id", max_len=128),
            attachments=tuple(attachments),
            reply_to_image=reply_to_image,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "chat_id": self.chat_id,
            "message_id": self.message_id,
            "message_thread_id": self.message_thread_id,
            "user_id": self.user_id,
            "role": self.role,
            "trigger_type": self.trigger_type,
            "run_id": self.run_id,
            "attachments": [dict(item) for item in self.attachments],
            "reply_to_image": self.reply_to_image,
        }


@dataclass(slots=True, frozen=True)
class CommandExecuteRequestV1:
    action: str
    request_id: str
    plugin_id: str | None
    plugin_entry: str | None
    command_name: str
    argument: str | None
    context: CommandContext
    permissions: tuple[str, ...]
    limits: CommandLimits

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CommandExecuteRequestV1":
        action = _require_non_empty_str(data.get("action"), "action")
        if action != _ACTION:
            raise CommandProtocolError("action")

        plugin_id_raw = data.get("plugin_id")
        plugin_entry_raw = data.get("plugin_entry")

        plugin_id = None
        if plugin_id_raw is not None:
            plugin_id = _require_non_empty_str(plugin_id_raw, "plugin_id", max_len=128)

        plugin_entry = None
        if plugin_entry_raw is not None:
            plugin_entry = _validate_plugin_entry(plugin_entry_raw)

        if bool(plugin_id) == bool(plugin_entry):
            raise CommandProtocolError("plugin_locator")

        command_name = _require_non_empty_str(data.get("command_name"), "command_name", max_len=_MAX_COMMAND_NAME_LEN)

        argument_raw = data.get("argument")
        argument = None
        if argument_raw is not None:
            argument = _require_non_empty_str(argument_raw, "argument", max_len=_MAX_ARGUMENT_LEN)

        permissions_raw = data.get("permissions", [])
        if not isinstance(permissions_raw, list):
            raise CommandProtocolError("permissions")
        permissions = tuple(_require_non_empty_str(item, "permissions.item", max_len=64) for item in permissions_raw)

        context_raw = data.get("context")
        if not isinstance(context_raw, dict):
            raise CommandProtocolError("context")

        limits_raw = data.get("limits")
        if not isinstance(limits_raw, dict):
            raise CommandProtocolError("limits")

        return cls(
            action=action,
            request_id=_require_non_empty_str(data.get("request_id"), "request_id", max_len=128),
            plugin_id=plugin_id,
            plugin_entry=plugin_entry,
            command_name=command_name,
            argument=argument,
            context=CommandContext.from_dict(context_raw),
            permissions=permissions,
            limits=CommandLimits.from_dict(limits_raw),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "request_id": self.request_id,
            "plugin_id": self.plugin_id,
            "plugin_entry": self.plugin_entry,
            "command_name": self.command_name,
            "argument": self.argument,
            "context": self.context.to_dict(),
            "permissions": list(self.permissions),
            "limits": self.limits.to_dict(),
        }


@dataclass(slots=True, frozen=True)
class CommandOp:
    op: str
    text: str
    chat_id: int | None = None
    message_id: int | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, max_text_len: int) -> "CommandOp":
        op = _require_non_empty_str(data.get("op"), "ops.op", max_len=32)
        if op not in _ALLOWED_HOST_OPS:
            raise CommandProtocolError("ops.op")

        text = _require_non_empty_str(data.get("text"), "ops.text")
        if len(text) > max_text_len:
            raise CommandProtocolError("ops.text")

        chat_id = data.get("chat_id")
        if chat_id is not None:
            chat_id = _require_int(chat_id, "ops.chat_id")

        message_id = data.get("message_id")
        if message_id is not None:
            message_id = _require_int(message_id, "ops.message_id", min_value=1)

        return cls(op=op, text=text, chat_id=chat_id, message_id=message_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "op": self.op,
            "text": self.text,
            "chat_id": self.chat_id,
            "message_id": self.message_id,
        }


@dataclass(slots=True, frozen=True)
class CommandError:
    code: str
    message: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CommandError":
        if set(data.keys()) - {"code", "message"}:
            raise CommandProtocolError("error.extra")
        message = _require_non_empty_str(data.get("message"), "error.message", max_len=_MAX_ERROR_MESSAGE_LEN)
        if "traceback" in message.lower():
            raise CommandProtocolError("error.message")
        return cls(
            code=_require_non_empty_str(data.get("code"), "error.code", max_len=_MAX_ERROR_CODE_LEN),
            message=message,
        )

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(slots=True, frozen=True)
class CommandExecuteResponseV1:
    action: str
    request_id: str
    ok: bool
    ops: tuple[CommandOp, ...] = ()
    error: CommandError | None = None
    metrics: dict[str, Any] | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, limits: CommandLimits) -> "CommandExecuteResponseV1":
        action = _require_non_empty_str(data.get("action"), "action")
        if action != _ACTION:
            raise CommandProtocolError("action")

        request_id = _require_non_empty_str(data.get("request_id"), "request_id", max_len=128)
        ok = data.get("ok")
        if not isinstance(ok, bool):
            raise CommandProtocolError("ok")

        metrics = data.get("metrics")
        if metrics is not None and not isinstance(metrics, dict):
            raise CommandProtocolError("metrics")

        if ok:
            ops_raw = data.get("ops")
            if not isinstance(ops_raw, list):
                raise CommandProtocolError("ops")
            if len(ops_raw) > limits.max_ops:
                raise CommandProtocolError("ops")
            ops = tuple(CommandOp.from_dict(item, max_text_len=limits.max_text_len) for item in ops_raw)
            if data.get("error") is not None:
                raise CommandProtocolError("error_for_ok")
            return cls(action=action, request_id=request_id, ok=True, ops=ops, error=None, metrics=metrics)

        error_raw = data.get("error")
        if not isinstance(error_raw, dict):
            raise CommandProtocolError("error")
        if data.get("ops") not in (None, []):
            raise CommandProtocolError("ops_for_error")
        return cls(
            action=action,
            request_id=request_id,
            ok=False,
            ops=(),
            error=CommandError.from_dict(error_raw),
            metrics=metrics,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "request_id": self.request_id,
            "ok": self.ok,
            "ops": [op.to_dict() for op in self.ops],
            "error": self.error.to_dict() if self.error is not None else None,
            "metrics": self.metrics,
        }
