from __future__ import annotations

import asyncio

from amo_bot import main as main_module


class _StopFlow(RuntimeError):
    pass


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
    monkeypatch.setenv("AMO_ENV_OVERRIDE", "0")


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


def test_run_serve_mode_starts_webui_and_runs_polling(monkeypatch, tmp_path) -> None:
    _set_env(monkeypatch, tmp_path, webui_host="0.0.0.0")
    app = _DummyApp()
    threading_calls: list[dict[str, object]] = []

    class _DummyThread:
        def __init__(self, *, target, kwargs, daemon, name) -> None:  # noqa: ANN001
            threading_calls.append(
                {
                    "target": target,
                    "kwargs": kwargs,
                    "daemon": daemon,
                    "name": name,
                    "started": False,
                }
            )
            self._call = threading_calls[-1]

        def start(self) -> None:
            self._call["started"] = True

    polling_call = {"created": False, "awaited": False}

    async def _fake_run_polling(*args, **kwargs):  # noqa: ANN002,ANN003
        polling_call["created"] = True
        polling_call["awaited"] = True

    real_asyncio_run = asyncio.run

    def _fake_asyncio_run(coro):  # noqa: ANN001
        return real_asyncio_run(coro)

    monkeypatch.setattr(main_module, "create_flask_app", lambda **kwargs: app)
    monkeypatch.setattr(main_module, "Dispatcher", _StubDispatcher)
    monkeypatch.setattr(main_module.threading, "Thread", _DummyThread)
    monkeypatch.setattr(main_module, "run_polling", _fake_run_polling)
    monkeypatch.setattr(main_module.asyncio, "run", _fake_asyncio_run)

    main_module.run(["--serve"])

    assert app.run_calls == []
    assert len(threading_calls) == 1
    assert threading_calls[0]["target"].__self__ is app
    assert threading_calls[0]["target"].__name__ == "run"
    assert threading_calls[0]["kwargs"] == {"host": "0.0.0.0", "port": 8080, "use_reloader": False}
    assert threading_calls[0]["daemon"] is True
    assert threading_calls[0]["name"] == "flask-webui"
    assert threading_calls[0]["started"] is True
    assert polling_call == {"created": True, "awaited": True}


def test_run_wires_topic_aware_send_functions(monkeypatch, tmp_path) -> None:
    _set_env(monkeypatch, tmp_path)

    class _DummyTG:
        def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002,ANN003
            self.calls: list[dict[str, object]] = []

        async def send_message(self, **kwargs):  # noqa: ANN003
            self.calls.append(kwargs)
            return {"ok": True}

    captured: dict[str, object] = {}

    class _DummyPCE:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            captured["send_message"] = kwargs["send_message"]
            captured["reply"] = kwargs["reply"]

    class _DummySPE:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            pass

        async def run_due_once(self) -> None:
            return None

    class _DummyDispatcher:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            captured["send_text"] = kwargs["send_text"]

    async def _fake_run_polling(*args, **kwargs):  # noqa: ANN002,ANN003
        raise _StopFlow()

    monkeypatch.setattr(main_module, "TelegramClient", _DummyTG)
    monkeypatch.setattr(main_module, "PluginCommandExecutor", _DummyPCE)
    monkeypatch.setattr(main_module, "ScheduledPluginExecutor", _DummySPE)
    monkeypatch.setattr(main_module, "Dispatcher", _DummyDispatcher)
    monkeypatch.setattr(main_module, "run_polling", _fake_run_polling)

    try:
        main_module.run([])
    except _StopFlow:
        pass

    send_text = captured["send_text"]
    send_message = captured["send_message"]
    reply = captured["reply"]

    asyncio.run(send_text(1, "hello", 872))
    asyncio.run(send_message(2, "world", 951))
    asyncio.run(reply(3, 44, "reply", 123))

    tg = main_module.TelegramClient()
    # retrieve created instance via closure by replaying from captured callables
    # call records are attached to the bound method owner
    owner = send_text.__closure__[0].cell_contents if send_text.__closure__ else None
    if owner is None or not hasattr(owner, "calls"):
        owner = send_message.__closure__[0].cell_contents

    assert owner.calls[0] == {"chat_id": 1, "text": "hello", "message_thread_id": 872}
    assert owner.calls[1] == {"chat_id": 2, "text": "world", "message_thread_id": 951}
    assert owner.calls[2] == {
        "chat_id": 3,
        "text": "reply",
        "reply_to_message_id": 44,
        "message_thread_id": 123,
    }
