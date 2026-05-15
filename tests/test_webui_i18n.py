from __future__ import annotations

import re
from pathlib import Path

from amo_bot.webui.i18n import _TRANSLATIONS


_TEMPLATE_FILES = [
    "base.html",
    "login.html",
    "dashboard.html",
    "users.html",
    "groups.html",
    "plugins.html",
]


def _template_i18n_keys() -> set[str]:
    templates_dir = Path(__file__).resolve().parents[1] / "src" / "amo_bot" / "webui" / "templates"
    pattern = re.compile(r"t\('([^']+)'\)")
    keys: set[str] = set()
    for name in _TEMPLATE_FILES:
        keys.update(pattern.findall((templates_dir / name).read_text(encoding="utf-8")))
    return keys


def test_template_i18n_keys_exist_in_de_and_en() -> None:
    keys = _template_i18n_keys()
    de_keys = set(_TRANSLATIONS["de"].keys())
    en_keys = set(_TRANSLATIONS["en"].keys())

    missing_in_de = sorted(keys - de_keys)
    missing_in_en = sorted(keys - en_keys)

    assert missing_in_de == [], f"Missing DE translation keys: {missing_in_de}"
    assert missing_in_en == [], f"Missing EN translation keys: {missing_in_en}"
