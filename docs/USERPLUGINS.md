# Userplugin Development Guide

> **DE:** Anleitung für Entwickler, die eigene Userplugins für AMO schreiben wollen.
> **EN:** Guide for developers who want to write custom userplugins for AMO.

**Target Audience:**
- Human developers building userplugins
- AI agents/subagents generating userplugins from this guide

**Version:** 2026.05.23
**Required AMO Version:** 2026.05.22 or later

---

## Table of Contents

1. [Quick Overview](#quick-overview)
2. [File Structure](#file-structure)
3. [Manifest Specification](#manifest-specification)
4. [Do's and Don'ts](#dos-and-donts)
5. [Minimal Working Example](#minimal-working-example)
6. [Host API Reference](#host-api-reference)
7. [Security Rules](#security-rules)
8. [UI/Config Integration](#uiconfig-integration)
9. [Testing Requirements](#testing-requirements)
10. [AI-Specific Guidelines](#ai-specific-guidelines)

---

## Quick Overview

**What is a Userplugin?**

A userplugin extends AMO's functionality without modifying core code. It runs in a sandboxed environment with explicitly declared capabilities.

**Core vs. Userplugin Responsibility:**

| Aspect | Core (AMO) | Userplugin |
|--------|------------|------------|
| Telegram API access | ✅ Direct | ❌ Via host_api only |
| Database access | ✅ Direct | ❌ No direct access |
| HTTP requests | ✅ Direct | ❌ Via permissions (rss.fetch) |
| File system | ✅ Direct | ❌ No direct access |
| Secrets management | ✅ Core only | ❌ Never handle secrets |
| Logging | ✅ Structured | ✅ Via logging module only |

---

## File Structure

A valid userplugin requires exactly these files:

```
my_plugin/
├── plugin.yaml          # REQUIRED: Manifest (YAML or JSON)
└── main.py              # REQUIRED: Implementation (must be named main.py)
```

**Supported manifest formats:** `plugin.yaml`, `plugin.yml`, `plugin.json`, or `manifest.json`

**Optional files:**

```
my_plugin/
├── plugin.yaml
├── main.py
└── README.md            # OPTIONAL: Plugin documentation
```

**Plugin directory location:**
- Set via `AMO_PLUGIN_DIR` in `.env` (default: `./plugins`)
- Each plugin lives in its own subdirectory

---

## Manifest Specification

The manifest is **mandatory** and must be valid YAML or JSON.

### Required Fields

```yaml
name: my_plugin
version: "1.0.0"
description: What this plugin does (max 200 chars)
commands:
  - mycommand
required_permissions:
  - send_message
```

OR with schedule:

```yaml
name: my_plugin
version: "1.0.0"
description: What this plugin does (max 200 chars)
schedule:
  interval_seconds: 60
required_permissions: []
```

### Field Reference

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | ✅ | Unique plugin name: lowercase, alphanumeric + underscore/hyphen, 3-50 chars. Pattern: `^[a-z][a-z0-9_-]{2,49}$`. Cannot be reserved: `core`, `system`, `internal`, `builtin` |
| `version` | string | ✅ | Plugin version string (any format) |
| `description` | string | ❌ | Short description |
| `commands` | array | ❌* | List of command names (e.g., `["rss", "weather"]`). Required if no schedule or worker |
| `schedule` | object | ❌* | Schedule config with `interval_seconds` (≥10) OR `cron` (5-field cron expression). Required if no commands or worker |
| `worker` | object | ❌* | Worker config with `restart_backoff_seconds`. Required if no commands or schedule |
| `required_roles` | array | ✅* | List of allowed roles. At least one of `required_roles` OR `required_permissions` must be defined. |
| `required_permissions` | array | ✅* | List of required capability permissions. At least one of `required_roles` OR `required_permissions` must be defined. |
| `settings_schema` | object | ❌ | Schema for WebUI configuration settings |

*Note: At least one of `commands`, `schedule`, or `worker` must be defined.*

*Note: At least one of `required_roles` or `required_permissions` must be defined.*

### Permission Declaration

**Rule:** Declare ONLY permissions you actually use in your plugin code.

```yaml
required_permissions:
  - rss.fetch
  - send_message
```

**Available permissions:**

| Permission | Purpose |
|------------|---------|
| `rss.fetch` | Fetch and parse RSS feeds |
| `send_message` | Send/reply to Telegram messages; also required for `send_photo`/`send_document` in sandbox |

**Note:** The runtime validates that plugins using RSS-related settings have `rss.fetch` permission declared. See `service.py` `_uses_rss_behavior()` for details.

**Sandbox-only operations:** `send_photo` and `send_document` are available only in the sandbox command host API and are gated by the `send_message` permission. They are not available in the non-sandbox runtime.

**DO NOT** declare permissions "just in case." Undeclared permission calls will fail at runtime.

---

## Do's and Don'ts

### ✅ DO

- ✅ Use **async/await** for all host API calls
- ✅ Validate all inputs before processing
- ✅ Handle timeouts and network failures gracefully
- ✅ Use structured logging via the `logging` module
- ✅ Declare exact permissions in `required_permissions`
- ✅ Test with minimal permissions first
- ✅ Return clear error messages to users
- ✅ Document settings in `settings_schema`
- ✅ Clean up temporary resources in `finally` blocks
- ✅ Use type hints in Python code

### ❌ DON'T

- ❌ **Never** log secrets, tokens, or user data
- ❌ **Never** commit private artifacts to the repo
- ❌ **Never** bypass sandbox via direct network/FS access
- ❌ **Never** use relative paths like `./data` or `../config`
- ❌ **Never** declare permissions you don't use
- ❌ **Never** store secrets in plugin code or config
- ❌ **Never** make blocking calls without timeouts
- ❌ **Never** access files outside plugin directory
- ❌ **Never** execute user-provided code/strings
- ❌ **Never** assume the plugin directory is writeable

---

## Minimal Working Example

### `plugin.yaml`

```yaml
name: rss_notifier
version: "1.0.0"
description: Monitors RSS feeds and sends new items to Telegram
commands:
  - rss
required_roles:
  - normal
  - vip
  - admin
  - owner
required_permissions:
  - rss.fetch
  - send_message
settings_schema:
  feed_url:
    type: text
    required: true
    description: RSS feed URL to monitor
  check_interval_minutes:
    type: number
    required: false
    default: 60
    min: 5
    max: 1440
    description: Check interval in minutes
```

### `main.py`

```python
"""
RSS Notifier Plugin for AMO Telegram Bot.

This is a minimal working example of a userplugin that:
1. Receives command invocations from the core
2. Uses the host API to send messages
3. Uses attachments for image analysis
"""

from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)


async def handle_command(context: Dict[str, Any], host_api: Any) -> None:
    """
    Main entry point for command execution.

    Args:
        context: Command context with keys:
            - plugin_id: Plugin name
            - run_id: Unique execution ID
            - chat_id: Telegram chat ID
            - message_id: Message ID
            - message_thread_id: Thread ID (optional)
            - user_id: Telegram user ID
            - role: User role (ignore/normal/vip/admin/owner)
            - command_name: Command that was invoked
            - argument: Command argument text
            - attachments: List of attachment dicts
            - reply_to_image: Image context for analysis
        host_api: API object for interacting with Telegram

    Raises:
        Any exception is caught and logged by the runtime.
    """
    command = context.get("command_name", "")
    argument = context.get("argument", "")
    chat_id = context.get("chat_id")
    message_id = context.get("message_id")

    if command == "rss":
        if not argument:
            await host_api.reply(chat_id, message_id, "Usage: /rss <feed_url>")
            return
        await host_api.reply(chat_id, message_id, f"Will check: {argument}")
    else:
        await host_api.reply(chat_id, message_id, "Unknown command")
```

---

## Host API Reference

### Host API Methods

The `host_api` object provides these async methods:

**`await host_api.send_message(chat_id: int, text: str)`**
- Sends a message to the specified chat
- Requires `send_message` permission
- Text is truncated to 4000 chars
- Raises `PluginCapabilityError` if permission not granted

**`await host_api.reply(chat_id: int, message_id: int, text: str)`**
- Replies to a specific message
- Requires `send_message` permission
- Text is truncated to 4000 chars

**`await host_api.send_photo(chat_id: int, file_path: str, caption: str = "", *, message_thread_id: int | None = None, mime_type: str | None = None)`** (Sandbox-only)
- Sends a photo to the specified chat
- Requires `send_message` permission (gated by `send_message`)
- Only available in sandbox command runtime

**`await host_api.send_document(chat_id: int, file_path: str, caption: str = "", *, message_thread_id: int | None = None, mime_type: str | None = None)`** (Sandbox-only)
- Sends a document to the specified chat
- Requires `send_message` permission (gated by `send_message`)
- Only available in sandbox command runtime

**Context attachments** (`context["attachments"]`):
List of attachment dicts with keys:
- `source_kind`: Source type
- `type_hint`: "image" or "image_document"
- `file_id`: Telegram file ID
- `file_unique_id`: Unique file identifier
- `width`, `height`: Image dimensions
- `size`: File size in bytes
- `mime_type`: MIME type
- `media_ref`: Downloaded media reference (if available)

**Context for image analysis** (`context["reply_to_image"]`):
Dict with image context when replying to an image, with keys:
- `ok`: Boolean indicating if image is valid
- `media_ref`: Media reference for analysis
- `reason_code`: Error code if not valid

**Note:** Plugins run in a sandboxed subprocess. Operations are limited and monitored.

---

## Security Rules

### 1. Secret Handling

**ABSOLUTELY FORBIDDEN:**

```python
# ❌ NEVER DO THIS
token = "my-secret-token"  # Hardcoded
logger.info(f"Token: {user_token}")  # Logging secrets
return {"error": f"Failed with token {api_key}"}  # Returning secrets
```

**Correct approach:**

Secrets are configured via `settings_schema` with `type: secret`. The core handles secret storage and validation. Plugins never see the actual secret values.
```

### 2. Private Artifacts

**DO NOT commit:**
- `.env` files
- `__pycache__/` directories
- `.pyc` files
- Test data with real user info
- Debug logs
- IDE configuration files

**Always include in `.gitignore`:**

```gitignore
# Plugin-specific
__pycache__/
*.pyc
*.pyo
*.egg-info/
dist/
build/
.env
*.log
```

### 3. Network Access

**FORBIDDEN:** Direct network access

```python
# ❌ NEVER DO THIS
import requests
response = requests.get("https://api.example.com")  # BYPASSES SANDBOX
```

**Note:** RSS/network operations must be handled by the runtime. Plugins with RSS-related settings must declare `rss.fetch` in `required_permissions`.

### 4. Filesystem Access

**FORBIDDEN:** Direct filesystem access outside plugin dir

```python
# ❌ NEVER DO THIS
open("/etc/passwd")  # Absolute escape
open("../../config.json")  # Relative escape
open(os.environ["HOME"] + "/.secrets")  # Home directory escape
```

**ALLOWED:** Plugin directory only (read-only)

```python
# ✅ CORRECT: Read from plugin directory only
import os
plugin_dir = os.path.dirname(os.path.abspath(__file__))
config_path = os.path.join(plugin_dir, "data.json")
```

---

## UI/Config Integration

### Settings Schema

The `settings_schema` field in `plugin.yaml` defines WebUI configuration options.

**Supported setting types:** `text`, `number`, `bool`, `select`, `secret`

**YAML format:**

```yaml
settings_schema:
  enabled:
    type: bool
    required: false
    default: true
    description: Enable this plugin
  api_key:
    type: secret
    required: false
    description: API key (stored securely by core)
  interval:
    type: number
    required: false
    default: 5
    min: 1
    max: 60
    description: Check interval in minutes
  mode:
    type: select
    required: false
    default: basic
    options:
      - basic
      - advanced
      - expert
    description: Plugin mode
  webhook_url:
    type: text
    required: false
    pattern: "^https?://.*"
    description: Webhook URL (must be HTTP/HTTPS)
```

**Setting type constraints:**
- `number`: `default`, `min`, `max` must be numeric; `min` must be ≤ `max`
- `bool`: `default` must be true/false
- `select`: `options` must be non-empty strings; `default` must be in options
- `text`/`secret`: `default` must be string; `pattern` must be valid regex

### UI Behavior

- Settings are edited in WebUI and persisted by core
- Plugin receives settings via the `settings_schema` validation
- Schema validation happens before plugin sees the data
- Invalid settings never reach the plugin
- Plugins cannot write to their directory; all state must be managed via host API

### What Belongs in UI Config

| Belongs in UI | Does NOT Belong |
|--------------|-----------------|
| Feature toggles | Secrets (use secret type in settings_schema) |
| Numeric thresholds | File paths |
| List of channels | Raw credentials |
| Boolean flags | Internal state |
| Text templates | Derived/calculated values |

**Settings are defined in `settings_schema` with types:** `text`, `number`, `bool`, `select`, `secret`

---

## Testing Requirements

### Minimum Test Coverage

Every userplugin MUST have:

1. **Manifest validation test**
   ```python
   def test_manifest_valid_yaml():
       import yaml
       with open("plugin.yaml") as f:
           manifest = yaml.safe_load(f)
       assert "name" in manifest
       assert "version" in manifest
       assert "required_permissions" in manifest or "required_roles" in manifest
   ```

2. **Plugin loads without errors**
   ```python
   def test_plugin_imports():
       # main.py must define handle_command
       import importlib.util
       spec = importlib.util.spec_from_file_location("main", "main.py")
       module = importlib.util.module_from_spec(spec)
       spec.loader.exec_module(module)
       assert callable(getattr(module, "handle_command", None))
   ```

3. **No direct network calls**
   ```python
   def test_no_direct_requests():
       import ast
       with open("main.py") as f:
           tree = ast.parse(f.read())
       for node in ast.walk(tree):
           if isinstance(node, ast.Import):
               for alias in node.names:
                   assert alias.name != "requests"
   ```

4. **No secrets in code**
   ```python
   def test_no_hardcoded_secrets():
       import re
       with open("main.py") as f:
           code = f.read()
       # Check for common secret patterns
       assert not re.search(r'[a-zA-Z0-9]{32,}', code)  # API keys
   ```

### QA Checklist Before Submission

- [ ] Plugin loads without errors in AMO
- [ ] All declared permissions are actually used
- [ ] No permissions are used that aren't declared
- [ ] Settings schema validates correctly
- [ ] Handles missing/invalid settings gracefully
- [ ] No secrets in code or logs
- [ ] No direct network/FS access
- [ ] Uses absolute paths only
- [ ] Has proper error handling
- [ ] Tests pass with `pytest -q`

---

## AI-Specific Guidelines

**For AI agents generating userplugins:**

### Absolute Paths Only

```python
# ❌ NEVER
data_file = "./data/items.json"
config = "../config.json"

# ✅ ALWAYS
import os
plugin_dir = os.path.dirname(os.path.abspath(__file__))
data_file = os.path.join(plugin_dir, "data", "items.json")
```

### No OpenClaw Artifacts

**REMOVE before generating:**
- References to OpenClaw paths (e.g., `/home/claw/.openclaw/`)
- Session-specific data
- Internal tooling references
- Debug prints from generation

### No Scope Creep

**Stick to the request:**
- If asked for RSS plugin, don't add weather API
- If asked for 3 commands, don't add 10
- Minimal viable implementation first

### No Public Actions Without Approval

**FORBIDDEN:**
- Pushing to repository
- Creating public issues
- Posting to social media
- Any external network action beyond capability calls

**REQUIRED:**
- Return generated code to user for review
- Let user decide on submission/deployment
- Never auto-commit or auto-push

### Code Generation Template

When asked to generate a plugin, use this structure:

**plugin.yaml:**

```yaml
name: my_plugin
version: "1.0.0"
description: Brief description of what this plugin does
commands:
  - mycommand
required_roles:
  - normal
required_permissions:
  - send_message
```

**main.py:**

```python
"""
[Plugin Name] for AMO Telegram Bot.

Generated: [Date]
Purpose: [Brief description]
Required permissions: [List of declared permissions]
"""

from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)


async def handle_command(context: Dict[str, Any], host_api: Any) -> None:
    """Main entry point for command execution."""
    command = context.get("command_name", "")
    argument = context.get("argument", "")
    chat_id = context.get("chat_id")
    message_id = context.get("message_id")

    # Handle your command logic here
    await host_api.reply(chat_id, message_id, "Hello from my plugin!")
```
```

---

## Quick Reference Card

```
MANIFEST (plugin.yaml)
├── name: lowercase, alphanumeric+underscore/hyphen, 3-50 chars
├── version: any format
├── commands: [] ← command names
├── schedule: {interval_seconds: N} or {cron: "..."}
├── worker: {restart_backoff_seconds: N}
├── required_roles: ["normal", "vip", "admin", "owner"]
└── required_permissions: ["rss.fetch", "send_message"]

CODE (main.py)
├── async def handle_command(context, host_api)
├── context: dict with chat_id, message_id, command_name, argument, attachments, etc.
├── host_api.send_message(chat_id, text)
└── host_api.reply(chat_id, message_id, text)

SETTINGS_SCHEMA
├── text: string with optional pattern regex
├── number: integer with min/max
├── bool: true/false
├── select: list of options
└── secret: string (stored securely)

SECURITY
├── NO secrets in code/logs
├── NO direct network (use capabilities)
├── NO filesystem escape
└── NO public actions without approval
```

---

## Related Documentation

- [CONTRIBUTING.md](../CONTRIBUTING.md) — General contribution guidelines
- [SETUP_EN.md](SETUP_EN.md) — Setup instructions
- [CHANGELOG.md](../CHANGELOG.md) — Version history

---

<p align="center">
  <sub>AMO Userplugin Guide — Version 2026.05.23</sub>
</p>
