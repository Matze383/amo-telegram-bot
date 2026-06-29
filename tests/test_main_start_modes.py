from __future__ import annotations

import asyncio

from amo_bot import main as main_module
from amo_bot.process_control import StopResult


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
    monkeypatch.setenv("WEBUI_SECRET_KEY", "unit-test-webui-secret-key-0123456789abcdef")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'bot.db'}")
    monkeypatch.setenv("OFFSET_STATE_FILE", str(tmp_path / "offset.json"))
    monkeypatch.setenv("BOT_PID_FILE", str(tmp_path / "amo_bot.pid"))
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


def test_run_stop_cli_uses_pid_file_without_starting_runtime(monkeypatch, tmp_path, capsys) -> None:
    _set_env(monkeypatch, tmp_path)
    pid_path = tmp_path / "custom.pid"
    calls: list[str] = []

    monkeypatch.setattr(
        main_module,
        "stop_running_bot",
        lambda pid_file: calls.append(pid_file) or StopResult(True, "stopped"),
    )
    monkeypatch.setattr(
        main_module,
        "init_db",
        lambda database_url: (_ for _ in ()).throw(AssertionError("init_db must not run for --stop")),
    )

    try:
        main_module.run(["--stop", "--pid-file", str(pid_path)])
    except SystemExit as exc:
        assert exc.code == 0
    else:
        raise AssertionError("--stop should exit with SystemExit")

    assert calls == [str(pid_path)]
    assert capsys.readouterr().out.strip() == "stopped"


def test_run_stop_cli_does_not_validate_full_settings(monkeypatch, tmp_path, capsys) -> None:
    pid_path = tmp_path / "amo_bot.pid"
    monkeypatch.setenv("DOTENV_PATH", str(tmp_path / "missing.env"))
    monkeypatch.setenv("BOT_PID_FILE", str(pid_path))

    monkeypatch.setattr(
        main_module,
        "get_settings",
        lambda: (_ for _ in ()).throw(AssertionError("get_settings must not run for --stop")),
    )
    monkeypatch.setattr(
        main_module,
        "stop_running_bot",
        lambda pid_file: StopResult(False, f"checked {pid_file}"),
    )

    try:
        main_module.run(["--stop"])
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("--stop should exit with SystemExit")

    assert capsys.readouterr().out.strip() == f"checked {pid_path}"


def test_run_default_starts_queue_runtime_without_legacy_polling(monkeypatch, tmp_path) -> None:
    _set_env(monkeypatch, tmp_path)
    pid_path = tmp_path / "amo_bot.pid"
    queue_calls: list[str] = []

    async def _fake_run_polling(*args, **kwargs):  # noqa: ANN002,ANN003
        raise AssertionError("legacy run_polling must not be called by default")

    def _fake_run_queue_runtime(settings) -> None:  # noqa: ANN001
        assert pid_path.exists()
        queue_calls.append(settings.database_url)

    monkeypatch.setattr(main_module, "run_polling", _fake_run_polling)
    monkeypatch.setattr(main_module, "_run_queue_runtime", _fake_run_queue_runtime)

    main_module.run([])

    assert queue_calls == [f"sqlite:///{tmp_path / 'bot.db'}"]
    assert not pid_path.exists()


def test_run_serve_mode_starts_webui_and_queue_by_default(monkeypatch, tmp_path) -> None:
    _set_env(monkeypatch, tmp_path, webui_host="0.0.0.0")
    pid_path = tmp_path / "amo_bot.pid"
    app = _DummyApp()
    queue_calls: list[str] = []
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

    async def _fake_run_polling(*args, **kwargs):  # noqa: ANN002,ANN003
        raise AssertionError("legacy run_polling must not be called by --serve default")

    def _fake_run_queue_runtime(settings) -> None:  # noqa: ANN001
        assert pid_path.exists()
        queue_calls.append(settings.database_url)

    monkeypatch.setattr(main_module, "create_flask_app", lambda **kwargs: app)
    monkeypatch.setattr(main_module, "Dispatcher", _StubDispatcher)
    monkeypatch.setattr(main_module.threading, "Thread", _DummyThread)
    monkeypatch.setattr(main_module, "run_polling", _fake_run_polling)
    monkeypatch.setattr(main_module, "_run_queue_runtime", _fake_run_queue_runtime)

    main_module.run(["--serve"])

    assert app.run_calls == []
    assert len(threading_calls) == 1
    assert threading_calls[0]["target"].__self__ is app
    assert threading_calls[0]["target"].__name__ == "run"
    assert threading_calls[0]["kwargs"] == {"host": "0.0.0.0", "port": 8080, "use_reloader": False}
    assert threading_calls[0]["daemon"] is True
    assert threading_calls[0]["name"] == "flask-webui"
    assert threading_calls[0]["started"] is True
    assert queue_calls == [f"sqlite:///{tmp_path / 'bot.db'}"]
    assert not pid_path.exists()


def test_run_queue_runtime_env_starts_supervisor_without_legacy_polling(monkeypatch, tmp_path) -> None:
    _set_env(monkeypatch, tmp_path)
    monkeypatch.setenv("AMO_TELEGRAM_RUNTIME", "queue")
    pid_path = tmp_path / "amo_bot.pid"
    queue_calls: list[str] = []

    class _DummyDispatcher:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            pass

    async def _fake_run_polling(*args, **kwargs):  # noqa: ANN002,ANN003
        raise AssertionError("legacy run_polling must not be called in queue runtime")

    monkeypatch.setattr(main_module, "Dispatcher", _DummyDispatcher)
    monkeypatch.setattr(main_module, "run_polling", _fake_run_polling)
    monkeypatch.setattr(main_module, "_run_queue_runtime", lambda settings: queue_calls.append(settings.database_url))

    main_module.run([])

    assert queue_calls == [f"sqlite:///{tmp_path / 'bot.db'}"]
    assert not pid_path.exists()


def test_run_queue_runtime_cli_overrides_polling_env(monkeypatch, tmp_path) -> None:
    _set_env(monkeypatch, tmp_path)
    monkeypatch.setenv("AMO_TELEGRAM_RUNTIME", "polling")
    queue_calls: list[str] = []

    class _DummyDispatcher:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            pass

    async def _fake_run_polling(*args, **kwargs):  # noqa: ANN002,ANN003
        raise AssertionError("legacy run_polling must not be called when --runtime queue is explicit")

    monkeypatch.setattr(main_module, "Dispatcher", _DummyDispatcher)
    monkeypatch.setattr(main_module, "run_polling", _fake_run_polling)
    monkeypatch.setattr(main_module, "_run_queue_runtime", lambda settings: queue_calls.append(settings.amo_telegram_runtime))

    main_module.run(["--runtime", "queue"])

    assert queue_calls == ["polling"]


def test_run_serve_queue_runtime_starts_webui_and_supervisor_without_legacy_polling(monkeypatch, tmp_path) -> None:
    _set_env(monkeypatch, tmp_path, webui_host="0.0.0.0")
    pid_path = tmp_path / "amo_bot.pid"
    app = _DummyApp()
    queue_calls: list[str] = []
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

    async def _fake_run_polling(*args, **kwargs):  # noqa: ANN002,ANN003
        raise AssertionError("legacy run_polling must not be called for --serve --runtime queue")

    def _fake_run_queue_runtime(settings) -> None:  # noqa: ANN001
        assert pid_path.exists()
        queue_calls.append(settings.database_url)

    monkeypatch.setattr(main_module, "create_flask_app", lambda **kwargs: app)
    monkeypatch.setattr(main_module.threading, "Thread", _DummyThread)
    monkeypatch.setattr(main_module, "run_polling", _fake_run_polling)
    monkeypatch.setattr(main_module, "_run_queue_runtime", _fake_run_queue_runtime)

    main_module.run(["--serve", "--runtime", "queue"])

    assert app.run_calls == []
    assert len(threading_calls) == 1
    assert threading_calls[0]["target"].__self__ is app
    assert threading_calls[0]["target"].__name__ == "run"
    assert threading_calls[0]["kwargs"] == {"host": "0.0.0.0", "port": 8080, "use_reloader": False}
    assert threading_calls[0]["daemon"] is True
    assert threading_calls[0]["name"] == "flask-webui"
    assert threading_calls[0]["started"] is True
    assert queue_calls == [f"sqlite:///{tmp_path / 'bot.db'}"]
    assert not pid_path.exists()


def test_run_dreaming_starts_inside_polling_event_loop(monkeypatch, tmp_path) -> None:
    _set_env(monkeypatch, tmp_path)
    monkeypatch.setenv("DREAMING_ENABLED", "1")
    pid_path = tmp_path / "amo_bot.pid"

    lifecycle: list[tuple[str, bool]] = []

    class _DummyDreamingRuntime:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            self.is_running = False
            lifecycle.append(("init", kwargs["enabled"]))

        def start(self) -> None:
            asyncio.get_running_loop()
            self.is_running = True
            lifecycle.append(("start", True))

        async def stop(self) -> None:
            self.is_running = False
            lifecycle.append(("stop", False))

    class _DummyDispatcher:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            pass

    async def _fake_run_polling(*args, **kwargs):  # noqa: ANN002,ANN003
        assert pid_path.exists()
        lifecycle.append(("polling", True))
        raise _StopFlow()

    monkeypatch.setattr(main_module, "DreamingRuntime", _DummyDreamingRuntime)
    monkeypatch.setattr(main_module, "Dispatcher", _DummyDispatcher)
    monkeypatch.setattr(main_module, "run_polling", _fake_run_polling)

    try:
        main_module.run(["--runtime", "polling"])
    except _StopFlow:
        pass

    assert lifecycle == [("init", True), ("start", True), ("polling", True), ("stop", False)]
    assert not pid_path.exists()


def test_run_webui_mode_does_not_start_dreaming(monkeypatch, tmp_path) -> None:
    _set_env(monkeypatch, tmp_path)
    monkeypatch.setenv("DREAMING_ENABLED", "1")
    app = _DummyApp()
    lifecycle: list[str] = []

    class _DummyDreamingRuntime:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            lifecycle.append("init")

        def start(self) -> None:
            lifecycle.append("start")

        async def stop(self) -> None:
            lifecycle.append("stop")

    monkeypatch.setattr(main_module, "create_flask_app", lambda **kwargs: app)
    monkeypatch.setattr(main_module, "Dispatcher", _StubDispatcher)
    monkeypatch.setattr(main_module, "DreamingRuntime", _DummyDreamingRuntime)

    main_module.run(["--webui"])

    assert lifecycle == ["init"]
    assert app.run_calls == [("127.0.0.1", 8080, None)]


def test_run_wires_dispatcher_with_non_none_ai_service(monkeypatch, tmp_path) -> None:
    _set_env(monkeypatch, tmp_path)

    captured: dict[str, object] = {}

    class _DummyDispatcher:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            captured["ai_service"] = kwargs.get("ai_service")

    async def _fake_run_polling(*args, **kwargs):  # noqa: ANN002,ANN003
        raise _StopFlow()

    monkeypatch.setattr(main_module, "Dispatcher", _DummyDispatcher)
    monkeypatch.setattr(main_module, "run_polling", _fake_run_polling)

    try:
        main_module.run(["--runtime", "polling"])
    except _StopFlow:
        pass

    assert "ai_service" in captured
    assert captured["ai_service"] is not None


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
        main_module.run(["--runtime", "polling"])
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


def test_run_wires_plugin_command_executor_reply_markup(monkeypatch, tmp_path) -> None:
    _set_env(monkeypatch, tmp_path)

    captured: dict[str, object] = {}

    class _DummyPCE:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            captured["reply_markup"] = kwargs.get("reply_markup")

    class _DummySPE:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            pass

        async def run_due_once(self) -> None:
            return None

    class _DummyDispatcher:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            pass

    async def _fake_run_polling(*args, **kwargs):  # noqa: ANN002,ANN003
        raise _StopFlow()

    monkeypatch.setattr(main_module, "PluginCommandExecutor", _DummyPCE)
    monkeypatch.setattr(main_module, "ScheduledPluginExecutor", _DummySPE)
    monkeypatch.setattr(main_module, "Dispatcher", _DummyDispatcher)
    monkeypatch.setattr(main_module, "run_polling", _fake_run_polling)

    try:
        main_module.run(["--runtime", "polling"])
    except _StopFlow:
        pass

    assert "reply_markup" in captured
    assert captured["reply_markup"] is not None
