from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from amo_bot.auth.roles import Role


class PluginManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    description: str = ""
    commands: list[str] = Field(min_length=1)
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
