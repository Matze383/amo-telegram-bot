from __future__ import annotations

from amo_bot import main as main_module


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

    class _DummyDispatcher:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            captured.update(kwargs)

    monkeypatch.setattr(main_module, "Dispatcher", _DummyDispatcher)

    settings = main_module.get_settings()
    main_module.init_db(settings.database_url)
    sender = main_module.QueueBackedTelegramSender(
        database_url=settings.database_url,
        topic_id=7,
        trigger_message_id=42,
        job_id="test",
    )
    dispatcher = main_module._build_queue_worker_dispatcher(
        settings=settings,
        session_factory=main_module.create_session_factory(settings.database_url),
        tg=object(),  # type: ignore[arg-type]
        sender=sender,
    )

    assert dispatcher is not None
    assert captured["plugin_command_executor"] is not None
