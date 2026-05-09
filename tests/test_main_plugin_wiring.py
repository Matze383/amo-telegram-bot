from __future__ import annotations

from amo_bot import main as main_module


class _StopRunPolling(RuntimeError):
    pass


def test_main_wires_plugin_command_executor_into_dispatcher(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "bot.db"
    offset_path = tmp_path / "offset.json"

    monkeypatch.setenv("BOT_TOKEN", "123:ABC")
    monkeypatch.setenv("WEBUI_PASSWORD", "secret")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("OFFSET_STATE_FILE", str(offset_path))
    monkeypatch.setenv("AMO_PLUGIN_DIR", str(tmp_path / "plugins"))

    captured: dict[str, object] = {}

    def _fake_run_polling(tg, offset_store, *, timeout_seconds, limit, retry_max_seconds, dispatcher):
        captured["dispatcher"] = dispatcher
        raise _StopRunPolling()

    monkeypatch.setattr(main_module, "run_polling", _fake_run_polling)

    try:
        main_module.run()
    except _StopRunPolling:
        pass

    dispatcher = captured.get("dispatcher")
    assert dispatcher is not None
    assert dispatcher.plugin_command_executor is not None
