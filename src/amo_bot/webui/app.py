from __future__ import annotations

import secrets
import time
from dataclasses import dataclass

from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from pydantic import BaseModel

from amo_bot.auth.roles import Role
from amo_bot.config.settings import Settings, get_settings
from amo_bot.db.base import create_session_factory
from amo_bot.db.repositories import UserRoleRepository
from amo_bot.plugins.loader import PluginLoader
from amo_bot.plugins.service import ActionContext, PluginPolicyError, PluginService


@dataclass(slots=True)
class WebUISessionStore:
    _tokens: dict[str, float]
    _ttl_seconds: int

    def __init__(self, ttl_seconds: int = 3600) -> None:
        self._tokens = {}
        self._ttl_seconds = max(1, ttl_seconds)

    def issue_token(self) -> str:
        token = secrets.token_urlsafe(32)
        self._tokens[token] = time.monotonic() + self._ttl_seconds
        return token

    def is_valid(self, token: str) -> bool:
        self._cleanup_expired()
        expiry = self._tokens.get(token)
        if expiry is None:
            return False
        if expiry <= time.monotonic():
            self._tokens.pop(token, None)
            return False
        return True

    def invalidate(self, token: str) -> None:
        self._tokens.pop(token, None)

    def _cleanup_expired(self) -> None:
        now = time.monotonic()
        expired = [token for token, expiry in self._tokens.items() if expiry <= now]
        for token in expired:
            self._tokens.pop(token, None)


class LoginRequest(BaseModel):
    password: str


class LoginResponse(BaseModel):
    token: str


class SetRoleRequest(BaseModel):
    target_telegram_user_id: int
    role: Role


class PluginToggleRequest(BaseModel):
    plugin_name: str


def create_app(
    *,
    settings: Settings | None = None,
    session_store: WebUISessionStore | None = None,
    plugin_service: PluginService | None = None,
) -> FastAPI:
    app_settings = settings or get_settings()
    auth_store = session_store or WebUISessionStore(ttl_seconds=app_settings.webui_session_ttl_seconds)
    session_factory = create_session_factory(app_settings.database_url)
    plugins = plugin_service or PluginService(
        loader=PluginLoader(app_settings.amo_plugin_dir),
        session_factory=session_factory,
    )

    app = FastAPI(
        title="AMO Telegram Bot WebUI (MVP only)",
        version="0.1.0",
        description="Local-only MVP WebUI with minimal auth. Not production-ready.",
    )

    def require_auth(authorization: str | None = Header(default=None)) -> str:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing or invalid auth")
        token = authorization.removeprefix("Bearer ").strip()
        if not auth_store.is_valid(token):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")
        return token

    def ensure_mutation_allowed() -> None:
        password = app_settings.webui_password.strip() if app_settings.webui_password else ""
        if not password or password.lower() == "change_me":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="WEBUI_PASSWORD not set to a safe value; mutating routes are disabled",
            )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/auth/login", response_model=LoginResponse)
    def login(payload: LoginRequest) -> LoginResponse:
        ensure_mutation_allowed()
        if payload.password != app_settings.webui_password:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")
        token = auth_store.issue_token()
        return LoginResponse(token=token)

    @app.get("/dashboard")
    def dashboard(_: str = Depends(require_auth)) -> dict[str, object]:
        return {
            "message": "MVP dashboard",
            "webui": {
                "host": app_settings.webui_host,
                "port": app_settings.webui_port,
                "local_only_expected": app_settings.webui_host == "127.0.0.1",
            },
            "warning": "MVP only. Keep local and do not expose to internet.",
        }

    @app.get("/users/{telegram_user_id}")
    def get_user(telegram_user_id: int, _: str = Depends(require_auth)) -> dict[str, object]:
        with session_factory() as session:
            repo = UserRoleRepository(session)
            role = repo.get_user_role(telegram_user_id)

        return {
            "telegram_user_id": telegram_user_id,
            "role": role.value if role is not None else Role.NORMAL.value,
            "exists": role is not None,
        }

    @app.post("/users/set-role")
    def set_role(payload: SetRoleRequest, _: str = Depends(require_auth)) -> dict[str, object]:
        ensure_mutation_allowed()
        if app_settings.webui_owner_telegram_id is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="WEBUI_OWNER_TELEGRAM_ID not configured; mutating role route is disabled",
            )

        with session_factory() as session:
            repo = UserRoleRepository(session)
            result = repo.set_user_role(
                actor_telegram_user_id=app_settings.webui_owner_telegram_id,
                target_telegram_user_id=payload.target_telegram_user_id,
                role=payload.role,
            )

        return {
            "changed": result.changed,
            "target_telegram_user_id": payload.target_telegram_user_id,
            "previous_role": result.previous_role.value if result.previous_role else None,
            "new_role": result.new_role.value,
            "warning": "owner role assignment via webui is powerful; ensure local owner-only access",
        }

    @app.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
    def logout(token: str = Depends(require_auth)) -> Response:
        auth_store.invalidate(token)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/plugins")
    def list_plugins(_: str = Depends(require_auth)) -> dict[str, object]:
        return plugins.list_plugins()

    @app.post("/plugins/activate")
    def activate_plugin(payload: PluginToggleRequest, _: str = Depends(require_auth)) -> dict[str, object]:
        ensure_mutation_allowed()
        if app_settings.webui_owner_telegram_id is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="WEBUI_OWNER_TELEGRAM_ID not configured; plugin mutation routes are disabled",
            )
        try:
            changed = plugins.activate(
                payload.plugin_name,
                context=ActionContext.WEBUI,
                actor_telegram_user_id=app_settings.webui_owner_telegram_id,
            )
        except PluginPolicyError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        return {"plugin_name": payload.plugin_name, "active": True, "changed": changed}

    @app.post("/plugins/deactivate")
    def deactivate_plugin(payload: PluginToggleRequest, _: str = Depends(require_auth)) -> dict[str, object]:
        ensure_mutation_allowed()
        if app_settings.webui_owner_telegram_id is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="WEBUI_OWNER_TELEGRAM_ID not configured; plugin mutation routes are disabled",
            )
        try:
            changed = plugins.deactivate(
                payload.plugin_name,
                context=ActionContext.WEBUI,
                actor_telegram_user_id=app_settings.webui_owner_telegram_id,
            )
        except PluginPolicyError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        return {"plugin_name": payload.plugin_name, "active": False, "changed": changed}

    return app


app = FastAPI(
    title="AMO Telegram Bot WebUI (MVP only)",
    version="0.1.0",
    description="App not configured yet. Use amo_bot.webui.app:create_app() with settings for tests or runtime.",
)


@app.on_event("startup")
def _configure_default_app() -> None:
    configured = create_app()
    app.router.routes = configured.router.routes
