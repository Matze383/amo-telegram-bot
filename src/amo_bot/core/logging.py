import logging
import re
from typing import Any

_TELEGRAM_BOT_URL_RE = re.compile(r"(/bot)([^/\s]+)(/)")
_REDACTED_BOT_SEGMENT = r"\1***REDACTED***\3"


def _mask_sensitive_text(value: str) -> str:
    return _TELEGRAM_BOT_URL_RE.sub(_REDACTED_BOT_SEGMENT, value)


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
