from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


CHECK_PATH = Path(__file__).resolve().parent / "check_userplugin_privacy.py"


def _load_privacy_check():
    spec = importlib.util.spec_from_file_location("check_userplugin_privacy", CHECK_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_userplugin_examples_do_not_log_raw_scope_keys() -> None:
    _load_privacy_check().test_userplugin_examples_do_not_log_raw_scope_keys()
