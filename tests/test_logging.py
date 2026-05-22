from __future__ import annotations

import logging

from amo_bot.core.logging import SensitiveLogFilter, setup_logging


def test_sensitive_filter_masks_telegram_bot_token_in_message() -> None:
    filt = SensitiveLogFilter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="POST https://api.telegram.org/bot123456:TESTSECRET/sendMessage",
        args=(),
        exc_info=None,
    )

    assert filt.filter(record) is True
    assert "TESTSECRET" not in str(record.msg)
    assert "/bot***REDACTED***/sendMessage" in str(record.msg)


def test_sensitive_filter_masks_telegram_bot_token_in_args_and_exception() -> None:
    filt = SensitiveLogFilter()
    try:
        raise RuntimeError("failed at https://api.telegram.org/bot123456:TESTSECRET/getUpdates")
    except RuntimeError:
        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=2,
            msg="request %s",
            args=("https://api.telegram.org/bot123456:TESTSECRET/getMe",),
            exc_info=True,
        )
        record.exc_info = tuple(record.exc_info) if isinstance(record.exc_info, tuple) else None
        if record.exc_info is None:
            import sys

            record.exc_info = sys.exc_info()

    assert filt.filter(record) is True
    rendered = record.getMessage()
    assert "TESTSECRET" not in rendered
    assert "/bot***REDACTED***/getMe" in rendered
    assert record.exc_info is not None
    assert "TESTSECRET" not in str(record.exc_info[1])


def test_sensitive_filter_masks_common_secret_patterns_in_message_args_exception_and_stack() -> None:
    filt = SensitiveLogFilter()
    try:
        raise RuntimeError("api_key=xyz token=abc password=hunter2 cookie=abc session=xyz sk-verysecretvalue ghp_supersecrettoken")
    except RuntimeError:
        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=3,
            msg="Authorization: Bearer topsecret api_key=alpha token=beta password=gamma cookie=delta session=omega sk-secretprefix ghp_secretprefix %s %s",
            args=("token=from-arg", {"x": "password=from-dict-arg", "safe": "status=ok"}),
            exc_info=True,
        )
        record.exc_info = tuple(record.exc_info) if isinstance(record.exc_info, tuple) else None
        if record.exc_info is None:
            import sys

            record.exc_info = sys.exc_info()
    record.stack_info = "trace: Authorization: Bearer stacksecret token=stacktoken"

    assert filt.filter(record) is True
    rendered = record.getMessage()

    for secret in (
        "topsecret",
        "alpha",
        "beta",
        "gamma",
        "delta",
        "omega",
        "from-arg",
        "from-dict-arg",
        "stacksecret",
        "stacktoken",
        "sk-secretprefix",
        "ghp_secretprefix",
        "sk-verysecretvalue",
        "ghp_supersecrettoken",
    ):
        assert secret not in rendered
        assert secret not in str(record.exc_info[1])
        assert secret not in (record.stack_info or "")

    assert "status=ok" in rendered
    assert "***REDACTED***" in rendered


def test_setup_logging_sets_httpx_httpcore_to_warning_and_installs_filter() -> None:
    setup_logging()
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING
    assert any(isinstance(f, SensitiveLogFilter) for f in logging.getLogger().filters)
