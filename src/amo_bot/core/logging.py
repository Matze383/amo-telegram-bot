import logging
import re
from typing import Any

_TELEGRAM_BOT_URL_RE = re.compile(r"(/bot)([^/\s]+)(/)")
_REDACTED_BOT_SEGMENT = r"\1***REDACTED***\3"

_BEARER_RE = re.compile(r"(?i)(\bAuthorization\s*:\s*Bearer\s+)([^\s,;]+)")
_KEYVALUE_SECRET_RE = re.compile(
    r"(?i)(\b(?:api_key|token|password|cookie|session)\b\s*[:=]\s*)([^\s,;]+)"
)
_PREFIX_SECRET_RE = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{8,}|ghp_[A-Za-z0-9]{8,})\b")


def _mask_sensitive_text(value: str) -> str:
    redacted = _TELEGRAM_BOT_URL_RE.sub(_REDACTED_BOT_SEGMENT, value)
    redacted = _BEARER_RE.sub(r"\1***REDACTED***", redacted)
    redacted = _KEYVALUE_SECRET_RE.sub(r"\1***REDACTED***", redacted)
    redacted = _PREFIX_SECRET_RE.sub("***REDACTED***", redacted)
    return redacted


class SensitiveLogFilter(logging.Filter):
    """Mask known secret-bearing URL segments in log records."""

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


def setup_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    root_logger = logging.getLogger()
    has_filter = any(isinstance(f, SensitiveLogFilter) for f in root_logger.filters)
    if not has_filter:
        root_logger.addFilter(SensitiveLogFilter())

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
