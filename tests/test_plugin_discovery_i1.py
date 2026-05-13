from __future__ import annotations

import json

from amo_bot.plugins.loader import DiscoveryCode, PluginLoader


def _base_manifest(name: str) -> dict[str, object]:
    return {
        "name": name,
        "version": "1.0.0",
        "commands": ["/demo"],
        "required_roles": ["admin"],
    }


def test_discovery_is_deterministic_and_reports_found_outcomes(tmp_path) -> None:
    plugins_dir = tmp_path / "plugins"
    (plugins_dir / "b_dir").mkdir(parents=True)
    (plugins_dir / "a_dir").mkdir(parents=True)
    (plugins_dir / "b_dir" / "plugin.json").write_text(json.dumps(_base_manifest("plugin_b")), encoding="utf-8")
    (plugins_dir / "a_dir" / "plugin.json").write_text(json.dumps(_base_manifest("plugin_a")), encoding="utf-8")

    result = PluginLoader(str(plugins_dir)).discover()

    assert [m.name for m in result.valid] == ["plugin_a", "plugin_b"]
    assert [(o.plugin_dir, o.status, o.code) for o in result.outcomes if o.status == "discovery_found"] == [
        ("a_dir", "discovery_found", DiscoveryCode.FOUND),
        ("b_dir", "discovery_found", DiscoveryCode.FOUND),
    ]


def test_discovery_invalid_yaml_is_non_fatal(tmp_path) -> None:
    plugins_dir = tmp_path / "plugins"
    broken = plugins_dir / "broken"
    ok = plugins_dir / "ok"
    broken.mkdir(parents=True)
    ok.mkdir(parents=True)

    (broken / "plugin.json").write_text('{"name": "x",', encoding="utf-8")
    (ok / "plugin.json").write_text(json.dumps(_base_manifest("okplugin")), encoding="utf-8")

    result = PluginLoader(str(plugins_dir)).discover()

    assert [m.name for m in result.valid] == ["okplugin"]
    assert any(entry.plugin_dir == "broken" for entry in result.invalid)
    assert any(o.plugin_dir == "broken" and o.code == DiscoveryCode.INVALID_YAML for o in result.outcomes)


def test_discovery_missing_manifest_creates_invalid_outcome(tmp_path) -> None:
    plugins_dir = tmp_path / "plugins"
    (plugins_dir / "missing_manifest").mkdir(parents=True)

    result = PluginLoader(str(plugins_dir)).discover()

    assert result.valid == []
    assert any(
        o.plugin_dir == "missing_manifest"
        and o.status == "discovery_invalid"
        and o.code == DiscoveryCode.MISSING_MANIFEST
        for o in result.outcomes
    )


def test_discovery_duplicate_plugin_ids_are_blocked_deterministically(tmp_path) -> None:
    plugins_dir = tmp_path / "plugins"
    (plugins_dir / "a_first").mkdir(parents=True)
    (plugins_dir / "b_second").mkdir(parents=True)

    manifest = _base_manifest("dup_plugin")
    (plugins_dir / "a_first" / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    (plugins_dir / "b_second" / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")

    result = PluginLoader(str(plugins_dir)).discover()

    assert [m.name for m in result.valid] == ["dup_plugin"]
    assert len(result.invalid) == 1
    assert result.invalid[0].plugin_dir == "b_second"
    assert any(
        o.plugin_dir == "b_second" and o.status == "discovery_blocked" and o.code == DiscoveryCode.DUPLICATE_ID
        for o in result.outcomes
    )


def test_discovery_disabled_plugin_is_ignored(tmp_path) -> None:
    plugins_dir = tmp_path / "plugins"
    plugin_dir = plugins_dir / "weather"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(json.dumps(_base_manifest("weather")), encoding="utf-8")
    (plugins_dir / "weather.disabled").write_text("", encoding="utf-8")

    result = PluginLoader(str(plugins_dir)).discover()

    assert result.valid == []
    assert any(
        o.plugin_dir == "weather" and o.status == "discovery_disabled" and o.code == DiscoveryCode.DISABLED
        for o in result.outcomes
    )


def test_discovery_rejects_reserved_id(tmp_path) -> None:
    plugins_dir = tmp_path / "plugins"
    plugin_dir = plugins_dir / "coreplugin"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(json.dumps(_base_manifest("core")), encoding="utf-8")

    result = PluginLoader(str(plugins_dir)).discover()

    assert result.valid == []
    assert len(result.invalid) == 1
    assert any(o.plugin_dir == "coreplugin" and o.code == DiscoveryCode.RESERVED_ID for o in result.outcomes)


def test_discovery_rejects_unsupported_trigger_declarations(tmp_path) -> None:
    plugins_dir = tmp_path / "plugins"
    plugin_dir = plugins_dir / "triggered"
    plugin_dir.mkdir(parents=True)
    payload = _base_manifest("triggered")
    payload["triggers"] = ["cron", "ki-triggered"]
    (plugin_dir / "plugin.json").write_text(json.dumps(payload), encoding="utf-8")

    result = PluginLoader(str(plugins_dir)).discover()

    assert result.valid == []
    assert len(result.invalid) == 1
    assert any(o.plugin_dir == "triggered" and o.code == DiscoveryCode.INVALID_TRIGGER_TYPE for o in result.outcomes)


def test_discovery_rejects_combined_interval_and_cron_triggers(tmp_path) -> None:
    plugins_dir = tmp_path / "plugins"
    plugin_dir = plugins_dir / "mixed_triggers"
    plugin_dir.mkdir(parents=True)
    payload = _base_manifest("mixed_triggers")
    payload["triggers"] = ["interval", "cron"]
    (plugin_dir / "plugin.json").write_text(json.dumps(payload), encoding="utf-8")

    result = PluginLoader(str(plugins_dir)).discover()

    assert result.valid == []
    assert len(result.invalid) == 1
    assert any(o.plugin_dir == "mixed_triggers" and o.code == DiscoveryCode.INVALID_TRIGGER_TYPE for o in result.outcomes)
