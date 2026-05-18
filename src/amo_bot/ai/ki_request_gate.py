from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any, Protocol


_SAFE_FALLBACK = "I can't execute that request in this context."
_SAFE_REASON_RE = re.compile(r"^[a-z0-9_]{1,48}$")
_SECRET_KEY_RE = re.compile(
    r"(?:api[_-]?key|token|secret|password|passwd|authorization|cookie|session|private[_-]?key)",
    re.IGNORECASE,
)
_BASE64_RE = re.compile(r"\b(?:[A-Za-z0-9+/]{40,}={0,2}|[A-Za-z0-9_-]{40,})\b")
_HEX_RE = re.compile(r"\b[a-f0-9]{32,}\b", re.IGNORECASE)


class KIPluginRequestDecision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class KIPluginRequest:
    plugin_id: str
    capability_id: str
    params: dict[str, Any]
    reason: str


@dataclass(frozen=True, slots=True)
class KIPluginPolicyContext:
    scope_id: str
    consent_given: bool


@dataclass(frozen=True, slots=True)
class KIPluginDecisionResult:
    decision: KIPluginRequestDecision
    reason_code: str


@dataclass(frozen=True, slots=True)
class KIAuditEntry:
    plugin_id: str
    capability_id: str
    decision: KIPluginRequestDecision
    reason_code: str


@dataclass(frozen=True, slots=True)
class KIPluginExecutionOutcome:
    forwarded: bool
    response_text: str
    result: dict[str, Any] | None
    audit: KIAuditEntry


class PluginSandboxRunner(Protocol):
    def run_plugin_capability(self, *, plugin_id: str, capability_id: str, params: dict[str, Any]) -> dict[str, Any]: ...


class KIPluginPolicyGate:
    def __init__(
        self,
        *,
        enabled_plugins: set[str] | None = None,
        allowed_capabilities: dict[str, set[str]] | None = None,
        allowed_scopes: dict[str, set[str]] | None = None,
        fallback_text: str = _SAFE_FALLBACK,
        max_result_chars: int = 800,
        max_result_items: int = 20,
    ) -> None:
        self._enabled_plugins = {x.strip().lower() for x in (enabled_plugins or set()) if isinstance(x, str) and x.strip()}
        self._allowed_capabilities = {
            plugin.strip().lower(): {c.strip().lower() for c in caps if isinstance(c, str) and c.strip()}
            for plugin, caps in (allowed_capabilities or {}).items()
            if isinstance(plugin, str) and plugin.strip()
        }
        self._allowed_scopes = {
            plugin.strip().lower(): {s.strip() for s in scopes if isinstance(s, str) and s.strip()}
            for plugin, scopes in (allowed_scopes or {}).items()
            if isinstance(plugin, str) and plugin.strip()
        }
        self._fallback_text = fallback_text.strip() or _SAFE_FALLBACK
        self._max_result_chars = max(64, int(max_result_chars))
        self._max_result_items = max(1, int(max_result_items))

    @staticmethod
    def parse_request(payload: dict[str, Any]) -> KIPluginRequest | None:
        if not isinstance(payload, dict):
            return None
        plugin_id = payload.get("plugin_id")
        capability_id = payload.get("capability_id")
        params = payload.get("params")
        reason = payload.get("reason")

        if not isinstance(plugin_id, str) or not plugin_id.strip():
            return None
        if not isinstance(capability_id, str) or not capability_id.strip():
            return None
        if not isinstance(params, dict):
            return None
        if not isinstance(reason, str) or not reason.strip():
            return None

        return KIPluginRequest(
            plugin_id=plugin_id.strip().lower(),
            capability_id=capability_id.strip().lower(),
            params=dict(params),
            reason=reason.strip(),
        )

    def evaluate(self, *, request: KIPluginRequest, context: KIPluginPolicyContext) -> KIPluginDecisionResult:
        plugin_id = request.plugin_id
        if plugin_id not in self._enabled_plugins:
            return KIPluginDecisionResult(decision=KIPluginRequestDecision.DENY, reason_code="plugin_not_enabled")

        allowed_caps = self._allowed_capabilities.get(plugin_id, set())
        if request.capability_id not in allowed_caps:
            return KIPluginDecisionResult(decision=KIPluginRequestDecision.DENY, reason_code="capability_not_allowed")

        allowed_scopes = self._allowed_scopes.get(plugin_id, set())
        if context.scope_id not in allowed_scopes:
            return KIPluginDecisionResult(decision=KIPluginRequestDecision.DENY, reason_code="scope_not_permitted")

        if not context.consent_given:
            return KIPluginDecisionResult(decision=KIPluginRequestDecision.DENY, reason_code="consent_required")

        return KIPluginDecisionResult(decision=KIPluginRequestDecision.ALLOW, reason_code="policy_allow")

    def handle_request(
        self,
        *,
        payload: dict[str, Any],
        context: KIPluginPolicyContext,
        runner: PluginSandboxRunner,
    ) -> KIPluginExecutionOutcome:
        request = self.parse_request(payload)
        if request is None:
            return self._deny_outcome(plugin_id="unknown", capability_id="unknown", reason_code="invalid_request")

        decision = self.evaluate(request=request, context=context)
        if decision.decision is KIPluginRequestDecision.DENY:
            return self._deny_outcome(
                plugin_id=request.plugin_id,
                capability_id=request.capability_id,
                reason_code=decision.reason_code,
            )

        raw_result = runner.run_plugin_capability(
            plugin_id=request.plugin_id,
            capability_id=request.capability_id,
            params=request.params,
        )
        sanitized = self._sanitize_result(raw_result)
        return KIPluginExecutionOutcome(
            forwarded=True,
            response_text="plugin request executed",
            result=sanitized,
            audit=KIAuditEntry(
                plugin_id=request.plugin_id,
                capability_id=request.capability_id,
                decision=KIPluginRequestDecision.ALLOW,
                reason_code="policy_allow",
            ),
        )

    def reject_direct_invocation(self, *, tool_name: str) -> KIPluginExecutionOutcome:
        safe_tool_name = tool_name.strip().lower() if isinstance(tool_name, str) and tool_name.strip() else "unknown"
        return self._deny_outcome(plugin_id=safe_tool_name, capability_id="direct_invoke", reason_code="direct_invocation_forbidden")

    def _deny_outcome(self, *, plugin_id: str, capability_id: str, reason_code: str) -> KIPluginExecutionOutcome:
        safe_reason = reason_code if _SAFE_REASON_RE.fullmatch(reason_code) else "request_denied"
        return KIPluginExecutionOutcome(
            forwarded=False,
            response_text=self._fallback_text,
            result=None,
            audit=KIAuditEntry(
                plugin_id=plugin_id,
                capability_id=capability_id,
                decision=KIPluginRequestDecision.DENY,
                reason_code=safe_reason,
            ),
        )

    def _sanitize_result(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {"status": "ok", "result": "[non_dict_result]"}

        out: dict[str, Any] = {}
        for key, inner in value.items():
            if len(out) >= self._max_result_items:
                out["_truncated"] = True
                break
            if not isinstance(key, str):
                continue
            lowered = key.lower()
            if lowered.startswith("raw_") or lowered in {"private", "internal", "debug", "prompt", "secrets"}:
                continue
            if _SECRET_KEY_RE.search(lowered):
                out[key] = "[redacted]"
                continue
            out[key] = self._sanitize_value(inner)

        return out

    def _sanitize_value(self, value: Any) -> Any:
        if isinstance(value, dict):
            nested = self._sanitize_result(value)
            return nested
        if isinstance(value, list):
            trimmed = [self._sanitize_value(item) for item in value[: self._max_result_items]]
            if len(value) > self._max_result_items:
                trimmed.append("[truncated]")
            return trimmed
        if isinstance(value, str):
            clipped = value[: self._max_result_chars]
            if _BASE64_RE.search(clipped) or _HEX_RE.search(clipped):
                return "[redacted]"
            return clipped
        return value
