from amo_bot.webui import app as legacy_webui_app


DISABLED_DETAIL = "Legacy FastAPI WebUI is permanently disabled. Use Flask WebUI."


def test_import_and_create_app_without_fastapi_dependency() -> None:
    app = legacy_webui_app.create_app()

    assert app.disabled is True
    assert app.status_code == 410
    assert app.detail == DISABLED_DETAIL
    assert app.routes == ()


def test_create_app_fails_closed_and_exposes_no_routes() -> None:
    app = legacy_webui_app.create_app()

    assert app.routes == ()

    try:
        app()
    except legacy_webui_app.LegacyWebUIDisabledError as exc:
        assert str(exc) == DISABLED_DETAIL
    else:
        raise AssertionError("Disabled app did not fail closed")


def test_module_level_app_is_disabled_and_no_legacy_mutation_routes() -> None:
    module_app = legacy_webui_app.app

    assert module_app.disabled is True
    assert module_app.routes == ()
    assert not hasattr(module_app, "api_route")

    response = legacy_webui_app.disabled_surface("plugins/activate")
    assert response.status_code == 410
    assert response.detail == DISABLED_DETAIL
