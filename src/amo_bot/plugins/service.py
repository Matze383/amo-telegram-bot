from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy.orm import sessionmaker

from amo_bot.auth.roles import ROLE_ACCESS_RANK, Role, role_meets_minimum, stricter_role
from amo_bot.db.repositories import PluginActivationRequestStatus, PluginRepository
from amo_bot.plugins.loader import DiscoveryResult, PluginLoader
from amo_bot.plugins.manifest import PluginManifest
from amo_bot.plugins.settings_validation import PluginSettingsValidator, redact_plugin_settings


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
    def plugin_min_role(required_roles: list[str] | tuple[str, ...] | set[str]) -> Role:
        """Derive the plugin's least-privileged allowed role as a minimum.

        Manifests historically store ``required_roles`` as an allow-list. For
        the minimum-role contract, the least strict role in that list becomes
        the plugin minimum. Missing roles mean normal access; ``ignore`` is not
        an allow role and is ignored for minimum calculations.

        Invalid role values are rejected with a contextual policy error instead
        of leaking raw enum conversion failures.
        """

        roles: list[Role] = []
        for item in required_roles:
            try:
                role = Role(item)
            except ValueError as exc:
                raise PluginPolicyError(f"invalid required role in plugin policy: {item!r}") from exc
            if role is not Role.IGNORE:
                roles.append(role)

        if not roles:
            return Role.NORMAL
        return min(roles, key=lambda role: ROLE_ACCESS_RANK[role])

    @staticmethod
    def effective_min_role(
        plugin_required_roles: list[str] | tuple[str, ...] | set[str],
        admin_restriction: Role | str | None = None,
    ) -> Role:
        """Return max(plugin_min_role, admin_restriction) in role strictness.

        Admin/owner restrictions can only tighten plugin access. Attempts to
        loosen below the plugin-declared minimum keep the plugin minimum.
        """

        plugin_minimum = PluginPolicy.plugin_min_role(plugin_required_roles)
        if admin_restriction is None:
            return plugin_minimum

        restriction = admin_restriction if isinstance(admin_restriction, Role) else Role(admin_restriction)
        if restriction is Role.IGNORE:
            return plugin_minimum
        return stricter_role(plugin_minimum, restriction)

    @staticmethod
    def is_role_allowed(
        *,
        actor_role: Role,
        plugin_required_roles: list[str] | tuple[str, ...] | set[str],
        admin_restriction: Role | str | None = None,
    ) -> bool:
        """Evaluate runtime access using the caller's already scoped role.

        Group-admin scoping is resolved before this policy by the role
        resolver. Therefore a scoped group admin is accepted only in the group
        where the resolver returned ``Role.ADMIN``; outside that scope callers
        should pass the fallback resolved role (usually ``Role.NORMAL``).
        """

        effective_minimum = PluginPolicy.effective_min_role(plugin_required_roles, admin_restriction)
        return role_meets_minimum(actor_role, effective_minimum)

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
            db_plugins = {item.name: item for item in repo.list_plugins()}

        plugins_payload = [
            {
                "name": manifest.name,
                "version": manifest.version,
                "enabled": bool(db_plugins[manifest.name].enabled) if manifest.name in db_plugins else False,
                "activation_status": db_plugins[manifest.name].activation_status if manifest.name in db_plugins else "activation_pending",
                "valid": True,
                "commands": manifest.commands,
                "required_roles": manifest.required_roles,
                "required_permissions": manifest.required_permissions,
                "schedule": manifest.schedule,
                "worker": manifest.worker,
                "settings_schema": {
                    key: schema.model_dump(mode="json", exclude_none=True)
                    for key, schema in manifest.settings_schema.items()
                },
                "settings": redact_plugin_settings(settings_schema=manifest.settings_schema, values={}),
                "worker_state": db_plugins[manifest.name].worker_state if manifest.name in db_plugins else None,
                "worker_last_heartbeat_at": db_plugins[manifest.name].worker_last_heartbeat_at if manifest.name in db_plugins else None,
                "worker_restart_count": db_plugins[manifest.name].worker_restart_count if manifest.name in db_plugins else 0,
                "worker_next_restart_at": db_plugins[manifest.name].worker_next_restart_at if manifest.name in db_plugins else None,
                "worker_last_error": db_plugins[manifest.name].worker_last_error if manifest.name in db_plugins else None,
                "last_run_at": db_plugins[manifest.name].last_run_at if manifest.name in db_plugins else None,
                "last_status": db_plugins[manifest.name].last_status if manifest.name in db_plugins else None,
                "next_run_at": db_plugins[manifest.name].next_run_at if manifest.name in db_plugins else None,
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
        validation = PluginSettingsValidator.validate(settings_schema=manifest.settings_schema, values={})
        if not validation.is_valid:
            first = validation.errors[0]
            raise PluginPolicyError(f"plugin settings validation failed: {first.setting}: {first.message}")
        with self._session_factory() as session:
            repo = PluginRepository(session)
            repo.sync_discovered([manifest])
            return repo.activate(plugin_name, actor_telegram_user_id=actor_telegram_user_id)

    def request_activation(
        self,
        plugin_name: str,
        *,
        context: ActionContext,
        actor_telegram_user_id: int | None,
        reason: str | None = None,
    ) -> PluginActivationRequestStatus:
        decision = PluginPolicy.can_activate(context)
        if not decision.allowed:
            raise PluginPolicyError(decision.reason or "activation request denied")

        manifest = self._require_discovered_valid_manifest(plugin_name)
        with self._session_factory() as session:
            repo = PluginRepository(session)
            repo.sync_discovered([manifest])
            return repo.create_activation_request(
                plugin_name,
                actor_telegram_user_id=actor_telegram_user_id,
                reason=reason,
            )

    def approve_activation_request(
        self,
        request_id: int,
        *,
        context: ActionContext,
        actor_telegram_user_id: int | None,
    ) -> bool:
        decision = PluginPolicy.can_activate(context)
        if not decision.allowed:
            raise PluginPolicyError(decision.reason or "activation approval denied")

        with self._session_factory() as session:
            repo = PluginRepository(session)
            request = repo.get_activation_request(request_id)
            if request is None:
                raise ValueError("activation request not found")
            manifest = self._require_discovered_valid_manifest(request.plugin_name)
            validation = PluginSettingsValidator.validate(settings_schema=manifest.settings_schema, values={})
            if not validation.is_valid:
                first = validation.errors[0]
                raise PluginPolicyError(f"plugin settings validation failed: {first.setting}: {first.message}")
            repo.sync_discovered([manifest])
            return repo.resolve_activation_request(
                request_id,
                status="approved",
                actor_telegram_user_id=actor_telegram_user_id,
            )

    def reject_activation_request(
        self,
        request_id: int,
        *,
        context: ActionContext,
        actor_telegram_user_id: int | None,
    ) -> bool:
        decision = PluginPolicy.can_deactivate(context)
        if not decision.allowed:
            raise PluginPolicyError(decision.reason or "activation rejection denied")

        with self._session_factory() as session:
            return PluginRepository(session).resolve_activation_request(
                request_id,
                status="rejected",
                actor_telegram_user_id=actor_telegram_user_id,
            )

    def block_activation_request(
        self,
        request_id: int,
        *,
        context: ActionContext,
        actor_telegram_user_id: int | None,
    ) -> bool:
        decision = PluginPolicy.can_deactivate(context)
        if not decision.allowed:
            raise PluginPolicyError(decision.reason or "activation block denied")

        with self._session_factory() as session:
            return PluginRepository(session).resolve_activation_request(
                request_id,
                status="blocked",
                actor_telegram_user_id=actor_telegram_user_id,
            )

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
