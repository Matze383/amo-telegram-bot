from __future__ import annotations

from amo_bot import main as main_module


class _StopFlow(RuntimeError):
    pass


class _DummyTask:
    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class _DummyLoop:
    def __init__(self) -> None:
        self.task = _DummyTask()
        self.closed = False

    def create_task(self, coro):  # noqa: ANN001
        coro.close()
        return self.task

    def run_until_complete(self, _task) -> None:  # noqa: ANN001
        return None

    def close(self) -> None:
        self.closed = True


class _DummyApp:
    def __init__(self) -> None:
        self.run_calls: list[tuple[str, int, bool | None]] = []

    def run(self, *, host: str, port: int, use_reloader: bool | None = None) -> None:
        self.run_calls.append((host, port, use_reloader))


class _StubDispatcher:
    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002,ANN003
        self.plugin_command_executor = kwargs.get("plugin_command_executor")


def _set_env(monkeypatch, tmp_path, webui_host: str = "127.0.0.1") -> None:
    monkeypatch.setenv("BOT_TOKEN", "123:ABC")
    monkeypatch.setenv("WEBUI_PASSWORD", "secret")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'bot.db'}")
    monkeypatch.setenv("OFFSET_STATE_FILE", str(tmp_path / "offset.json"))
    monkeypatch.setenv("AMO_PLUGIN_DIR", str(tmp_path / "plugins"))
    monkeypatch.setenv("WEBUI_OWNER_TELEGRAM_ID", "")
    monkeypatch.setenv("WEBUI_HOST", webui_host)


def test_run_webui_mode_starts_flask_only(monkeypatch, tmp_path) -> None:
    _set_env(monkeypatch, tmp_path, webui_host="0.0.0.0")
    app = _DummyApp()

    monkeypatch.setattr(main_module, "create_flask_app", lambda **kwargs: app)
    monkeypatch.setattr(main_module, "Dispatcher", _StubDispatcher)

    def _fake_run_polling(*args, **kwargs):  # noqa: ANN002,ANN003
        raise AssertionError("run_polling must not be called in --webui mode")

    monkeypatch.setattr(main_module, "run_polling", _fake_run_polling)

    main_module.run(["--webui"])

    assert app.run_calls == [("0.0.0.0", 8080, None)]


def test_run_serve_mode_starts_webui_and_cancels_polling(monkeypatch, tmp_path) -> None:
    _set_env(monkeypatch, tmp_path, webui_host="0.0.0.0")
    app = _DummyApp()
    loop = _DummyLoop()

    monkeypatch.setattr(main_module, "create_flask_app", lambda **kwargs: app)
    monkeypatch.setattr(main_module, "Dispatcher", _StubDispatcher)
    monkeypatch.setattr(main_module.asyncio, "new_event_loop", lambda: loop)
    monkeypatch.setattr(main_module.asyncio, "set_event_loop", lambda _loop: None)

    def _fake_run_polling(*args, **kwargs):  # noqa: ANN002,ANN003
        raise AssertionError("polling coroutine must not execute during wiring test")

    monkeypatch.setattr(main_module, "run_polling", _fake_run_polling)

    main_module.run(["--serve"])

    assert app.run_calls == [("0.0.0.0", 8080, False)]
    assert loop.task.cancelled is True
    assert loop.closed is True
