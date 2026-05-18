from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class SandboxErrorCode(str, Enum):
    INVALID_REQUEST = "invalid_request"
    INVALID_RESPONSE = "invalid_response"
    WORKER_ERROR = "worker_error"
    WORKER_TIMEOUT = "worker_timeout"
    PROTOCOL_ERROR = "protocol_error"


@dataclass(slots=True, frozen=True)
class SandboxRequest:
    request_id: str
    plugin_id: str
    action: str
    payload: dict[str, Any]
    timeout_ms: int

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SandboxRequest":
        request_id = data.get("request_id")
        plugin_id = data.get("plugin_id")
        action = data.get("action")
        payload = data.get("payload")
        timeout_ms = data.get("timeout_ms")

        if not isinstance(request_id, str) or not request_id.strip():
            raise ValueError("request_id")
        if not isinstance(plugin_id, str) or not plugin_id.strip():
            raise ValueError("plugin_id")
        if not isinstance(action, str) or not action.strip():
            raise ValueError("action")
        if not isinstance(payload, dict):
            raise ValueError("payload")
        if not isinstance(timeout_ms, int) or timeout_ms <= 0:
            raise ValueError("timeout_ms")

        return cls(
            request_id=request_id,
            plugin_id=plugin_id,
            action=action,
            payload=payload,
            timeout_ms=timeout_ms,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "plugin_id": self.plugin_id,
            "action": self.action,
            "payload": self.payload,
            "timeout_ms": self.timeout_ms,
        }


@dataclass(slots=True, frozen=True)
class SandboxResponse:
    request_id: str
    ok: bool
    result: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SandboxResponse":
        request_id = data.get("request_id")
        ok = data.get("ok")
        result = data.get("result")
        error_code = data.get("error_code")
        error_message = data.get("error_message")

        if not isinstance(request_id, str) or not request_id.strip():
            raise ValueError("request_id")
        if not isinstance(ok, bool):
            raise ValueError("ok")

        if ok:
            if result is not None and not isinstance(result, dict):
                raise ValueError("result")
            if error_code is not None or error_message is not None:
                raise ValueError("error_fields_for_ok")
        else:
            if not isinstance(error_code, str) or not error_code.strip():
                raise ValueError("error_code")
            if error_message is not None and not isinstance(error_message, str):
                raise ValueError("error_message")
            if result is not None:
                raise ValueError("result_for_error")

        return cls(
            request_id=request_id,
            ok=ok,
            result=result,
            error_code=error_code,
            error_message=error_message,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "ok": self.ok,
            "result": self.result,
            "error_code": self.error_code,
            "error_message": self.error_message,
        }


class SandboxRunnerError(Exception):
    def __init__(self, code: SandboxErrorCode, message: str) -> None:
        super().__init__(f"{code.value}:{message}")
        self.code = code
        self.message = message

    def to_response(self, request_id: str) -> SandboxResponse:
        return SandboxResponse(
            request_id=request_id,
            ok=False,
            error_code=self.code.value,
            error_message=self.message,
        )
