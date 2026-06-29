from __future__ import annotations

from amo_bot import main as main_module


class _StopRunPolling(RuntimeError):
    pass


def test_main_wires_plugin_command_executor_into_dispatcher(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "bot.db"
    offset_path = tmp_path / "offset.json"

    monkeypatch.setenv("BOT_TOKEN", "123:ABC")
    monkeypatch.setenv("WEBUI_PASSWORD", "secret")
    monkeypatch.setenv("WEBUI_SECRET_KEY", "unit-test-webui-secret-key-0123456789abcdef")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("OFFSET_STATE_FILE", str(offset_path))
    monkeypatch.setenv("AMO_PLUGIN_DIR", str(tmp_path / "plugins"))
    monkeypatch.setenv("AMO_ENV_OVERRIDE", "0")
    monkeypatch.setenv("WEBUI_OWNER_TELEGRAM_ID", "")
    monkeypatch.setenv("WEBUI_LOGIN_DELAY_BASE_SECONDS", "0.25")
    monkeypatch.setenv("WEBUI_LOGIN_DELAY_MAX_SECONDS", "1.0")

    captured: dict[str, object] = {}

    def _fake_run_polling(
        tg,
        offset_store,
        *,
        timeout_seconds,
        limit,
        retry_max_seconds,
        dispatcher,
        scheduled_tick,
        scheduled_tick_interval_seconds=5.0,
    ):
        captured["dispatcher"] = dispatcher
        captured["scheduled_tick"] = scheduled_tick
        captured["scheduled_tick_interval_seconds"] = scheduled_tick_interval_seconds
        raise _StopRunPolling()

    monkeypatch.setattr(main_module, "run_polling", _fake_run_polling)

    try:
        main_module.run(["--runtime", "polling"])
    except _StopRunPolling:
        pass

    dispatcher = captured.get("dispatcher")
    assert dispatcher is not None
    assert dispatcher.plugin_command_executor is not None
    assert callable(captured.get("scheduled_tick"))
