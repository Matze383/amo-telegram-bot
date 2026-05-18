from __future__ import annotations

from dataclasses import dataclass
from typing import NoReturn

_DISABLED_DETAIL = "Legacy FastAPI WebUI is permanently disabled. Use Flask WebUI."


class LegacyWebUIDisabledError(RuntimeError):
    """Raised when code attempts to use the retired legacy FastAPI WebUI."""


@dataclass(frozen=True)
class DisabledResponse:
    status_code: int
    detail: str


class DisabledLegacyWebUIApp:
    """Fail-closed stub surface for the removed legacy FastAPI app."""

    disabled: bool = True
    status_code: int = 410
    detail: str = _DISABLED_DETAIL
    routes: tuple[()] = ()

    def __call__(self, *_: object, **__: object) -> NoReturn:
        raise LegacyWebUIDisabledError(self.detail)

    def raise_disabled(self) -> NoReturn:
        raise LegacyWebUIDisabledError(self.detail)


def create_app(*, settings: object | None = None, **_: object) -> DisabledLegacyWebUIApp:
    """Return disabled stub object for compatibility with old imports."""

    _ = settings
    return DisabledLegacyWebUIApp()


def disabled_surface(*_: object, **__: object) -> DisabledResponse:
    return DisabledResponse(status_code=410, detail=_DISABLED_DETAIL)


app = create_app()
