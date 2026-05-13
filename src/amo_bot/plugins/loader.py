from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
import re
from typing import Any

import yaml

from pydantic import ValidationError

from amo_bot.plugins.manifest import PluginManifest


_PLUGIN_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{2,49}$")
_RESERVED_PLUGIN_IDS = {"core", "system", "internal", "builtin"}
_ALLOWED_MVP_TRIGGERS = {"cron", "interval", "user-triggered"}
_RESERVED_TRIGGERS = {"ki-triggered", "ai-triggered"}


class DiscoveryCode(StrEnum):
    FOUND = "FOUND"
    MISSING_MANIFEST = "MISSING_MANIFEST"
    DISABLED = "DISABLED"
    INVALID_YAML = "INVALID_YAML"
    INVALID_MANIFEST = "INVALID_MANIFEST"
    RESERVED_ID = "RESERVED_ID"
    DUPLICATE_ID = "DUPLICATE_ID"
    INVALID_TRIGGER_TYPE = "INVALID_TRIGGER_TYPE"


@dataclass(slots=True, frozen=True)
class DiscoveryOutcome:
    plugin_dir: str
    status: str
    code: DiscoveryCode
    detail: str


@dataclass(slots=True, frozen=True)
class InvalidPluginManifest:
    plugin_dir: str
    file_name: str
    error: str


@dataclass(slots=True, frozen=True)
class DiscoveryResult:
    valid: list[PluginManifest]
    invalid: list[InvalidPluginManifest]
    outcomes: list[DiscoveryOutcome]


class PluginLoader:
    def __init__(self, plugins_dir: str = "plugins") -> None:
        self.plugins_dir = Path(plugins_dir)

    def discover(self) -> DiscoveryResult:
        valid: list[PluginManifest] = []
        invalid: list[InvalidPluginManifest] = []
        outcomes: list[DiscoveryOutcome] = []
        seen_plugin_ids: dict[str, str] = {}

        if not self.plugins_dir.exists():
            return DiscoveryResult(valid=valid, invalid=invalid, outcomes=outcomes)

        for plugin_dir in sorted(path for path in self.plugins_dir.iterdir() if path.is_dir()):
            if (plugin_dir.parent / f"{plugin_dir.name}.disabled").exists() or (plugin_dir / ".disabled").exists():
                outcomes.append(
                    DiscoveryOutcome(
                        plugin_dir=plugin_dir.name,
                        status="discovery_disabled",
                        code=DiscoveryCode.DISABLED,
                        detail="plugin disabled by marker file",
                    )
                )
                continue
            manifest_candidates = [
                plugin_dir / "plugin.yaml",
                plugin_dir / "plugin.yml",
                plugin_dir / "plugin.json",
                plugin_dir / "manifest.json",
            ]
            manifest_path = next((path for path in manifest_candidates if path.exists()), None)
            if manifest_path is None:
                outcomes.append(
                    DiscoveryOutcome(
                        plugin_dir=plugin_dir.name,
                        status="discovery_invalid",
                        code=DiscoveryCode.MISSING_MANIFEST,
                        detail="missing plugin.yaml/plugin.yml/plugin.json/manifest.json",
                    )
                )
                continue

            try:
                raw_text = manifest_path.read_text(encoding="utf-8")
                if manifest_path.suffix in {".yaml", ".yml"}:
                    raw = yaml.safe_load(raw_text)
                else:
                    raw = json.loads(raw_text)
                if not isinstance(raw, dict):
                    raise ValueError("manifest root must be an object")

                trigger_error = self._validate_mvp_triggers(raw)
                if trigger_error is not None:
                    invalid.append(
                        InvalidPluginManifest(
                            plugin_dir=plugin_dir.name,
                            file_name=manifest_path.name,
                            error=f"invalid manifest: {trigger_error}",
                        )
                    )
                    outcomes.append(
                        DiscoveryOutcome(
                            plugin_dir=plugin_dir.name,
                            status="discovery_invalid",
                            code=DiscoveryCode.INVALID_TRIGGER_TYPE,
                            detail=trigger_error,
                        )
                    )
                    continue

                manifest = PluginManifest.model_validate(raw)

                if not _PLUGIN_ID_RE.fullmatch(manifest.name):
                    error_detail = "plugin id/name must match ^[a-z][a-z0-9_-]{2,49}$"
                    invalid.append(
                        InvalidPluginManifest(
                            plugin_dir=plugin_dir.name,
                            file_name=manifest_path.name,
                            error=f"invalid manifest: {error_detail}",
                        )
                    )
                    outcomes.append(
                        DiscoveryOutcome(
                            plugin_dir=plugin_dir.name,
                            status="discovery_invalid",
                            code=DiscoveryCode.INVALID_MANIFEST,
                            detail=error_detail,
                        )
                    )
                    continue

                if manifest.name in _RESERVED_PLUGIN_IDS:
                    error_detail = f"plugin id '{manifest.name}' is reserved"
                    invalid.append(
                        InvalidPluginManifest(
                            plugin_dir=plugin_dir.name,
                            file_name=manifest_path.name,
                            error=f"invalid manifest: {error_detail}",
                        )
                    )
                    outcomes.append(
                        DiscoveryOutcome(
                            plugin_dir=plugin_dir.name,
                            status="discovery_blocked",
                            code=DiscoveryCode.RESERVED_ID,
                            detail=error_detail,
                        )
                    )
                    continue

                first_dir = seen_plugin_ids.get(manifest.name)
                if first_dir is not None:
                    error_detail = f"duplicate plugin id '{manifest.name}' also found in '{first_dir}'"
                    invalid.append(
                        InvalidPluginManifest(
                            plugin_dir=plugin_dir.name,
                            file_name=manifest_path.name,
                            error=f"invalid manifest: {error_detail}",
                        )
                    )
                    outcomes.append(
                        DiscoveryOutcome(
                            plugin_dir=plugin_dir.name,
                            status="discovery_blocked",
                            code=DiscoveryCode.DUPLICATE_ID,
                            detail=error_detail,
                        )
                    )
                    continue

                seen_plugin_ids[manifest.name] = plugin_dir.name
                valid.append(manifest)
                outcomes.append(
                    DiscoveryOutcome(
                        plugin_dir=plugin_dir.name,
                        status="discovery_found",
                        code=DiscoveryCode.FOUND,
                        detail="manifest accepted",
                    )
                )
            except (OSError, json.JSONDecodeError, yaml.YAMLError, ValidationError, ValueError) as exc:
                code = (
                    DiscoveryCode.INVALID_YAML
                    if isinstance(exc, (json.JSONDecodeError, yaml.YAMLError))
                    else DiscoveryCode.INVALID_MANIFEST
                )
                invalid.append(
                    InvalidPluginManifest(
                        plugin_dir=plugin_dir.name,
                        file_name=manifest_path.name,
                        error=f"invalid manifest: {str(exc)[:300]}",
                    )
                )
                outcomes.append(
                    DiscoveryOutcome(
                        plugin_dir=plugin_dir.name,
                        status="discovery_invalid",
                        code=code,
                        detail=str(exc)[:300],
                    )
                )

        return DiscoveryResult(valid=valid, invalid=invalid, outcomes=outcomes)

    def _validate_mvp_triggers(self, raw_manifest: dict[str, Any]) -> str | None:
        triggers = raw_manifest.get("triggers")
        if triggers is None:
            return None
        if not isinstance(triggers, list):
            return "triggers must be an array"

        seen: set[str] = set()
        for trigger in triggers:
            if not isinstance(trigger, str):
                return "triggers must contain only strings"
            trigger_clean = trigger.strip()
            if not trigger_clean:
                return "triggers must contain only non-empty strings"
            if trigger_clean in seen:
                return f"duplicate trigger type: {trigger_clean}"
            seen.add(trigger_clean)
            if trigger_clean in _RESERVED_TRIGGERS:
                return f"unsupported trigger type: {trigger_clean}"
            if trigger_clean not in _ALLOWED_MVP_TRIGGERS:
                return f"unsupported trigger type: {trigger_clean}"

        if "interval" in seen and "cron" in seen:
            return "triggers must not combine interval and cron"

        return None

    def discover_manifests(self) -> list[PluginManifest]:
        return self.discover().valid
