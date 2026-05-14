from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any


class AIToolCapability(str, Enum):
    """High-level capability labels for AI-usable tools."""

    READ = "read"
    QUERY = "query"
    COMPUTE = "compute"
    NOTIFY = "notify"


@dataclass(frozen=True, slots=True)
class AIToolDescriptor:
    """Metadata-only descriptor for a tool that could be AI-usable later."""

    name: str
    capability: AIToolCapability
    description: str


class AIToolRegistry:
    """In-memory registry for AI tool descriptors.

    Registry is descriptor-only and performs no tool execution.
    """

    def __init__(self) -> None:
        self._tools_by_name: dict[str, AIToolDescriptor] = {}

    def register(self, descriptor: AIToolDescriptor) -> None:
        key = descriptor.name.strip().lower()
        if not key:
            raise ValueError("tool name must not be empty")
        if key in self._tools_by_name:
            raise ValueError(f"tool already registered: {descriptor.name}")
        self._tools_by_name[key] = descriptor

    def get(self, name: str) -> AIToolDescriptor | None:
        return self._tools_by_name.get(name.strip().lower())

    def list_tools(self) -> list[AIToolDescriptor]:
        return [self._tools_by_name[k] for k in sorted(self._tools_by_name.keys())]

    def list_by_capability(self, capability: AIToolCapability) -> list[AIToolDescriptor]:
        return [tool for tool in self.list_tools() if tool.capability == capability]


class AIToolPolicy:
    """Policy gate for AI tool usage.

    Default policy is deny-all for every tool, regardless of registration.
    """

    def is_allowed(self, *, tool_name: str) -> bool:
        _ = tool_name
        return False


_SAFE_TOKEN_PATTERN = re.compile(r"^[a-z0-9_]{1,32}$")


def _normalize_safe_token(value: str | None, *, fallback: str) -> str:
    """Normalize caller-provided token to a strict machine-safe shape."""

    if not isinstance(value, str):
        return fallback
    normalized = value.strip().lower()
    if not normalized:
        return fallback
    if _SAFE_TOKEN_PATTERN.fullmatch(normalized):
        return normalized
    return fallback


class AIToolInvocationStatus(str, Enum):
    """Safe status labels for tool invocation envelope responses."""

    SUCCESS = "success"
    ERROR = "error"
    DENIED = "denied"


@dataclass(frozen=True, slots=True)
class AIToolInvocationRequest:
    """Validated request envelope for future AI tool invocation."""

    tool_name: str
    arguments: dict[str, Any]
    call_id: str | None = None


@dataclass(frozen=True, slots=True)
class AIToolInvocationResponse:
    """Safe response envelope for tool invocation outcomes."""

    status: AIToolInvocationStatus
    tool_name: str
    call_id: str | None
    result: dict[str, Any] | None
    error_code: str | None
    reason: str | None


def validate_tool_invocation_request(payload: dict[str, Any]) -> tuple[AIToolInvocationRequest | None, str | None]:
    """Validate and normalize a future tool invocation request envelope."""

    tool_name_raw = payload.get("tool_name")
    if not isinstance(tool_name_raw, str) or not tool_name_raw.strip():
        return None, "invalid_tool_name"
    tool_name = tool_name_raw.strip().lower()

    arguments_raw = payload.get("arguments", {})
    if not isinstance(arguments_raw, dict):
        return None, "invalid_arguments"

    call_id_raw = payload.get("call_id")
    if call_id_raw is not None:
        if not isinstance(call_id_raw, str) or not call_id_raw.strip():
            return None, "invalid_call_id"
        call_id = call_id_raw.strip()
    else:
        call_id = None

    return AIToolInvocationRequest(tool_name=tool_name, arguments=dict(arguments_raw), call_id=call_id), None


def build_tool_invocation_rejection(
    *, tool_name: str = "unknown", call_id: str | None = None, reason: str
) -> AIToolInvocationResponse:
    """Build safe validation/policy rejection envelope without internal leakage."""

    normalized_tool_name = tool_name.strip().lower() if isinstance(tool_name, str) and tool_name.strip() else "unknown"
    normalized_reason = _normalize_safe_token(reason, fallback="request_rejected")
    return AIToolInvocationResponse(
        status=AIToolInvocationStatus.DENIED,
        tool_name=normalized_tool_name,
        call_id=call_id,
        result=None,
        error_code="request_rejected",
        reason=normalized_reason,
    )


def build_tool_invocation_error(
    *, tool_name: str, call_id: str | None = None, error_code: str = "internal_error", reason: str = "execution_failed"
) -> AIToolInvocationResponse:
    """Build safe execution error envelope without exposing internals."""

    normalized_tool_name = tool_name.strip().lower() if tool_name.strip() else "unknown"
    normalized_error_code = _normalize_safe_token(error_code, fallback="internal_error")
    normalized_reason = _normalize_safe_token(reason, fallback="execution_failed")
    return AIToolInvocationResponse(
        status=AIToolInvocationStatus.ERROR,
        tool_name=normalized_tool_name,
        call_id=call_id,
        result=None,
        error_code=normalized_error_code,
        reason=normalized_reason,
    )


def invoke_tool_noop(
    *, request: AIToolInvocationRequest, policy: AIToolPolicy
) -> AIToolInvocationResponse:
    """No-op/fake tool invocation handler for KI-E2.

    This intentionally performs no real tool execution.
    """

    if not policy.is_allowed(tool_name=request.tool_name):
        return AIToolInvocationResponse(
            status=AIToolInvocationStatus.DENIED,
            tool_name=request.tool_name,
            call_id=request.call_id,
            result=None,
            error_code="policy_denied",
            reason="tool_not_allowed",
        )

    return AIToolInvocationResponse(
        status=AIToolInvocationStatus.SUCCESS,
        tool_name=request.tool_name,
        call_id=request.call_id,
        result={"mode": "noop", "executed": False},
        error_code=None,
        reason=None,
    )
