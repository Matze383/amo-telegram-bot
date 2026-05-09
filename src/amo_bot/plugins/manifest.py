from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from amo_bot.auth.roles import Role


class PluginManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    description: str = ""
    commands: list[str] = Field(default_factory=list)
    schedule: dict[str, int] | None = None
    worker: dict[str, int] | None = None
    required_roles: list[str] = Field(default_factory=list)
    required_permissions: list[str] = Field(default_factory=list)

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
    def validate_schedule(cls, value: dict[str, int] | None) -> dict[str, int] | None:
        if value is None:
            return None
        interval = value.get("interval_seconds")
        if not isinstance(interval, int) or interval < 1:
            raise ValueError("schedule.interval_seconds must be a positive integer")
        return {"interval_seconds": interval}

    @field_validator("worker")
    @classmethod
    def validate_worker(cls, value: dict[str, int] | None) -> dict[str, int] | None:
        if value is None:
            return None
        backoff = value.get("restart_backoff_seconds", 60)
        if not isinstance(backoff, int) or backoff < 1:
            raise ValueError("worker.restart_backoff_seconds must be a positive integer")
        return {"restart_backoff_seconds": backoff}

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
