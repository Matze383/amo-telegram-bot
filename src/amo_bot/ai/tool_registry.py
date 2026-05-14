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


class AIScopeKind(str, Enum):
    """Supported AI scope kinds for policy decisions."""

    TOPIC = "topic"
    PRIVATE = "private"


class AIRole(str, Enum):
    """Role levels used by the AI tool policy evaluator."""

    NORMAL = "normal"
    VIP = "vip"
    ADMIN = "admin"
    OWNER = "owner"


_ROLE_RANK: dict[AIRole, int] = {
    AIRole.NORMAL: 10,
    AIRole.VIP: 20,
    AIRole.ADMIN: 30,
    AIRole.OWNER: 40,
}


@dataclass(frozen=True, slots=True)
class AIToolScopeContext:
    """Scope metadata for evaluating whether a tool can be invoked."""

    scope_kind: AIScopeKind
    chat_id: int | None
    topic_id: int | None
    user_id: int | None = None


@dataclass(frozen=True, slots=True)
class AIToolPolicyDecision:
    """Machine-safe allow/deny decision for a tool invocation request."""

    allowed: bool
    reason_code: str


class AIToolPolicy:
    """Policy gate for AI tool usage.

    Default policy is deny-all for every tool, regardless of registration.
    """

    def __init__(
        self,
        *,
        enabled: bool = False,
        global_allowlist: set[str] | None = None,
        topic_allowlist: dict[tuple[int, int | None], set[str]] | None = None,
        private_allowlist: dict[int, set[str]] | None = None,
        min_role: AIRole = AIRole.OWNER,
    ) -> None:
        self._enabled = enabled
        self._global_allowlist = self._normalize_allowlist(global_allowlist)
        self._topic_allowlist = {
            key: self._normalize_allowlist(value) for key, value in (topic_allowlist or {}).items()
        }
        self._private_allowlist = {
            key: self._normalize_allowlist(value) for key, value in (private_allowlist or {}).items()
        }
        self._min_role = min_role

    @staticmethod
    def _normalize_allowlist(names: set[str] | None) -> set[str]:
        if not names:
            return set()
        normalized: set[str] = set()
        for name in names:
            if not isinstance(name, str):
                continue
            key = name.strip().lower()
            if key:
                normalized.add(key)
        return normalized

    def is_allowed(self, *, tool_name: str) -> bool:
        decision = self.evaluate(
            tool_name=tool_name,
            role=AIRole.OWNER,
            scope=AIToolScopeContext(scope_kind=AIScopeKind.TOPIC, chat_id=None, topic_id=None),
        )
        return decision.allowed

    def evaluate(self, *, tool_name: str, role: AIRole, scope: AIToolScopeContext) -> AIToolPolicyDecision:
        normalized_tool_name = tool_name.strip().lower() if isinstance(tool_name, str) else ""
        if not normalized_tool_name:
            return AIToolPolicyDecision(allowed=False, reason_code="invalid_tool_name")

        if not self._enabled:
            return AIToolPolicyDecision(allowed=False, reason_code="tools_disabled")

        if _ROLE_RANK[role] < _ROLE_RANK[self._min_role]:
            return AIToolPolicyDecision(allowed=False, reason_code="role_denied")

        if normalized_tool_name in self._global_allowlist:
            return AIToolPolicyDecision(allowed=True, reason_code="allowed_global")

        if scope.scope_kind is AIScopeKind.TOPIC:
            key = (scope.chat_id or 0, scope.topic_id)
            topic_allowed = self._topic_allowlist.get(key, set())
            if normalized_tool_name in topic_allowed:
                return AIToolPolicyDecision(allowed=True, reason_code="allowed_scope")
            return AIToolPolicyDecision(allowed=False, reason_code="not_in_scope_allowlist")

        if scope.scope_kind is AIScopeKind.PRIVATE:
            private_allowed = self._private_allowlist.get(scope.chat_id or 0, set())
            if normalized_tool_name in private_allowed:
                return AIToolPolicyDecision(allowed=True, reason_code="allowed_scope")
            return AIToolPolicyDecision(allowed=False, reason_code="not_in_scope_allowlist")

        return AIToolPolicyDecision(allowed=False, reason_code="scope_not_supported")


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
    *, request: AIToolInvocationRequest, policy: AIToolPolicy, role: AIRole = AIRole.OWNER, scope: AIToolScopeContext | None = None
) -> AIToolInvocationResponse:
    """No-op/fake tool invocation handler for KI-E2/KI-E3.

    This intentionally performs no real tool execution.
    """

    evaluation_scope = scope or AIToolScopeContext(scope_kind=AIScopeKind.TOPIC, chat_id=None, topic_id=None)
    decision = policy.evaluate(tool_name=request.tool_name, role=role, scope=evaluation_scope)

    if not decision.allowed:
        return AIToolInvocationResponse(
            status=AIToolInvocationStatus.DENIED,
            tool_name=request.tool_name,
            call_id=request.call_id,
            result=None,
            error_code="policy_denied",
            reason=decision.reason_code,
        )

    return AIToolInvocationResponse(
        status=AIToolInvocationStatus.SUCCESS,
        tool_name=request.tool_name,
        call_id=request.call_id,
        result={"mode": "noop", "executed": False},
        error_code=None,
        reason=None,
    )
