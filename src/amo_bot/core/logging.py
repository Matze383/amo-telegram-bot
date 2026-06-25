from __future__ import annotations

import contextvars
import json
import logging
import os
import re
import time
import uuid
from typing import Any

# ── correlation IDs ───────────────────────────────────────────────────────────

# Per-request/thread correlation — Telegram update_id or WebUI request ID.
request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)

# Per-scheduled-job run ID.
run_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "run_id", default=None
)

# ── ID masking ────────────────────────────────────────────────────────────────

_USER_ID_RE = re.compile(r"\b(\d{6,})\b")


def _masked_id(value: str | int) -> str:
    """Hash-like mask for Telegram/chat IDs — first 3 + last 2 digits + length."""
    s = str(value)
    if len(s) <= 5:
        return "***"
    return f"{s[:3]}***..{s[-2:]} [{len(s)} digits]"


def masked_id(value: str | int | None) -> str:
    """Public API: safe string representation of an ID that may be private."""
    if value is None:
        return "none"
    return _masked_id(value)


# ── Sensitive text redaction ──────────────────────────────────────────────────

_TELEGRAM_BOT_URL_RE = re.compile(r"(/bot)([^/\s]+)(/)")
_REDACTED_BOT_SEGMENT = r"\1***REDACTED***\3"

_BEARER_RE = re.compile(r"(?i)(\bAuthorization\s*[:=]\s*Bearer\s+)([^\s,;]+)")
_KEYVALUE_SECRET_RE = re.compile(
    r"(?i)(\b(?:api_key|key|token|password|secret|cookie|session)\b\s*[:=]\s*)([^\s,;&]+)"
)
_PREFIX_SECRET_RE = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{8,}|ghp_[A-Za-z0-9]{8,})\b")


def redact_sensitive_text(value: str) -> str:
    redacted = _TELEGRAM_BOT_URL_RE.sub(_REDACTED_BOT_SEGMENT, value)
    redacted = _BEARER_RE.sub(r"\1***REDACTED***", redacted)
    redacted = _KEYVALUE_SECRET_RE.sub(r"\1***REDACTED***", redacted)
    redacted = _PREFIX_SECRET_RE.sub("***REDACTED***", redacted)
    return redacted


def _mask_sensitive_text(value: str) -> str:
    return redact_sensitive_text(value)


class SensitiveLogFilter(logging.Filter):
    """Mask known secret-bearing URL segments and credentials in log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._mask(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: self._mask(v) for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(self._mask(v) for v in record.args)
            else:
                record.args = self._mask(record.args)
        if record.exc_info and len(record.exc_info) >= 2 and record.exc_info[1] is not None:
            exc = record.exc_info[1]
            if exc.args:
                exc.args = tuple(self._mask(v) for v in exc.args)
        if record.stack_info:
            record.stack_info = _mask_sensitive_text(record.stack_info)
        return True

    def _mask(self, value: Any) -> Any:
        if isinstance(value, str):
            return _mask_sensitive_text(value)
        if isinstance(value, dict):
            return {k: self._mask(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._mask(v) for v in value]
        if isinstance(value, tuple):
            return tuple(self._mask(v) for v in value)
        return value


# ── Formatters ────────────────────────────────────────────────────────────────


class TextFormatter(logging.Formatter):
    """Human-readable formatter that injects correlation IDs when set."""

    def format(self, record: logging.LogRecord) -> str:
        parts = [super().format(record)]

        req_id = request_id_var.get()
        if req_id:
            parts.append(f"[req={req_id}]")

        run_id = run_id_var.get()
        if run_id:
            parts.append(f"[run={run_id}]")

        if parts:
            return " ".join(parts)
        return super().format(record)


class JSONFormatter(logging.Formatter):
    """Structured JSON formatter with correlation IDs and standard fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": str(record.getMessage()),
        }

        req_id = request_id_var.get()
        if req_id:
            payload["request_id"] = req_id

        run_id = run_id_var.get()
        if run_id:
            payload["run_id"] = run_id

        if record.exc_info:
            payload["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
            payload["exc_msg"] = str(record.exc_info[1]) if record.exc_info[1] else None

        if record.name:
            payload["logger"] = record.name

        # Extra fields from LogRecord (via extra={...})
        for key in {"event", "component", "update_id", "chat_scope", "user_scope",
                    "message_id", "command", "duration_ms", "outcome", "reason_code",
                    "chat_id", "user_id", "thread_id", "plugin_id"}:
            if hasattr(record, key):
                value = getattr(record, key)
                if value is not None:
                    payload[key] = value

        return json.dumps(payload, ensure_ascii=True)


# ── Duration timer ────────────────────────────────────────────────────────────

class duration_timer:
    """Context manager that records elapsed milliseconds in a dict on exit."""

    def __init__(self, store: dict[str, Any], key: str = "duration_ms") -> None:
        self._store = store
        self._key = key
        self._start: float | None = None

    def __enter__(self) -> "duration_timer":
        self._start = time.monotonic()
        return self

    def __exit__(self, *_: Any) -> None:
        if self._start is not None:
            self._store[self._key] = int((time.monotonic() - self._start) * 1000)


# ── ID scope helpers ──────────────────────────────────────────────────────────

def _chat_scope(chat_id: int | None) -> str:
    if chat_id is None:
        return "none"
    if chat_id < 0:
        return "group"
    return "private"


def _user_scope(user_id: int | None) -> str:
    return "user" if user_id is not None else "none"


# ── Structured event helpers ──────────────────────────────────────────────────


def log_event(
    logger: logging.Logger,
    level: int,
    *,
    event: str,
    component: str,
    update_id: int | None = None,
    chat_id: int | None = None,
    message_id: int | None = None,
    message_thread_id: int | None = None,
    user_id: int | None = None,
    command: str | None = None,
    duration_ms: int | None = None,
    outcome: str | None = None,
    reason_code: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """
    Emit a structured log event with standard fields.

    Private IDs are masked by default unless LOG_INCLUDE_PRIVATE_IDS is set.
    Raw message text / prompts / tokens / cookies / API keys / file contents
    must NEVER be included.
    """
    include_private = os.environ.get("LOG_INCLUDE_PRIVATE_IDS", "").lower() in (
        "1", "true", "yes", "on",
    )

    kwargs: dict[str, Any] = {
        "event": event,
        "component": component,
    }

    if update_id is not None:
        kwargs["update_id"] = update_id

    if include_private:
        if chat_id is not None:
            kwargs["chat_id"] = chat_id
        if user_id is not None:
            kwargs["user_id"] = user_id
        if message_id is not None:
            kwargs["message_id"] = message_id
        if message_thread_id is not None:
            kwargs["thread_id"] = message_thread_id
    else:
        if chat_id is not None:
            kwargs["chat_scope"] = _chat_scope(chat_id)
            kwargs["_chat_id_masked"] = _masked_id(chat_id)
        if user_id is not None:
            kwargs["user_scope"] = _user_scope(user_id)
            kwargs["_user_id_masked"] = _masked_id(user_id)
        if message_id is not None:
            kwargs["message_id"] = message_id
        if message_thread_id is not None:
            kwargs["thread_id"] = message_thread_id

    if command is not None:
        kwargs["command"] = command

    if duration_ms is not None:
        kwargs["duration_ms"] = duration_ms

    if outcome is not None:
        kwargs["outcome"] = outcome

    if reason_code is not None:
        kwargs["reason_code"] = reason_code

    if extra:
        for k, v in extra.items():
            if k not in kwargs:
                kwargs[k] = v

    logger.log(level, "%s", kwargs)


# ── Setup ─────────────────────────────────────────────────────────────────────

_LOG_LEVELS = {"debug": logging.DEBUG, "info": logging.INFO, "warning": logging.WARNING, "error": logging.ERROR}
_LOG_DEBUG_SCOPES: set[str] = set()
_LOG_FORMAT: str = "text"
_LOG_FILE: str | None = None
_LOG_INCLUDE_PRIVATE_IDS: bool = False


def _init_from_env() -> None:
    global _LOG_LEVEL, _LOG_DEBUG_SCOPES, _LOG_FORMAT, _LOG_FILE, _LOG_INCLUDE_PRIVATE_IDS  # noqa: PLW0603
    level_name = os.environ.get("LOG_LEVEL", "info").lower()
    _LOG_LEVEL = _LOG_LEVELS.get(level_name, logging.INFO)

    debug_scopes = os.environ.get("LOG_DEBUG_SCOPES", "")
    _LOG_DEBUG_SCOPES = set(s.strip() for s in debug_scopes.split(",") if s.strip())

    _LOG_FORMAT = os.environ.get("LOG_FORMAT", "text").lower()
    _LOG_FILE = os.environ.get("LOG_FILE") or None

    _LOG_INCLUDE_PRIVATE_IDS = os.environ.get("LOG_INCLUDE_PRIVATE_IDS", "").lower() in (
        "1", "true", "yes", "on",
    )


def _is_debug_scope(component: str) -> bool:
    return component in _LOG_DEBUG_SCOPES


def setup_logging(
    level: int | None = None,
    log_format: str | None = None,
    log_file: str | None = None,
    debug_scopes: set[str] | None = None,
    include_private_ids: bool | None = None,
) -> None:
    """
    Configure the Python logging system.

    Environment variables (all optional):
      LOG_LEVEL              – debug|info|warning|error  (default: INFO)
      LOG_FORMAT             – text|json                 (default: text)
      LOG_FILE               – path to file              (default: stderr only)
      LOG_DEBUG_SCOPES       – comma-separated component names for DEBUG-level noise
      LOG_INCLUDE_PRIVATE_IDS – 1/true/yes/on to include unmasked IDs

    Per-call overrides:
      level, log_format, log_file, debug_scopes, include_private_ids
    """
    global _LOG_LEVEL, _LOG_DEBUG_SCOPES, _LOG_FORMAT, _LOG_FILE, _LOG_INCLUDE_PRIVATE_IDS  # noqa: PLW0603

    _init_from_env()

    if level is not None:
        _LOG_LEVEL = level
    if log_format is not None:
        _LOG_FORMAT = log_format
    if log_file is not None:
        _LOG_FILE = log_file
    if debug_scopes is not None:
        _LOG_DEBUG_SCOPES = debug_scopes
    if include_private_ids is not None:
        _LOG_INCLUDE_PRIVATE_IDS = include_private_ids

    root = logging.getLogger()

    # Remove any previously attached handlers/formatters to allow re-runs.
    root.handlers.clear()

    effective_level = _LOG_LEVEL
    formatter: logging.Formatter
    if _LOG_FORMAT == "json":
        formatter = JSONFormatter()
    else:
        formatter = TextFormatter(
            fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )

    if _LOG_FILE:
        handler = logging.FileHandler(_LOG_FILE, encoding="utf-8")
    else:
        handler = logging.StreamHandler()

    handler.setFormatter(formatter)
    root.addHandler(handler)
    root.setLevel(effective_level)

    # Keep httpx / httpcore quiet by default.
    for lib in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(lib).setLevel(logging.WARNING)

    # Install SensitiveLogFilter once.
    has_filter = any(isinstance(f, SensitiveLogFilter) for f in root.filters)
    if not has_filter:
        root.addFilter(SensitiveLogFilter())


def get_log_level() -> int:
    return _LOG_LEVEL


def get_debug_scopes() -> set[str]:
    return _LOG_DEBUG_SCOPES


def is_debug_scope(component: str) -> bool:
    return _is_debug_scope(component)


def include_private_ids() -> bool:
    return _LOG_INCLUDE_PRIVATE_IDS


def new_request_id() -> str:
    """Generate a fresh request/correlation ID (UUID4 short form)."""
    return uuid.uuid4().hex[:16]


def set_request_id(value: str | None) -> contextvars.Token[str | None]:
    """Bind a request ID to the current async task/thread context."""
    return request_id_var.set(value)


def get_request_id() -> str | None:
    return request_id_var.get()


def reset_request_id(token: contextvars.Token[str | None]) -> None:
    request_id_var.reset(token)


def set_run_id(value: str | None) -> contextvars.Token[str | None]:
    """Bind a scheduled-job run ID to the current async task/thread context."""
    return run_id_var.set(value)


def get_run_id() -> str | None:
    return run_id_var.get()


def reset_run_id(token: contextvars.Token[str | None]) -> None:
    run_id_var.reset(token)
