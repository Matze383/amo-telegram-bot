from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy.orm import sessionmaker

from amo_bot.db.repositories import PluginRepository
from amo_bot.plugins.loader import DiscoveryResult, PluginLoader
from amo_bot.plugins.manifest import PluginManifest


class ActionContext(StrEnum):
    WEBUI = "webui"
    TELEGRAM = "telegram"


@dataclass(slots=True, frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str | None = None


class PluginPolicy:
    """Zentrale MVP-Policy fuer Plugin-Aktionen."""

    @staticmethod
    def can_activate(context: ActionContext) -> PolicyDecision:
        if context is ActionContext.WEBUI:
            return PolicyDecision(allowed=True)
        return PolicyDecision(
            allowed=False,
            reason="plugin activation is restricted to webui context in MVP",
        )

    @staticmethod
    def can_deactivate(context: ActionContext) -> PolicyDecision:
        if context is ActionContext.WEBUI:
            return PolicyDecision(allowed=True)
        return PolicyDecision(
            allowed=False,
            reason="plugin deactivation is restricted to webui context in MVP",
        )


class PluginPolicyError(RuntimeError):
    pass


@dataclass(slots=True, frozen=True)
class PluginListItem:
    name: str
    version: str
    enabled: bool
    valid: bool


class PluginService:
    def __init__(
        self,
        *,
        loader: PluginLoader,
        session_factory: sessionmaker,
    ) -> None:
        self._loader = loader
        self._session_factory = session_factory

    def list_plugins(self) -> dict[str, list[dict[str, object]]]:
        discovery = self._loader.discover()
        valid_by_name = {manifest.name: manifest for manifest in discovery.valid}

        with self._session_factory() as session:
            repo = PluginRepository(session)
            repo.sync_discovered(discovery.valid)
            db_plugins = {item.name: item.enabled for item in repo.list_plugins()}

        plugins_payload = [
            {
                "name": manifest.name,
                "version": manifest.version,
                "enabled": bool(db_plugins.get(manifest.name, False)),
                "valid": True,
            }
            for manifest in sorted(valid_by_name.values(), key=lambda item: item.name)
        ]

        invalid_payload = [
            {
                "plugin_dir": entry.plugin_dir,
                "file_name": entry.file_name,
                "error": entry.error,
                "valid": False,
            }
            for entry in discovery.invalid
        ]

        return {"plugins": plugins_payload, "invalid_manifests": invalid_payload}

    def activate(self, plugin_name: str, *, context: ActionContext, actor_telegram_user_id: int | None) -> bool:
        decision = PluginPolicy.can_activate(context)
        if not decision.allowed:
            raise PluginPolicyError(decision.reason or "activation denied")

        manifest = self._require_discovered_valid_manifest(plugin_name)
        with self._session_factory() as session:
            repo = PluginRepository(session)
            repo.sync_discovered([manifest])
            return repo.activate(plugin_name, actor_telegram_user_id=actor_telegram_user_id)

    def deactivate(self, plugin_name: str, *, context: ActionContext, actor_telegram_user_id: int | None) -> bool:
        decision = PluginPolicy.can_deactivate(context)
        if not decision.allowed:
            raise PluginPolicyError(decision.reason or "deactivation denied")

        manifest = self._require_discovered_valid_manifest(plugin_name)
        with self._session_factory() as session:
            repo = PluginRepository(session)
            repo.sync_discovered([manifest])
            return repo.deactivate(plugin_name, actor_telegram_user_id=actor_telegram_user_id)

    def _require_discovered_valid_manifest(self, plugin_name: str) -> PluginManifest:
        discovery: DiscoveryResult = self._loader.discover()
        for manifest in discovery.valid:
            if manifest.name == plugin_name:
                return manifest
        raise ValueError("plugin not found or manifest invalid")
