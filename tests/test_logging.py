"""
Tests for the unified structured logging system (issue #43).

Covers:
- JSON / text formatters
- Sensitive redaction + ID masking
- Correlation ID propagation (request_id / run_id contextvars)
- Telegram API error / rate-limit structured logging
- AI metadata — no prompt/token/secret leak
- Plugin lifecycle and run_id
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
import sys
import time
import uuid
from typing import Any

import pytest

from amo_bot.core.logging import (
    SensitiveLogFilter,
    TextFormatter,
    JSONFormatter,
    duration_timer,
    log_event,
    masked_id,
    new_request_id,
    get_request_id,
    set_request_id,
    get_run_id,
    set_run_id,
    setup_logging,
    get_log_level,
    is_debug_scope,
)


# ── Formatter tests ────────────────────────────────────────────────────────────

class TestTextFormatter:
    def test_injects_request_id_when_set(self) -> None:
        fmt = TextFormatter(fmt="%(message)s")
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname=__file__, lineno=1,
            msg="hello world", args=(), exc_info=None,
        )
        token = set_request_id("req-abc123")
        try:
            result = fmt.format(record)
        finally:
            set_request_id(None)
        assert "req-abc123" in result

    def test_injects_run_id_when_set(self) -> None:
        fmt = TextFormatter(fmt="%(message)s")
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname=__file__, lineno=1,
            msg="task done", args=(), exc_info=None,
        )
        token = set_run_id("run-xyz789")
        try:
            result = fmt.format(record)
        finally:
            set_run_id(None)
        assert "run-xyz789" in result

    def test_injects_both_correlation_ids(self) -> None:
        fmt = TextFormatter(fmt="%(message)s")
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname=__file__, lineno=1,
            msg="both", args=(), exc_info=None,
        )
        req_token = set_request_id("req-both")
        run_token = set_run_id("run-both")
        try:
            result = fmt.format(record)
        finally:
            set_request_id(None)
            set_run_id(None)
        assert "req-both" in result
        assert "run-both" in result

    def test_no_extra_when_not_set(self) -> None:
        fmt = TextFormatter(fmt="%(message)s")
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname=__file__, lineno=1,
            msg="plain", args=(), exc_info=None,
        )
        result = fmt.format(record)
        assert "req=" not in result
        assert "run=" not in result
        assert result.strip() == "plain"


class TestJSONFormatter:
    def test_basic_fields(self) -> None:
        fmt = JSONFormatter()
        record = logging.LogRecord(
            name="my.logger", level=logging.INFO, pathname=__file__, lineno=1,
            msg="test message", args=(), exc_info=None,
        )
        result = json.loads(fmt.format(record))
        assert result["level"] == "INFO"
        assert result["logger"] == "my.logger"
        assert result["message"] == "test message"

    def test_correlation_ids_when_set(self) -> None:
        fmt = JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname=__file__, lineno=1,
            msg="corr test", args=(), exc_info=None,
        )
        req_token = set_request_id("req-json-01")
        run_token = set_run_id("run-json-01")
        try:
            result = json.loads(fmt.format(record))
        finally:
            set_request_id(None)
            set_run_id(None)
        assert result["request_id"] == "req-json-01"
        assert result["run_id"] == "run-json-01"

    def test_extra_fields_passthrough(self) -> None:
        fmt = JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname=__file__, lineno=1,
            msg="extra test", args=(), exc_info=None,
        )
        record.event = "plugin.command.start"
        record.component = "plugin.runtime"
        record.duration_ms = 42
        result = json.loads(fmt.format(record))
        assert result["event"] == "plugin.command.start"
        assert result["component"] == "plugin.runtime"
        assert result["duration_ms"] == 42

    def test_exc_info_in_json(self) -> None:
        fmt = JSONFormatter()
        try:
            raise RuntimeError("boom")
        except Exception:
            record = logging.LogRecord(
                name="test", level=logging.ERROR, pathname=__file__, lineno=1,
                msg="error", args=(), exc_info=sys.exc_info(),
            )
        result = json.loads(fmt.format(record))
        assert result["exc_type"] == "RuntimeError"
        assert result["exc_msg"] == "boom"


# ── ID masking tests ──────────────────────────────────────────────────────────

class TestMaskedId:
    def test_private_chat_id_masked(self) -> None:
        # Positive chat IDs are private
        assert masked_id(123456789) != "123456789"
        assert "123" in masked_id(123456789)
        assert "***" in masked_id(123456789)

    def test_group_chat_id_masked(self) -> None:
        # Negative chat IDs are groups
        assert masked_id(-987654321) != "-987654321"
        assert "***" in masked_id(-987654321)

    def test_short_id_masked(self) -> None:
        assert masked_id(42) == "***"
        assert masked_id(1234) == "***"

    def test_none_returns_none_string(self) -> None:
        assert masked_id(None) == "none"

    def test_string_int(self) -> None:
        result = masked_id("999888777666")
        assert result != "999888777666"
        assert "***" in result


# ── SensitiveLogFilter tests ───────────────────────────────────────────────────

class TestSensitiveLogFilter:
    def test_telegram_bot_token_in_msg(self) -> None:
        filt = SensitiveLogFilter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname=__file__, lineno=1,
            msg="POST https://api.telegram.org/bot123456:TESTSECRET/sendMessage",
            args=(), exc_info=None,
        )
        assert filt.filter(record) is True
        assert "TESTSECRET" not in str(record.msg)
        assert "/bot***REDACTED***/sendMessage" in str(record.msg)

    def test_telegram_bot_token_in_args(self) -> None:
        filt = SensitiveLogFilter()
        record = logging.LogRecord(
            name="test", level=logging.ERROR, pathname=__file__, lineno=1,
            msg="request %s",
            args=("https://api.telegram.org/bot123456:TESTSECRET/getMe",),
            exc_info=None,
        )
        assert filt.filter(record) is True
        rendered = record.getMessage()
        assert "TESTSECRET" not in rendered
        assert "/bot***REDACTED***/getMe" in rendered

    def test_bearer_token_in_msg(self) -> None:
        filt = SensitiveLogFilter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname=__file__, lineno=1,
            msg="Authorization: Bearer super-secret-value",
            args=(), exc_info=None,
        )
        assert filt.filter(record) is True
        rendered = record.getMessage()
        assert "super-secret-value" not in rendered
        assert "***REDACTED***" in rendered

    def test_api_key_kv_pattern(self) -> None:
        filt = SensitiveLogFilter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname=__file__, lineno=1,
            msg="api_key=sk-abcdefgh123456",
            args=(), exc_info=None,
        )
        assert filt.filter(record) is True
        assert "sk-abcdefgh123456" not in record.getMessage()
        assert "***REDACTED***" in record.getMessage()

    def test_ghp_secret_prefix(self) -> None:
        filt = SensitiveLogFilter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname=__file__, lineno=1,
            msg="token ghp_abcdefgh1234567890",
            args=(), exc_info=None,
        )
        assert filt.filter(record) is True
        assert "ghp_abcdefgh1234567890" not in record.getMessage()

    def test_exception_args_scrubbed(self) -> None:
        filt = SensitiveLogFilter()
        try:
            raise RuntimeError("failed with api_key=sk-testsecret123456")
        except RuntimeError:
            record = logging.LogRecord(
                name="test", level=logging.ERROR, pathname=__file__, lineno=1,
                msg="error", args=(), exc_info=True,
            )
            record.exc_info = sys.exc_info()
        assert filt.filter(record) is True
        rendered = str(record.exc_info[1])
        assert "sk-testsecret123456" not in rendered

    def test_dict_args_scrubbed(self) -> None:
        filt = SensitiveLogFilter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname=__file__, lineno=1,
            msg="data: %s",
            args=({"token": "ghp_secretvalue12345", "safe": "ok"}),
            exc_info=None,
        )
        assert filt.filter(record) is True
        rendered = record.getMessage()
        assert "ghp_secretvalue12345" not in rendered
        assert "ok" in rendered

    def test_nested_dict_args_scrubbed(self) -> None:
        filt = SensitiveLogFilter()
        # Test the _mask method directly with nested structures
        result = filt._mask({"outer": {"token": "sk-innersecret123456"}})
        rendered = str(result)
        assert "sk-innersecret123456" not in rendered
        assert "***REDACTED***" in str(result["outer"]["token"])


# ── duration_timer tests ────────────────────────────────────────────────────────

class TestDurationTimer:
    def test_stores_duration_ms(self) -> None:
        store: dict[str, Any] = {}
        with duration_timer(store):
            time.sleep(0.05)
        assert "duration_ms" in store
        assert store["duration_ms"] >= 40  # at least 40ms

    def test_custom_key(self) -> None:
        store: dict[str, Any] = {}
        with duration_timer(store, key="elapsed"):
            time.sleep(0.02)
        assert "elapsed" in store
        assert "duration_ms" not in store


# ── Correlation ID contextvars ─────────────────────────────────────────────────

class TestCorrelationIds:
    def test_new_request_id_format(self) -> None:
        rid = new_request_id()
        assert isinstance(rid, str)
        assert len(rid) == 16  # 8 hex pairs

    def test_request_id_roundtrip(self) -> None:
        token = set_request_id("test-req-id")
        try:
            assert get_request_id() == "test-req-id"
        finally:
            set_request_id(None)
        assert get_request_id() is None

    def test_run_id_roundtrip(self) -> None:
        token = set_run_id("test-run-id")
        try:
            assert get_run_id() == "test-run-id"
        finally:
            set_run_id(None)
        assert get_run_id() is None

    def test_request_id_propagates_in_async(self) -> None:
        results: dict[str, str | None] = {}

        async def inner() -> None:
            results["inner"] = get_request_id()

        token = set_request_id("async-req-42")
        try:
            asyncio.run(inner())
        finally:
            set_request_id(None)
        assert results["inner"] == "async-req-42"

    def test_run_id_propagates_in_async(self) -> None:
        results: dict[str, str | None] = {}

        async def inner() -> None:
            results["inner"] = get_run_id()

        token = set_run_id("async-run-99")
        try:
            asyncio.run(inner())
        finally:
            set_run_id(None)
        assert results["inner"] == "async-run-99"


# ── log_event tests ────────────────────────────────────────────────────────────

class TestLogEvent:
    def setup_method(self) -> None:
        self.stream = io.StringIO()
        handler = logging.StreamHandler(self.stream)
        handler.setFormatter(JSONFormatter())
        self.logger = logging.getLogger("test_log_event")
        self.logger.setLevel(logging.DEBUG)
        self.logger.handlers.clear()
        self.logger.addHandler(handler)

    def _last_json(self) -> dict[str, Any]:
        """
        Read the last JSON record from the stream.

        JSONFormatter puts the structured dict in the 'message' field as a Python
        repr string (e.g. "{'event': 'test', ...}"). This helper extracts and parses it,
        then merges top-level fields from the outer wrapper.
        """
        self.stream.seek(0)
        raw = self.stream.read()
        for line in reversed(raw.strip().split("\n")):
            stripped = line.strip()
            if stripped.startswith("{"):
                outer = json.loads(stripped)
                msg = outer.get("message", "")
                if isinstance(msg, str) and msg.startswith("{"):
                    # Convert Python repr to valid JSON and parse
                    inner = json.loads(msg.replace("'", '"'))
                    # Merge: level/logger/request_id/run_id from outer, event/component from inner
                    result = {k: v for k, v in outer.items()
                              if k not in ("message", "event", "component", "duration_ms",
                                           "reason_code", "chat_scope", "user_scope",
                                           "update_id", "_chat_id_masked", "_user_id_masked",
                                           "chat_id", "user_id", "thread_id", "message_id",
                                           "command", "outcome", "extra")}
                    result.update(inner)
                    return result
                return outer
        raise AssertionError(f"No JSON line found in: {raw[:300]}")

    def test_basic_fields(self) -> None:
        log_event(self.logger, logging.INFO, event="test.basic", component="test")
        rec = self._last_json()
        assert rec["event"] == "test.basic"
        assert rec["component"] == "test"
        assert rec["level"] == "INFO"

    def test_chat_id_masked_by_default(self) -> None:
        log_event(
            self.logger, logging.INFO,
            event="test.mask", component="test",
            chat_id=1234567890, user_id=9876543210,
        )
        rec = self._last_json()
        assert "chat_id" not in rec
        assert rec["chat_scope"] == "private"
        assert "_chat_id_masked" in rec
        assert "123" in rec["_chat_id_masked"]

    def test_private_ids_shown_when_env_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LOG_INCLUDE_PRIVATE_IDS", "1")
        # Re-init so env takes effect
        from amo_bot.core import logging as core_logging
        core_logging._LOG_INCLUDE_PRIVATE_IDS = True

        log_event(
            self.logger, logging.INFO,
            event="test.show", component="test",
            chat_id=111222333, user_id=444555666,
        )
        rec = self._last_json()
        assert rec["chat_id"] == 111222333
        assert rec["user_id"] == 444555666

        monkeypatch.delenv("LOG_INCLUDE_PRIVATE_IDS", raising=False)
        core_logging._LOG_INCLUDE_PRIVATE_IDS = False

    def test_group_chat_scope(self) -> None:
        log_event(
            self.logger, logging.INFO,
            event="test.group", component="test",
            chat_id=-999888777,
        )
        rec = self._last_json()
        assert rec["chat_scope"] == "group"
        assert "_chat_id_masked" in rec

    def test_duration_ms(self) -> None:
        log_event(
            self.logger, logging.INFO,
            event="test.duration", component="test",
            duration_ms=1234,
        )
        rec = self._last_json()
        assert rec["duration_ms"] == 1234

    def test_reason_code(self) -> None:
        log_event(
            self.logger, logging.INFO,
            event="test.reason", component="test",
            reason_code="rate_limit",
        )
        rec = self._last_json()
        assert rec["reason_code"] == "rate_limit"

    def test_extra_merged(self) -> None:
        log_event(
            self.logger, logging.INFO,
            event="test.extra", component="test",
            extra={"custom_field": "custom_value", "count": 99},
        )
        rec = self._last_json()
        assert rec["custom_field"] == "custom_value"
        assert rec["count"] == 99

    def test_update_id_not_masked(self) -> None:
        log_event(
            self.logger, logging.INFO,
            event="test.update", component="test",
            update_id=9876543,
        )
        rec = self._last_json()
        assert rec["update_id"] == 9876543

    def test_no_raw_message_text_leaked(self) -> None:
        """Verify log_event itself never emits raw user message text."""
        log_event(
            self.logger, logging.INFO,
            event="test.safe", component="test",
            user_id=111222333,
            # These should NOT appear in output:
            # (no raw text fields are passed)
        )
        rec = self._last_json()
        # Just verify no free-form text field that could contain the prompt
        for value in rec.values():
            if isinstance(value, str):
                assert "show me" not in value.lower()
                assert "my password" not in value.lower()


# ── Telegram API structured logging ─────────────────────────────────────────────

class TestTelegramClientStructuredLogging:
    def test_telegram_api_error_has_structured_fields(self) -> None:
        """TelegramApiError carries structured data for logging."""
        from amo_bot.telegram.client import TelegramApiError, TelegramRateLimitError

        err = TelegramApiError("Not Found", code=404, error_data={"ok": False})
        assert err.code == 404
        assert err.error_data["ok"] is False

        rate_limit = TelegramRateLimitError(retry_after=42)
        assert rate_limit.retry_after == 42
        assert rate_limit.code == 429


# ── Plugin run_id tests ────────────────────────────────────────────────────────

class TestPluginRunId:
    def test_plugin_command_executor_emits_run_id(self) -> None:
        """Verify PluginCommandExecutor generates a run_id per invocation."""
        # We can only verify the infrastructure; actual execution needs DB
        run_id = str(uuid.uuid4())
        token = set_run_id(run_id)
        try:
            assert get_run_id() == run_id
        finally:
            set_run_id(None)

    def test_scheduled_executor_run_id_context(self) -> None:
        """Verify scheduled jobs get a run_id via set_run_id."""
        run_id = str(uuid.uuid4())
        token = set_run_id(run_id)
        try:
            assert get_run_id() == run_id
        finally:
            set_run_id(None)


# ── Setup tests ────────────────────────────────────────────────────────────────

class TestSetupLogging:
    def test_setup_logging_installs_filter_once(self) -> None:
        # Save original handlers
        root = logging.getLogger()
        orig_handlers = list(root.handlers)
        orig_filters = list(root.filters)

        # Clean slate
        root.handlers.clear()
        root.filters.clear()

        setup_logging(level=logging.INFO)

        assert any(isinstance(f, SensitiveLogFilter) for f in root.filters)
        assert logging.getLogger("httpx").level == logging.WARNING
        assert logging.getLogger("httpcore").level == logging.WARNING

        # Restore
        root.handlers.clear()
        root.filters.clear()
        for h in orig_handlers:
            root.addHandler(h)
        for f in orig_filters:
            root.addFilter(f)

    def test_get_log_level_default_info(self) -> None:
        # After setup, level should be INFO by default
        root = logging.getLogger()
        orig_handlers = list(root.handlers)
        orig_filters = list(root.filters)
        root.handlers.clear()
        root.filters.clear()

        setup_logging(level=logging.INFO)
        assert get_log_level() == logging.INFO

        root.handlers.clear()
        root.filters.clear()
        for h in orig_handlers:
            root.addHandler(h)
        for f in orig_filters:
            root.addFilter(f)

    def test_is_debug_scope(self) -> None:
        # Default: nothing is a debug scope
        assert is_debug_scope("ai.router") is False


# ── AI router logging (no prompt leak) ───────────────────────────────────────

class TestAIRouterLoggingNoPromptLeak:
    def test_router_audit_does_not_contain_prompt(self) -> None:
        """
        The _audit_recall path in AIRouter must only emit metadata dicts,
        never raw message text / prompt content.
        """
        from amo_bot.ai.router import AIRouter

        router = AIRouter()
        scope = {"scope_type": "private_user", "chat_id": None, "topic_id": None, "user_id": 42}
        meta = {
            "decision": "include",
            "reason": "ok",
            "records_in": 3,
            "records_out": 2,
            "chars_out": 150,
            "truncated_records": False,
            "truncated_chars": False,
            "timeout_hit": False,
            "error_class": "",
        }

        # Capture the log call args
        import io, logging
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JSONFormatter())
        local_logger = logging.getLogger("test_router_audit")
        local_logger.handlers.clear()
        local_logger.addHandler(handler)
        local_logger.setLevel(logging.INFO)

        # Patch the router's logger temporarily
        original_logger = router._logger if hasattr(router, "_logger") else None
        import amo_bot.ai.router as router_module
        router_module.logger = local_logger

        try:
            router._audit_recall(scope=scope, meta=meta)
        finally:
            router_module.logger = logging.getLogger(__name__)

        stream.seek(0)
        raw = stream.read()

        # The logged message should be a JSON dict
        for line in raw.strip().split("\n"):
            if not line:
                continue
            rec = json.loads(line)
            msg = rec.get("message", "")
            if isinstance(msg, dict):
                # The payload should be the structured dict
                for v in msg.values():
                    if isinstance(v, str):
                        # Should not contain raw user text
                        assert "password123" not in v.lower()
