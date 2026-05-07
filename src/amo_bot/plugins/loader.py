from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from amo_bot.plugins.manifest import PluginManifest


@dataclass(slots=True, frozen=True)
class InvalidPluginManifest:
    plugin_dir: str
    file_name: str
    error: str


@dataclass(slots=True, frozen=True)
class DiscoveryResult:
    valid: list[PluginManifest]
    invalid: list[InvalidPluginManifest]


class PluginLoader:
    def __init__(self, plugins_dir: str = "plugins") -> None:
        self.plugins_dir = Path(plugins_dir)

    def discover(self) -> DiscoveryResult:
        valid: list[PluginManifest] = []
        invalid: list[InvalidPluginManifest] = []

        if not self.plugins_dir.exists():
            return DiscoveryResult(valid=valid, invalid=invalid)

        for plugin_dir in sorted(path for path in self.plugins_dir.iterdir() if path.is_dir()):
            manifest_path = plugin_dir / "plugin.json"
            if not manifest_path.exists():
                manifest_path = plugin_dir / "manifest.json"
            if not manifest_path.exists():
                continue

            try:
                raw = json.loads(manifest_path.read_text(encoding="utf-8"))
                valid.append(PluginManifest.model_validate(raw))
            except (OSError, json.JSONDecodeError, ValidationError, ValueError) as exc:
                invalid.append(
                    InvalidPluginManifest(
                        plugin_dir=plugin_dir.name,
                        file_name=manifest_path.name,
                        error=f"invalid manifest: {str(exc)[:300]}",
                    )
                )

        return DiscoveryResult(valid=valid, invalid=invalid)

    def discover_manifests(self) -> list[PluginManifest]:
        return self.discover().valid
