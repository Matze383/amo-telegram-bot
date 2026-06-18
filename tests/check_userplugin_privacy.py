#!/usr/bin/env python3
"""
Check user-plugin documentation examples for raw Telegram identifier logging.

Issue #81: copyable user-plugin examples must not log raw Telegram scope IDs
such as chat_id, thread_id, message_id, user_id, or subscription_key.
"""

from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
USERPLUGINS_MD = REPO_ROOT / "docs" / "USERPLUGINS.md"
YT_RSS_EXAMPLE = REPO_ROOT / "docs" / "examples" / "plugin-yt-rss-b2" / "main.py"
YT_RSS_PLUGIN = REPO_ROOT / "plugins" / "yt_rss" / "main.py"
PYTHON_EXAMPLES = (YT_RSS_EXAMPLE, YT_RSS_PLUGIN)

LOG_METHODS = {"debug", "info", "warning", "error", "critical", "exception"}
RAW_SCOPE_KEYS = {
    "chat_id",
    "thread_id",
    "message_thread_id",
    "message_id",
    "user_id",
    "from_id",
    "subscription_key",
    "run_id",
    "channel_id",
    "group_id",
}
SAFE_MARKERS = (
    "mask_id(",
    "masked_id(",
    "_masked_id(",
    "hash_id(",
    "hashed_id(",
    "_hashed_id(",
    "_topic_log_extra(",
)


@dataclass(frozen=True)
class Violation:
    path: Path
    line: int
    detail: str


def _extract_python_blocks(content: str) -> list[tuple[str, int]]:
    blocks: list[tuple[str, int]] = []
    for match in re.finditer(r"```python\n(.*?)```", content, re.DOTALL):
        line = content[: match.start()].count("\n") + 1
        blocks.append((match.group(1), line))
    return blocks


def _line_logs_raw_identifier(line: str) -> bool:
    if not re.search(r"\blogger\.(debug|info|warning|error|critical|exception)\s*\(", line):
        return False
    if not any(key in line for key in RAW_SCOPE_KEYS):
        return False
    return not any(marker in line for marker in SAFE_MARKERS)


def _check_markdown_python_blocks(path: Path) -> list[Violation]:
    content = path.read_text(encoding="utf-8")
    violations: list[Violation] = []
    for code, offset in _extract_python_blocks(content):
        for index, line in enumerate(code.splitlines(), start=offset + 1):
            if _line_logs_raw_identifier(line):
                violations.append(Violation(path, index, line.strip()))
    return violations


def _is_logger_call(node: ast.Call) -> bool:
    func = node.func
    return isinstance(func, ast.Attribute) and func.attr in LOG_METHODS


def _literal_key(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _string_contains_raw_scope_key(node: ast.AST) -> bool:
    values: list[str] = []
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        values.append(node.value)
    elif isinstance(node, ast.JoinedStr):
        values.extend(
            value.value
            for value in node.values
            if isinstance(value, ast.Constant) and isinstance(value.value, str)
        )
    return any(key in value for value in values for key in RAW_SCOPE_KEYS)


def _check_python_logging_calls(path: Path) -> list[Violation]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations: list[Violation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_logger_call(node):
            continue

        for arg in node.args:
            if _string_contains_raw_scope_key(arg):
                violations.append(Violation(path, node.lineno, "logger message mentions a raw scope key"))

        for keyword in node.keywords:
            if keyword.arg != "extra":
                continue
            if isinstance(keyword.value, ast.Dict):
                for key_node in keyword.value.keys:
                    key = _literal_key(key_node) if key_node is not None else None
                    if key in RAW_SCOPE_KEYS:
                        violations.append(Violation(path, node.lineno, f"logger extra contains raw key {key!r}"))
            elif isinstance(keyword.value, ast.Call):
                if isinstance(keyword.value.func, ast.Name) and keyword.value.func.id in {"_topic_log_extra"}:
                    continue
            else:
                violations.append(Violation(path, node.lineno, "logger extra is not statically privacy-checkable"))
    return violations


def find_violations() -> list[Violation]:
    violations: list[Violation] = []
    violations.extend(_check_markdown_python_blocks(USERPLUGINS_MD))
    for path in PYTHON_EXAMPLES:
        violations.extend(_check_python_logging_calls(path))
    return violations


def test_userplugin_examples_do_not_log_raw_scope_keys() -> None:
    violations = find_violations()
    assert not violations, "\n".join(f"{v.path.relative_to(REPO_ROOT)}:{v.line}: {v.detail}" for v in violations)


def main() -> int:
    violations = find_violations()
    if not violations:
        print("No raw Telegram identifiers found in user-plugin logging examples.")
        return 0

    print("Privacy violations found in user-plugin logging examples:")
    print("=" * 72)
    for violation in violations:
        rel_path = violation.path.relative_to(REPO_ROOT)
        print(f"{rel_path}:{violation.line}: {violation.detail}")
    print("=" * 72)
    print("Use masked or hashed identifiers for diagnostic correlation.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
