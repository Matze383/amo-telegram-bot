from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping

from .capability_audit import CapabilityAuditTrail

from sqlalchemy import text
from sqlalchemy.engine import Engine, RowMapping


class SQLExecutionResult(StrEnum):
    SUCCESS = "success"
    DENIED = "denied"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class SQLTemplate:
    template_id: str
    sql: str
    allowed_views: tuple[str, ...]
    allowed_params: tuple[str, ...]
    max_rows: int = 100


@dataclass(frozen=True, slots=True)
class SQLCorepluginRequest:
    template_id: str
    params: Mapping[str, Any]
    actor_type: str | None = None
    scope_type: str | None = None
    context_flags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SQLCorepluginResponse:
    result: SQLExecutionResult
    reason_code: str
    rows: tuple[dict[str, Any], ...] = ()
    row_count: int = 0
    truncated: bool = False


_FORBIDDEN_TABLE_TOKENS = (
    "users",
    "user_secrets",
    "topic_daily_memories",
    "topic_long_memories",
    "plugin_settings",
)

_MAX_PARAM_STR_LEN = 120
_MAX_COLUMNS = 12
_DEFAULT_MASK = "***MASKED***"
_ALLOWED_ACTOR_TYPES = {"ki", "user_plugin"}
_ALLOWED_SCOPE_TYPES = {"chat", "topic"}
_DENIED_ELEVATED_FLAGS = {"admin", "tunnel", "elevated"}


class SQLCorepluginService:
    """CP-H1: template-only, read-only SQL execution over allowlisted views."""

    CAPABILITY_NAME = "ki.sql.query"
    CAPABILITY_VERSION = "1.0.0"

    def __init__(
        self,
        *,
        engine: Engine,
        templates: Mapping[str, SQLTemplate],
        max_rows_global: int = 200,
        max_columns: int = _MAX_COLUMNS,
        masked_columns: tuple[str, ...] = ("chat_id", "user_id", "topic_id"),
        audit_trail: CapabilityAuditTrail | None = None,
    ) -> None:
        self._engine = engine
        self._templates = {key.strip().lower(): value for key, value in templates.items()}
        self._max_rows_global = max(1, min(max_rows_global, 500))
        self._max_columns = max(1, min(max_columns, 24))
        self._masked_columns = {c.strip().lower() for c in masked_columns if c and c.strip()}
        self._audit = audit_trail

    def execute(self, request: SQLCorepluginRequest) -> SQLCorepluginResponse:
        request_id = self._build_request_id(request)
        self._record_requested(request_id=request_id, request=request)

        policy_deny_reason = self._validate_policy_context(request)
        if policy_deny_reason:
            return self._deny(request_id=request_id, reason_code=policy_deny_reason)

        template = self._templates.get((request.template_id or "").strip().lower())
        if template is None:
            return self._deny(request_id=request_id, reason_code="unknown_template")

        normalized_sql = " ".join(template.sql.strip().split()).lower()
        if self._contains_forbidden_table(normalized_sql):
            return self._deny(request_id=request_id, reason_code="forbidden_table")

        if not self._is_safe_select_template(template, normalized_sql):
            return self._deny(request_id=request_id, reason_code="invalid_sql_template")

        deny_reason = self._validate_params(request.params, template.allowed_params)
        if deny_reason:
            return self._deny(request_id=request_id, reason_code=deny_reason)

        limit = min(template.max_rows, self._max_rows_global)
        query_params = dict(request.params)
        query_params["_cap_limit"] = limit + 1

        try:
            with self._engine.connect() as connection:
                result = connection.execute(text(template.sql), query_params)
                fetched = result.mappings().fetchall()
        except Exception:
            self._record_failed(request_id=request_id, error_code="db_error")
            return SQLCorepluginResponse(result=SQLExecutionResult.ERROR, reason_code="db_error")

        truncated = len(fetched) > limit
        bounded_rows = fetched[:limit]
        safe_rows = tuple(self._sanitize_row(row) for row in bounded_rows)
        self._record_decision(request_id=request_id, decision_result="allow", reason_code="ok")

        return SQLCorepluginResponse(
            result=SQLExecutionResult.SUCCESS,
            reason_code="ok",
            rows=safe_rows,
            row_count=len(safe_rows),
            truncated=truncated,
        )

    def _is_safe_select_template(self, template: SQLTemplate, normalized_sql: str) -> bool:
        if not normalized_sql.startswith("select "):
            return False
        if any(token in normalized_sql for token in ("--", ";", "/*", "*/")):
            return False
        if any(
            token in normalized_sql
            for token in (
                " insert ",
                " update ",
                " delete ",
                " drop ",
                " alter ",
                " pragma ",
                " attach ",
                " detach ",
                " vacuum ",
                " union ",
            )
        ):
            return False
        for allowed_view in template.allowed_views:
            name = allowed_view.strip().lower()
            if not name:
                continue
            for marker in (f" from {name}", f" join {name}"):
                if marker in normalized_sql:
                    return True
        return False

    def _validate_policy_context(self, request: SQLCorepluginRequest) -> str | None:
        actor_type = (request.actor_type or "").strip().lower()
        scope_type = (request.scope_type or "").strip().lower()

        if actor_type not in _ALLOWED_ACTOR_TYPES:
            return "missing_or_invalid_actor"
        if scope_type not in _ALLOWED_SCOPE_TYPES:
            return "missing_or_invalid_scope"

        normalized_flags = {flag.strip().lower() for flag in request.context_flags if flag and flag.strip()}
        if normalized_flags & _DENIED_ELEVATED_FLAGS:
            return "elevated_context_denied"

        return None

    def _validate_params(self, params: Mapping[str, Any], allowed_params: tuple[str, ...]) -> str | None:
        allowed = {p.strip() for p in allowed_params}
        if set(params.keys()) - allowed:
            return "invalid_params"
        for value in params.values():
            if value is None:
                continue
            if isinstance(value, str):
                if len(value) > _MAX_PARAM_STR_LEN:
                    return "invalid_params"
                if any(token in value.lower() for token in ("--", ";", "/*", "*/", " union ", " drop ")):
                    return "injection_detected"
            elif isinstance(value, (int, float, bool)):
                continue
            else:
                return "invalid_params"
        return None

    def _sanitize_row(self, row: RowMapping) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for idx, (key, value) in enumerate(row.items()):
            if idx >= self._max_columns:
                break
            normalized_key = str(key).lower()
            if normalized_key in self._masked_columns:
                out[str(key)] = _DEFAULT_MASK
            elif isinstance(value, str):
                out[str(key)] = value[:160]
            else:
                out[str(key)] = value
        return out

    def _contains_forbidden_table(self, normalized_sql: str) -> bool:
        return any(f" {forbidden} " in normalized_sql for forbidden in _FORBIDDEN_TABLE_TOKENS)

    def _deny(self, *, request_id: str, reason_code: str) -> SQLCorepluginResponse:
        self._record_decision(request_id=request_id, decision_result="deny", reason_code=reason_code)
        return SQLCorepluginResponse(result=SQLExecutionResult.DENIED, reason_code=reason_code)

    def _record_requested(self, *, request_id: str, request: SQLCorepluginRequest) -> None:
        if self._audit is None:
            return
        self._audit.record_requested(
            request_id=request_id,
            capability_name=self.CAPABILITY_NAME,
            capability_version=self.CAPABILITY_VERSION,
            actor_type=self._safe_meta_value(request.actor_type, fallback="unknown"),
            scope_type=self._safe_meta_value(request.scope_type, fallback="unknown"),
            input_summary_count=min(len(request.params), 32),
            input_summary_approx_bytes=0,
            risk_flags_count=min(len(request.context_flags), 16),
        )

    def _record_decision(self, *, request_id: str, decision_result: str, reason_code: str) -> None:
        if self._audit is None:
            return
        self._audit.record_decision(
            request_id=request_id,
            capability_name=self.CAPABILITY_NAME,
            capability_version=self.CAPABILITY_VERSION,
            decision_result=decision_result,
            reason_code=reason_code,
        )

    def _record_failed(self, *, request_id: str, error_code: str) -> None:
        if self._audit is None:
            return
        self._audit.record_failed(
            request_id=request_id,
            capability_name=self.CAPABILITY_NAME,
            capability_version=self.CAPABILITY_VERSION,
            error_code=error_code,
        )

    def _build_request_id(self, request: SQLCorepluginRequest) -> str:
        template_part = self._safe_meta_value(request.template_id, fallback="unknown")
        actor_part = self._safe_meta_value(request.actor_type, fallback="unknown")
        scope_part = self._safe_meta_value(request.scope_type, fallback="unknown")
        return f"sql_{template_part}_{actor_part}_{scope_part}"[:64]

    def _safe_meta_value(self, raw: str | None, *, fallback: str) -> str:
        normalized = "".join(ch for ch in (raw or "").strip().lower() if ch.isalnum() or ch == "_")
        if not normalized:
            return fallback
        return normalized[:16]


def build_default_sql_templates() -> dict[str, SQLTemplate]:
    return {
        "topic_activity_summary": SQLTemplate(
            template_id="topic_activity_summary",
            sql=(
                "SELECT chat_id, topic_id, message_count, active_users "
                "FROM v_topic_activity_summary "
                "WHERE chat_id = :chat_id "
                "ORDER BY message_count DESC "
                "LIMIT :_cap_limit"
            ),
            allowed_views=("v_topic_activity_summary",),
            allowed_params=("chat_id",),
            max_rows=100,
        ),
        "plugin_health_overview": SQLTemplate(
            template_id="plugin_health_overview",
            sql=(
                "SELECT plugin_id, status, last_run_at, fail_count "
                "FROM v_plugin_health_overview "
                "WHERE status = :status "
                "LIMIT :_cap_limit"
            ),
            allowed_views=("v_plugin_health_overview",),
            allowed_params=("status",),
            max_rows=80,
        ),
    }
