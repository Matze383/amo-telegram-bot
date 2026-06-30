from __future__ import annotations

import asyncio

import pytest

from amo_bot import main as main_module
from amo_bot.process_control import StopResult


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
    monkeypatch.setenv("DOTENV_PATH", str(tmp_path / "missing.env"))
    monkeypatch.setenv("WEBUI_LOGIN_DELAY_BASE_SECONDS", "0.25")
    monkeypatch.setenv("WEBUI_LOGIN_DELAY_MAX_SECONDS", "2.0")
    monkeypatch.setenv("AMO_ENV_OVERRIDE", "0")
    monkeypatch.delenv("AMO_TELEGRAM_RUNTIME", raising=False)


def test_run_webui_mode_starts_flask_only(monkeypatch, tmp_path) -> None:
    _set_env(monkeypatch, tmp_path, webui_host="0.0.0.0")
    app = _DummyApp()

    monkeypatch.setattr(main_module, "create_flask_app", lambda **kwargs: app)
    monkeypatch.setattr(main_module, "Dispatcher", _StubDispatcher)

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


def test_run_default_starts_queue_runtime(monkeypatch, tmp_path) -> None:
    _set_env(monkeypatch, tmp_path)
    pid_path = tmp_path / "amo_bot.pid"
    queue_calls: list[str] = []

    def _fake_run_queue_runtime(settings) -> None:  # noqa: ANN001
        assert pid_path.exists()
        queue_calls.append(settings.database_url)

    monkeypatch.setattr(main_module, "_run_queue_runtime", _fake_run_queue_runtime)

    main_module.run([])

    assert queue_calls == [f"sqlite:///{tmp_path / 'bot.db'}"]
    assert not pid_path.exists()


@pytest.mark.parametrize("runtime_value", ["polling", "invalid"])
def test_run_rejects_legacy_runtime_before_queue_start(monkeypatch, tmp_path, runtime_value: str) -> None:
    _set_env(monkeypatch, tmp_path)
    monkeypatch.setenv("AMO_TELEGRAM_RUNTIME", runtime_value)

    monkeypatch.setattr(
        main_module,
        "_run_queue_runtime",
        lambda settings: (_ for _ in ()).throw(AssertionError("queue runtime must not start")),  # noqa: ARG005
    )
    monkeypatch.setattr(
        main_module,
        "init_db",
        lambda database_url: (_ for _ in ()).throw(AssertionError("init_db must not run before runtime check")),  # noqa: ARG005
    )

    with pytest.raises(RuntimeError) as exc_info:
        main_module.run([])

    message = str(exc_info.value)
    assert "legacy polling runtime removed" in message
    assert "only queue supported" in message
    assert "remove variable or set queue" in message


@pytest.mark.parametrize("runtime_value", ["queue", "", "   ", None])
def test_run_allows_queue_or_empty_runtime_env(monkeypatch, tmp_path, runtime_value: str | None) -> None:
    _set_env(monkeypatch, tmp_path)
    queue_calls: list[str] = []

    if runtime_value is None:
        monkeypatch.delenv("AMO_TELEGRAM_RUNTIME", raising=False)
    else:
        monkeypatch.setenv("AMO_TELEGRAM_RUNTIME", runtime_value)

    monkeypatch.setattr(main_module, "_run_queue_runtime", lambda settings: queue_calls.append(settings.database_url))

    main_module.run([])

    assert queue_calls == [f"sqlite:///{tmp_path / 'bot.db'}"]


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

    def _fake_run_queue_runtime(settings) -> None:  # noqa: ANN001
        assert pid_path.exists()
        queue_calls.append(settings.database_url)

    monkeypatch.setattr(main_module, "create_flask_app", lambda **kwargs: app)
    monkeypatch.setattr(main_module, "Dispatcher", _StubDispatcher)
    monkeypatch.setattr(main_module.threading, "Thread", _DummyThread)
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


def test_run_serve_queue_runtime_starts_webui_and_supervisor(monkeypatch, tmp_path) -> None:
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

    def _fake_run_queue_runtime(settings) -> None:  # noqa: ANN001
        assert pid_path.exists()
        queue_calls.append(settings.database_url)

    monkeypatch.setattr(main_module, "create_flask_app", lambda **kwargs: app)
    monkeypatch.setattr(main_module.threading, "Thread", _DummyThread)
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


def test_queue_runtime_builds_configured_fixed_worker_pool(monkeypatch, tmp_path) -> None:
    _set_env(monkeypatch, tmp_path)
    monkeypatch.setenv("AMO_TELEGRAM_QUEUE_WORKER_COUNT", "3")
    settings = main_module.get_settings()

    workers = main_module._queue_worker_processes(settings)

    assert [worker.name for worker in workers] == [
        "telegram-queue-worker-1",
        "telegram-queue-worker-2",
        "telegram-queue-worker-3",
    ]
    assert [worker.kind for worker in workers] == ["queue_worker", "queue_worker", "queue_worker"]
    assert [worker.args for worker in workers] == [(1,), (2,), (3,)]


def test_build_queue_worker_dispatcher_wires_non_none_ai_service(monkeypatch, tmp_path) -> None:
    _set_env(monkeypatch, tmp_path)
    captured: dict[str, object] = {}

    class _DummyDispatcher:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            captured["ai_service"] = kwargs.get("ai_service")

    monkeypatch.setattr(main_module, "Dispatcher", _DummyDispatcher)

    settings = main_module.get_settings()
    main_module.init_db(settings.database_url)
    sender = main_module.QueueBackedTelegramSender(
        database_url=settings.database_url,
        topic_id=7,
        trigger_message_id=42,
        job_id="test",
    )
    main_module._build_queue_worker_dispatcher(
        settings=settings,
        session_factory=main_module.create_session_factory(settings.database_url),
        tg=object(),  # type: ignore[arg-type]
        sender=sender,
    )

    assert "ai_service" in captured
    assert captured["ai_service"] is not None


def test_build_queue_worker_dispatcher_wires_topic_aware_send_functions(monkeypatch, tmp_path) -> None:
    _set_env(monkeypatch, tmp_path)

    captured: dict[str, object] = {}
    sent: list[tuple[str, int, str, int | None]] = []

    class _DummyPCE:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            captured["send_message"] = kwargs["send_message"]
            captured["reply"] = kwargs["reply"]

    class _DummyDispatcher:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            captured["send_text"] = kwargs["send_text"]

    class _DummySender:
        job_id = "job-1"

        async def send_text(self, chat_id: int, text: str, message_thread_id: int | None = None) -> object:
            sent.append(("text", chat_id, text, message_thread_id))
            return {"ok": True}

        async def send_markup(
            self,
            chat_id: int,
            text: str,
            reply_markup: dict[str, object],
            message_thread_id: int | None = None,
        ) -> object:
            sent.append(("markup", chat_id, text, message_thread_id))
            return {"ok": True}

    monkeypatch.setattr(main_module, "QueueBackedTelegramSender", lambda **kwargs: _DummySender())
    monkeypatch.setattr(main_module, "PluginCommandExecutor", _DummyPCE)
    monkeypatch.setattr(main_module, "Dispatcher", _DummyDispatcher)

    settings = main_module.get_settings()
    main_module.init_db(settings.database_url)
    main_module._build_queue_worker_dispatcher(
        settings=settings,
        session_factory=main_module.create_session_factory(settings.database_url),
        tg=object(),  # type: ignore[arg-type]
        sender=_DummySender(),
    )

    send_text = captured["send_text"]
    send_message = captured["send_message"]
    reply = captured["reply"]

    asyncio.run(send_text(1, "hello", 872))
    asyncio.run(send_message(2, "world", 951))
    asyncio.run(reply(3, 44, "reply", 123))

    assert sent == [
        ("text", 1, "hello", 872),
        ("text", 2, "world", 951),
        ("text", 3, "reply", 123),
    ]


def test_build_queue_worker_dispatcher_wires_plugin_command_executor_reply_markup(monkeypatch, tmp_path) -> None:
    _set_env(monkeypatch, tmp_path)

    captured: dict[str, object] = {}

    class _DummyPCE:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            captured["reply_markup"] = kwargs.get("reply_markup")

    class _DummyDispatcher:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            pass

    monkeypatch.setattr(main_module, "PluginCommandExecutor", _DummyPCE)
    monkeypatch.setattr(main_module, "Dispatcher", _DummyDispatcher)

    settings = main_module.get_settings()
    main_module.init_db(settings.database_url)
    sender = main_module.QueueBackedTelegramSender(
        database_url=settings.database_url,
        topic_id=7,
        trigger_message_id=42,
        job_id="test",
    )
    main_module._build_queue_worker_dispatcher(
        settings=settings,
        session_factory=main_module.create_session_factory(settings.database_url),
        tg=object(),  # type: ignore[arg-type]
        sender=sender,
    )

    assert "reply_markup" in captured
    assert captured["reply_markup"] is not None
