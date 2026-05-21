from __future__ import annotations

import asyncio
import importlib.util
import inspect
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# The sandbox runner intentionally starts workers with a minimal, default-deny
# environment and without PYTHONPATH. In CI the package is tested from the source
# tree (pytest pythonpath=src) but is not installed into site-packages, so the
# child process must bootstrap the trusted package root derived from this file
# before importing amo_bot modules. This does not add cwd or user-provided paths.
_PACKAGE_ROOT = Path(__file__).resolve().parents[3]
if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))

from amo_bot.plugins.sandbox.types import SandboxErrorCode, SandboxRequest, SandboxResponse


def _safe_error(request_id: str, code: SandboxErrorCode, message: str) -> dict[str, object]:
    return SandboxResponse(
        request_id=request_id,
        ok=False,
        error_code=code.value,
        error_message=_sanitize_error(message),
    ).to_dict()


def _sanitize_error(message: str, *, fallback: str = "sandbox execution failed") -> str:
    clean = (message or "").strip().replace("\n", " ")[:220]
    if not clean or "traceback" in clean.lower():
        return fallback
    return clean


def _allowed_capabilities() -> set[str]:
    raw = os.environ.get("AMO_SANDBOX_ALLOWED_CAPABILITIES", "")
    return {item.strip() for item in raw.split(",") if item.strip()}


def _resolve_capability(action: str, payload: dict[str, object]) -> str:
    explicit = payload.get("capability")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    if action == "run":
        return "plugin.execute"
    return "plugin.unknown"


def _sanitize_value(value: object) -> object:
    if isinstance(value, dict):
        return _sanitize_payload(value)
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    return value


def _sanitize_payload(payload: dict[str, object]) -> dict[str, object]:
    redacted: dict[str, object] = {}
    for key, value in payload.items():
        if any(token in key.lower() for token in ("secret", "token", "password", "key")):
            redacted[key] = "[redacted]"
        else:
            redacted[key] = _sanitize_value(value)
    return redacted


class _PluginRuntimeError(RuntimeError):
    pass


@dataclass(slots=True, frozen=True)
class _RuntimeContext:
    plugin_id: str
    run_id: str
    trigger_type: str
    started_at: str | None = None
    scheduled_at: str | None = None


class _RecordingHostAPI:
    def __init__(self, *, permissions: set[str], max_ops: int = 16, max_text_len: int = 4000) -> None:
        self._permissions = permissions
        self._max_ops = max_ops
        self._max_text_len = max_text_len
        self._ops: list[dict[str, object]] = []

    def _require_permission(self, permission: str, operation: str) -> None:
        if permission not in self._permissions:
            raise _PluginRuntimeError(f"operation '{operation}' requires capability '{permission}'")

    async def send_message(self, chat_id: int, text: str) -> dict[str, object]:
        self._require_permission("send_message", "send_message")
        if not isinstance(chat_id, int):
            raise ValueError("chat_id must be int")
        text_clean = (text or "").strip()
        if not text_clean:
            raise ValueError("text must not be empty")
        self._append({"op": "send_message", "chat_id": chat_id, "text": text_clean[: self._max_text_len]})
        return {"ok": True}

    async def reply(self, chat_id: int, message_id: int, text: str) -> dict[str, object]:
        self._require_permission("send_message", "reply")
        if not isinstance(chat_id, int) or not isinstance(message_id, int):
            raise ValueError("chat_id and message_id must be int")
        text_clean = (text or "").strip()
        if not text_clean:
            raise ValueError("text must not be empty")
        self._append(
            {"op": "reply", "chat_id": chat_id, "message_id": message_id, "text": text_clean[: self._max_text_len]}
        )
        return {"ok": True}

    def _append(self, op: dict[str, object]) -> None:
        if len(self._ops) >= self._max_ops:
            raise _PluginRuntimeError("maximum operation count exceeded")
        self._ops.append(op)

    @property
    def ops(self) -> list[dict[str, object]]:
        return list(self._ops)


def _plugins_root() -> Path:
    raw = os.environ.get("AMO_SANDBOX_PLUGIN_DIR") or "plugins"
    return Path(raw).expanduser().resolve()


def _resolve_plugin_entry(payload: dict[str, object]) -> Path:
    entry = payload.get("plugin_entry")
    if not isinstance(entry, str) or not entry.strip():
        raise _PluginRuntimeError("plugin entry not found")
    candidate = Path(entry)
    if not candidate.is_absolute():
        candidate = _plugins_root() / candidate
    candidate = candidate.resolve()
    root = _plugins_root()
    if candidate != root and root not in candidate.parents:
        raise _PluginRuntimeError("plugin entry not allowed")
    if candidate.name != "main.py" or not candidate.is_file():
        raise _PluginRuntimeError("plugin entry not found")
    return candidate


def _load_handler(module_path: Path, trigger: str):
    handler_name = "handle_worker" if trigger == "worker" else "handle_schedule" if trigger == "schedule" else ""
    if not handler_name:
        raise _PluginRuntimeError("unsupported trigger")
    module_name = f"amo_sandbox_plugin_{module_path.parent.name}_{trigger}"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise _PluginRuntimeError("unable to load plugin module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    handler = getattr(module, handler_name, None)
    if handler is None or not callable(handler):
        raise _PluginRuntimeError(f"plugin {handler_name}(context, host_api) missing")
    if not inspect.iscoroutinefunction(handler):
        raise _PluginRuntimeError(f"plugin {handler_name} must be async")
    return handler


async def _execute_plugin(request: SandboxRequest) -> SandboxResponse:
    payload = request.payload
    trigger = payload.get("trigger")
    if trigger not in {"worker", "schedule"}:
        sanitized = _sanitize_payload(payload)
        return SandboxResponse(
            request_id=request.request_id,
            ok=True,
            result={"plugin_id": request.plugin_id, "action": request.action, "echo": sanitized},
        )

    permissions_raw = payload.get("permissions", [])
    if not isinstance(permissions_raw, list):
        raise _PluginRuntimeError("invalid permissions")
    permissions = {str(item) for item in permissions_raw if isinstance(item, str)}
    module_path = _resolve_plugin_entry(payload)
    context = _RuntimeContext(
        plugin_id=request.plugin_id,
        run_id=str(payload.get("run_id") or request.request_id),
        trigger_type=str(trigger),
        started_at=payload.get("started_at") if isinstance(payload.get("started_at"), str) else None,
        scheduled_at=payload.get("scheduled_at") if isinstance(payload.get("scheduled_at"), str) else None,
    )
    host_api = _RecordingHostAPI(permissions=permissions)
    handler = _load_handler(module_path, str(trigger))
    await handler(context, host_api)
    return SandboxResponse(
        request_id=request.request_id,
        ok=True,
        result={"plugin_id": request.plugin_id, "action": request.action, "ops": host_api.ops},
    )


def main() -> int:
    raw = sys.stdin.read()
    request_id = "unknown"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        sys.stdout.write(json.dumps(_safe_error(request_id, SandboxErrorCode.INVALID_REQUEST, "invalid_json")))
        sys.stdout.flush()
        return 1

    if not isinstance(parsed, dict):
        sys.stdout.write(json.dumps(_safe_error(request_id, SandboxErrorCode.INVALID_REQUEST, "invalid_shape")))
        sys.stdout.flush()
        return 1

    if isinstance(parsed.get("request_id"), str):
        request_id = parsed["request_id"]

    try:
        request = SandboxRequest.from_dict(parsed)
    except ValueError:
        sys.stdout.write(json.dumps(_safe_error(request_id, SandboxErrorCode.INVALID_REQUEST, "invalid_fields")))
        sys.stdout.flush()
        return 1

    capability = _resolve_capability(request.action, request.payload)
    if capability not in _allowed_capabilities():
        sys.stdout.write(json.dumps(_safe_error(request.request_id, SandboxErrorCode.INVALID_REQUEST, "capability_denied")))
        sys.stdout.flush()
        return 1

    try:
        response = asyncio.run(_execute_plugin(request))
    except Exception as exc:
        response = SandboxResponse(
            request_id=request.request_id,
            ok=False,
            error_code=SandboxErrorCode.WORKER_ERROR.value,
            error_message=_sanitize_error(str(exc)),
        )
    sys.stdout.write(json.dumps(response.to_dict()))
    sys.stdout.flush()
    return 0 if response.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
