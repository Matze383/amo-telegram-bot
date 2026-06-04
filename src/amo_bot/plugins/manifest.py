from __future__ import annotations

from typing import Any

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from amo_bot.auth.roles import Role


class PluginSettingSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    required: bool = False
    default: str | int | float | bool | None = None
    min: int | float | None = None
    max: int | float | None = None
    options: list[str] | None = None
    pattern: str | None = None

    @field_validator("type")
    @classmethod
    def validate_type(cls, value: str) -> str:
        allowed = {"text", "number", "bool", "select", "secret"}
        value_clean = value.strip().lower()
        if value_clean not in allowed:
            raise ValueError(f"unsupported setting type: {value}")
        return value_clean

    @model_validator(mode="after")
    def validate_schema_constraints(self) -> "PluginSettingSchema":
        if self.type == "number":
            if self.default is not None and not isinstance(self.default, (int, float)):
                raise ValueError("number setting default must be numeric")
            if self.min is not None and self.max is not None and self.min > self.max:
                raise ValueError("number setting min must be <= max")
        elif self.type == "bool":
            if self.default is not None and not isinstance(self.default, bool):
                raise ValueError("bool setting default must be true/false")
        elif self.type == "select":
            if not self.options:
                raise ValueError("select setting must define non-empty options")
            cleaned = [item.strip() for item in self.options if isinstance(item, str)]
            if len(cleaned) != len(self.options) or any(not item for item in cleaned):
                raise ValueError("select setting options must contain only non-empty strings")
            if self.default is not None and self.default not in cleaned:
                raise ValueError("select setting default must be one of options")
            self.options = cleaned
        elif self.type in {"text", "secret"}:
            if self.default is not None and not isinstance(self.default, str):
                raise ValueError(f"{self.type} setting default must be a string")
            if self.pattern is not None:
                try:
                    re.compile(self.pattern)
                except re.error as exc:
                    raise ValueError(f"invalid pattern: {exc}") from exc

        return self


_MIN_INTERVAL_SECONDS = 10


_CRON_FIELD_LIMITS: tuple[tuple[int, int], ...] = (
    (0, 59),   # minute
    (0, 23),   # hour
    (1, 31),   # day of month
    (1, 12),   # month
    (0, 7),    # day of week (0/7 = Sunday)
)


def _validate_cron_number(token: str, min_value: int, max_value: int) -> bool:
    if not token.isdigit():
        return False
    value = int(token)
    return min_value <= value <= max_value


def _validate_cron_range(token: str, min_value: int, max_value: int) -> bool:
    start_raw, end_raw = token.split("-", 1)
    if not (_validate_cron_number(start_raw, min_value, max_value) and _validate_cron_number(end_raw, min_value, max_value)):
        return False
    return int(start_raw) <= int(end_raw)


def _validate_cron_part(token: str, min_value: int, max_value: int) -> bool:
    if token == "*":
        return True

    base = token
    step_raw: str | None = None
    if "/" in token:
        base, step_raw = token.split("/", 1)
        if not step_raw or not step_raw.isdigit() or int(step_raw) <= 0:
            return False

    if base == "*":
        return True
    if "-" in base:
        return _validate_cron_range(base, min_value, max_value)
    return _validate_cron_number(base, min_value, max_value)


def _is_valid_mvp_cron(expression: str) -> bool:
    fields = expression.split()
    if len(fields) != 5:
        return False

    for field, (min_value, max_value) in zip(fields, _CRON_FIELD_LIMITS, strict=True):
        segments = field.split(",")
        if any(not segment for segment in segments):
            return False
        if not all(_validate_cron_part(segment, min_value, max_value) for segment in segments):
            return False

    return True


class PluginManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    description: str = ""
    commands: list[str] = Field(default_factory=list)
    schedule: dict[str, Any] | None = None
    worker: dict[str, int] | None = None
    required_roles: list[str] = Field(default_factory=list)
    required_permissions: list[str] = Field(default_factory=list)
    settings_schema: dict[str, PluginSettingSchema] = Field(default_factory=dict)

    @field_validator("commands")
    @classmethod
    def validate_commands(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if isinstance(item, str)]
        if len(cleaned) != len(value) or any(not item for item in cleaned):
            raise ValueError("commands must contain only non-empty strings")
        return cleaned

    @field_validator("required_permissions")
    @classmethod
    def validate_required_permissions(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if isinstance(item, str)]
        if len(cleaned) != len(value) or any(not item for item in cleaned):
            raise ValueError("required_permissions must contain only non-empty strings")
        return cleaned

    @field_validator("schedule")
    @classmethod
    def validate_schedule(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None

        has_interval = "interval_seconds" in value
        has_cron = "cron" in value
        if has_interval == has_cron:
            raise ValueError("schedule must define exactly one of interval_seconds or cron")

        if has_interval:
            interval = value.get("interval_seconds")
            if not isinstance(interval, int):
                raise ValueError("schedule.interval_seconds must be an integer")
            if interval < _MIN_INTERVAL_SECONDS:
                raise ValueError(f"schedule.interval_seconds must be >= {_MIN_INTERVAL_SECONDS}")
            return {"interval_seconds": interval}

        cron_expr = value.get("cron")
        if not isinstance(cron_expr, str) or not cron_expr.strip():
            raise ValueError("schedule.cron must be a non-empty string")
        cron_clean = cron_expr.strip()
        if not _is_valid_mvp_cron(cron_clean):
            raise ValueError("schedule.cron must be a valid cron expression")
        return {"cron": cron_clean}

    @field_validator("worker")
    @classmethod
    def validate_worker(cls, value: dict[str, int] | None) -> dict[str, int] | None:
        if value is None:
            return None
        backoff = value.get("restart_backoff_seconds", 60)
        if not isinstance(backoff, int) or backoff < 1:
            raise ValueError("worker.restart_backoff_seconds must be a positive integer")
        worker = {"restart_backoff_seconds": backoff}
        timeout_ms = value.get("timeout_ms")
        if timeout_ms is not None:
            if not isinstance(timeout_ms, int) or timeout_ms < 100:
                raise ValueError("worker.timeout_ms must be an integer >= 100")
            worker["timeout_ms"] = timeout_ms
        return worker

    @field_validator("required_roles")
    @classmethod
    def validate_required_roles(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip().lower() for item in value if isinstance(item, str)]
        if len(cleaned) != len(value) or any(not item for item in cleaned):
            raise ValueError("required_roles must contain only non-empty strings")

        allowed = {role.value for role in Role}
        invalid = [role for role in cleaned if role not in allowed]
        if invalid:
            raise ValueError(f"required_roles contains invalid roles: {', '.join(sorted(set(invalid)))}")
        return cleaned

    @model_validator(mode="before")
    @classmethod
    def ensure_auth_fields_present(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        has_roles = "required_roles" in data
        has_permissions = "required_permissions" in data
        if not has_roles and not has_permissions:
            raise ValueError("manifest must define required_roles or required_permissions")
        return data

    @model_validator(mode="after")
    def ensure_trigger_present(self) -> PluginManifest:
        if not self.commands and self.schedule is None and self.worker is None:
            raise ValueError("manifest must define commands, schedule, or worker")
        return self

    @field_validator("settings_schema")
    @classmethod
    def validate_settings_schema_keys(cls, value: dict[str, PluginSettingSchema]) -> dict[str, PluginSettingSchema]:
        for key in value:
            key_clean = key.strip()
            if not key_clean:
                raise ValueError("settings_schema keys must be non-empty strings")
            if key_clean != key:
                raise ValueError("settings_schema keys must not contain leading/trailing spaces")
        return value
