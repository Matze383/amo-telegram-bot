from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from amo_bot.plugins.manifest import PluginSettingSchema


@dataclass(slots=True, frozen=True)
class PluginSettingsValidationError:
    setting: str
    message: str


@dataclass(slots=True, frozen=True)
class PluginSettingsValidationResult:
    values: dict[str, Any]
    errors: list[PluginSettingsValidationError]

    @property
    def is_valid(self) -> bool:
        return not self.errors


class PluginSettingsValidator:
    @staticmethod
    def validate(
        *,
        settings_schema: dict[str, PluginSettingSchema],
        values: dict[str, Any] | None,
    ) -> PluginSettingsValidationResult:
        incoming = values or {}
        normalized: dict[str, Any] = {}
        errors: list[PluginSettingsValidationError] = []

        for name, schema in settings_schema.items():
            present = name in incoming
            value = incoming.get(name)

            if not present and schema.default is not None:
                value = schema.default
                present = True

            if schema.required and (not present or value is None or (isinstance(value, str) and not value.strip())):
                errors.append(PluginSettingsValidationError(setting=name, message="required setting missing"))
                continue

            if not present:
                continue

            if schema.type in {"text", "secret"}:
                if not isinstance(value, str):
                    errors.append(PluginSettingsValidationError(setting=name, message="value must be a string"))
                    continue
                if schema.pattern is not None and re.fullmatch(schema.pattern, value) is None:
                    errors.append(PluginSettingsValidationError(setting=name, message="value does not match pattern"))
                    continue
                normalized[name] = value
                continue

            if schema.type == "number":
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    errors.append(PluginSettingsValidationError(setting=name, message="value must be a number"))
                    continue
                numeric = float(value)
                if schema.min is not None and numeric < float(schema.min):
                    errors.append(PluginSettingsValidationError(setting=name, message=f"value must be >= {schema.min}"))
                    continue
                if schema.max is not None and numeric > float(schema.max):
                    errors.append(PluginSettingsValidationError(setting=name, message=f"value must be <= {schema.max}"))
                    continue
                normalized[name] = value
                continue

            if schema.type == "bool":
                if not isinstance(value, bool):
                    errors.append(PluginSettingsValidationError(setting=name, message="value must be true/false"))
                    continue
                normalized[name] = value
                continue

            if schema.type == "select":
                if not isinstance(value, str):
                    errors.append(PluginSettingsValidationError(setting=name, message="value must be a string"))
                    continue
                options = schema.options or []
                if value not in options:
                    errors.append(PluginSettingsValidationError(setting=name, message="value must be one of configured options"))
                    continue
                normalized[name] = value
                continue

        return PluginSettingsValidationResult(values=normalized, errors=errors)


def redact_plugin_settings(*, settings_schema: dict[str, PluginSettingSchema], values: dict[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for name, value in values.items():
        schema = settings_schema.get(name)
        if schema is not None and schema.type == "secret":
            redacted[name] = "***"
        else:
            redacted[name] = value
    return redacted
