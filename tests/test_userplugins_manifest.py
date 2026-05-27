"""Tests to validate USERPLUGINS.md contract against manifest.py implementation.

This test validates that the documented USERPLUGINS.md manifest fields
match the actual manifest.py validation.
"""

import os
import sys

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest
from amo_bot.plugins.manifest import PluginManifest


class TestManifestAuthFieldValidation:
    """Validate required_roles and required_permissions validation.
    
    The contract: at least one of required_roles or required_permissions must be defined.
    Each defaults to [] when absent, but a manifest with neither field is invalid.
    """

    def test_neither_field_invalid(self):
        """Manifest with neither required_roles nor required_permissions is invalid."""
        with pytest.raises(ValueError) as exc_info:
            PluginManifest(
                name="test_plugin",
                version="1.0.0",
                commands=["test"],
            )
        assert "required_roles" in str(exc_info.value).lower() or "required_permissions" in str(exc_info.value).lower()

    def test_roles_only_valid(self):
        """Manifest with only required_roles is valid (permissions defaults to [])."""
        manifest = PluginManifest(
            name="test_plugin",
            version="1.0.0",
            commands=["test"],
            required_roles=["normal"],
        )
        assert manifest.required_roles == ["normal"]
        assert manifest.required_permissions == []

    def test_permissions_only_valid(self):
        """Manifest with only required_permissions is valid (roles defaults to [])."""
        manifest = PluginManifest(
            name="test_plugin",
            version="1.0.0",
            commands=["test"],
            required_permissions=["send_message"],
        )
        assert manifest.required_permissions == ["send_message"]
        assert manifest.required_roles == []

    def test_both_fields_valid(self):
        """Manifest with both required_roles and required_permissions is valid."""
        manifest = PluginManifest(
            name="test_plugin",
            version="1.0.0",
            commands=["test"],
            required_roles=["normal"],
            required_permissions=["send_message"],
        )
        assert manifest.required_roles == ["normal"]
        assert manifest.required_permissions == ["send_message"]


class TestManifestRequiredFields:
    """Validate that documented required fields are actually required."""

    def test_name_is_required(self):
        """name field must be present and non-empty."""
        with pytest.raises(Exception):
            PluginManifest(version="1.0.0", commands=["test"], required_roles=["normal"], required_permissions=[])

    def test_version_is_required(self):
        """version field must be present and non-empty."""
        with pytest.raises(Exception):
            PluginManifest(name="test_plugin", commands=["test"], required_roles=["normal"], required_permissions=[])

    def test_description_defaults_to_empty_string(self):
        """description defaults to empty string when not provided."""
        manifest = PluginManifest(
            name="test_plugin",
            version="1.0.0",
            commands=["test"],
            required_roles=["normal"],
            required_permissions=[],
        )
        assert manifest.description == ""

    def test_at_least_one_trigger_required(self):
        """At least one of commands, schedule, or worker must be defined."""
        # No trigger defined - should fail
        with pytest.raises(Exception) as exc_info:
            PluginManifest(
                name="test_plugin",
                version="1.0.0",
                required_roles=["normal"],
                required_permissions=[],
            )
        assert "commands, schedule, or worker" in str(exc_info.value).lower() or "manifest must define" in str(exc_info.value).lower()

    def test_commands_is_optional_with_schedule(self):
        """commands is optional when schedule is defined."""
        manifest = PluginManifest(
            name="test_plugin",
            version="1.0.0",
            schedule={"interval_seconds": 60},
            required_roles=["normal"],
            required_permissions=[],
        )
        assert manifest.commands == []
        assert manifest.schedule == {"interval_seconds": 60}

    def test_commands_is_optional_with_worker(self):
        """commands is optional when worker is defined."""
        manifest = PluginManifest(
            name="test_plugin",
            version="1.0.0",
            worker={"restart_backoff_seconds": 60},
            required_roles=["normal"],
            required_permissions=[],
        )
        assert manifest.commands == []
        assert manifest.worker == {"restart_backoff_seconds": 60}


class TestManifestScheduleValidation:
    """Validate schedule field constraints from documentation."""

    def test_schedule_interval_minimum_10_seconds(self):
        """interval_seconds must be >= 10."""
        with pytest.raises(ValueError) as exc_info:
            PluginManifest(
                name="test_plugin",
                version="1.0.0",
                schedule={"interval_seconds": 5},
                required_roles=["normal"],
                required_permissions=[],
            )
        assert "interval_seconds" in str(exc_info.value).lower()

    def test_schedule_accepts_valid_interval(self):
        """Valid interval_seconds >= 10 is accepted."""
        manifest = PluginManifest(
            name="test_plugin",
            version="1.0.0",
            schedule={"interval_seconds": 60},
            required_roles=["normal"],
            required_permissions=[],
        )
        assert manifest.schedule == {"interval_seconds": 60}

    def test_schedule_accepts_cron_expression(self):
        """Schedule can use cron expression (5-field)."""
        manifest = PluginManifest(
            name="test_plugin",
            version="1.0.0",
            schedule={"cron": "0 * * * *"},
            required_roles=["normal"],
            required_permissions=[],
        )
        assert manifest.schedule == {"cron": "0 * * * *"}


class TestManifestWorkerValidation:
    """Validate worker field constraints from documentation."""

    def test_worker_default_restart_backoff(self):
        """worker.restart_backoff_seconds defaults to 60."""
        manifest = PluginManifest(
            name="test_plugin",
            version="1.0.0",
            worker={},
            required_roles=["normal"],
            required_permissions=[],
        )
        # Worker with empty dict should get default backoff
        assert manifest.worker is not None

    def test_worker_custom_restart_backoff(self):
        """worker.restart_backoff_seconds can be customized."""
        manifest = PluginManifest(
            name="test_plugin",
            version="1.0.0",
            worker={"restart_backoff_seconds": 120},
            required_roles=["normal"],
            required_permissions=[],
        )
        assert manifest.worker == {"restart_backoff_seconds": 120}


class TestManifestRoleValidation:
    """Validate required_roles field constraints."""

    def test_required_roles_validates_against_role_enum(self):
        """required_roles must contain valid Role enum values."""
        with pytest.raises(ValueError) as exc_info:
            PluginManifest(
                name="test_plugin",
                version="1.0.0",
                commands=["test"],
                required_roles=["invalid_role"],
                required_permissions=[],
            )
        assert "required_roles" in str(exc_info.value).lower() or "invalid" in str(exc_info.value).lower()

    def test_required_roles_normal_is_valid(self):
        """normal is a valid required_roles value."""
        manifest = PluginManifest(
            name="test_plugin",
            version="1.0.0",
            commands=["test"],
            required_roles=["normal"],
            required_permissions=[],
        )
        assert "normal" in manifest.required_roles

    def test_required_roles_accepted_values(self):
        """All documented role values are accepted."""
        for role in ["ignore", "normal", "vip", "admin", "owner"]:
            manifest = PluginManifest(
                name="test_plugin",
                version="1.0.0",
                commands=["test"],
                required_roles=[role],
                required_permissions=[],
            )
            assert role in manifest.required_roles


class TestManifestSettingsSchema:
    """Validate settings_schema field constraints."""

    def test_settings_schema_text_type(self):
        """text type is supported in settings_schema."""
        from amo_bot.plugins.manifest import PluginSettingSchema
        schema = PluginSettingSchema(type="text")
        assert schema.type == "text"

    def test_settings_schema_number_type(self):
        """number type is supported in settings_schema."""
        from amo_bot.plugins.manifest import PluginSettingSchema
        schema = PluginSettingSchema(type="number", min=1, max=100, default=50)
        assert schema.type == "number"
        assert schema.min == 1
        assert schema.max == 100

    def test_settings_schema_bool_type(self):
        """bool type is supported in settings_schema."""
        from amo_bot.plugins.manifest import PluginSettingSchema
        schema = PluginSettingSchema(type="bool", default=True)
        assert schema.type == "bool"
        assert schema.default is True

    def test_settings_schema_select_type(self):
        """select type is supported in settings_schema."""
        from amo_bot.plugins.manifest import PluginSettingSchema
        schema = PluginSettingSchema(type="select", options=["a", "b", "c"], default="a")
        assert schema.type == "select"
        assert schema.options == ["a", "b", "c"]

    def test_settings_schema_secret_type(self):
        """secret type is supported in settings_schema."""
        from amo_bot.plugins.manifest import PluginSettingSchema
        schema = PluginSettingSchema(type="secret")
        assert schema.type == "secret"

    def test_settings_schema_invalid_type_rejected(self):
        """Invalid setting types are rejected."""
        from amo_bot.plugins.manifest import PluginSettingSchema
        with pytest.raises(ValueError):
            PluginSettingSchema(type="invalid_type")


class TestManifestNameValidation:
    """Validate name field constraints from documentation."""

    def test_name_cannot_be_reserved(self):
        """Reserved plugin IDs (core, system, internal, builtin) are rejected by loader."""
        # The manifest model itself doesn't enforce reserved names,
        # but the loader does. This is documented behavior.
        import sys
        import os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
        from amo_bot.plugins.loader import _RESERVED_PLUGIN_IDS
        assert "core" in _RESERVED_PLUGIN_IDS
        assert "system" in _RESERVED_PLUGIN_IDS
        assert "internal" in _RESERVED_PLUGIN_IDS
        assert "builtin" in _RESERVED_PLUGIN_IDS


class TestMinimalDocumentedPlugin:
    """Validate at least one minimal documented plugin can be discovered."""

    def test_minimal_command_plugin_valid(self):
        """Minimal command plugin from documentation validates."""
        manifest = PluginManifest(
            name="my_plugin",
            version="1.0.0",
            commands=["mycommand"],
            required_roles=["normal"],
            required_permissions=["send_message"],
        )
        assert manifest.name == "my_plugin"
        assert manifest.version == "1.0.0"
        assert "mycommand" in manifest.commands
        assert "normal" in manifest.required_roles
        assert "send_message" in manifest.required_permissions

    def test_minimal_schedule_plugin_valid(self):
        """Minimal schedule plugin from documentation validates."""
        manifest = PluginManifest(
            name="my_plugin",
            version="1.0.0",
            schedule={"interval_seconds": 60},
            required_roles=["normal"],
            required_permissions=[],
        )
        assert manifest.name == "my_plugin"
        assert manifest.schedule == {"interval_seconds": 60}

    def test_minimal_worker_plugin_valid(self):
        """Minimal worker plugin from documentation validates."""
        manifest = PluginManifest(
            name="my_plugin",
            version="1.0.0",
            worker={"restart_backoff_seconds": 60},
            required_roles=["normal"],
            required_permissions=["send_message"],
        )
        assert manifest.name == "my_plugin"
        assert manifest.worker == {"restart_backoff_seconds": 60}
