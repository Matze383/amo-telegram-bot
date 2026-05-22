# Userplugin Development Guide

> **DE:** Anleitung für Entwickler, die eigene Userplugins für AMO schreiben wollen.  
> **EN:** Guide for developers who want to write custom userplugins for AMO.

**Target Audience:**
- Human developers building userplugins
- AI agents/subagents generating userplugins from this guide

**Version:** 2026.05.22  
**Required AMO Version:** 2026.05.22 or later (for `rss.fetch` capability)

---

## Table of Contents

1. [Quick Overview](#quick-overview)
2. [File Structure](#file-structure)
3. [Manifest Specification](#manifest-specification)
4. [Do's and Don'ts](#dos-and-donts)
5. [Minimal Working Example](#minimal-working-example)
6. [Capability Reference](#capability-reference)
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
| Telegram API access | ✅ Direct | ❌ Via capabilities only |
| Database access | ✅ Direct | ❌ Via SQL capability (read-only) |
| HTTP requests | ✅ Direct | ✅ Via `rss.fetch` or `http` capability |
| File system | ✅ Direct | ❌ No direct access |
| Secrets management | ✅ Core only | ❌ Never handle secrets |
| Logging | ✅ Structured | ✅ Via core API only |

---

## File Structure

A valid userplugin requires exactly these files:

```
my_plugin/
├── plugin.json          # REQUIRED: Manifest
└── plugin.py            # REQUIRED: Implementation
```

**Optional files:**

```
my_plugin/
├── plugin.json
├── plugin.py
├── config_schema.json   # OPTIONAL: UI config schema
└── README.md            # OPTIONAL: Plugin documentation
```

**Plugin directory location:**
- Set via `AMO_PLUGIN_DIR` in `.env` (default: `./plugins`)
- Each plugin lives in its own subdirectory

---

## Manifest Specification

The `plugin.json` manifest is **mandatory** and must be valid JSON.

### Required Fields

```json
{
  "id": "my_plugin",
  "name": "My Plugin",
  "version": "1.0.0",
  "description": "What this plugin does (max 200 chars)",
  "author": "Your Name or Org",
  "entry_point": "plugin.py",
  "capabilities": []
}
```

### Field Reference

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | ✅ | Unique identifier: lowercase, alphanumeric + underscore only, max 32 chars. Pattern: `^[a-z][a-z0-9_]*$` |
| `name` | string | ✅ | Human-readable name, max 64 chars |
| `version` | string | ✅ | SemVer: `major.minor.patch` (e.g., `1.0.0`) |
| `description` | string | ✅ | Short description, max 200 chars |
| `author` | string | ✅ | Author name or organization |
| `entry_point` | string | ✅ | Filename of main Python file (must exist) |
| `capabilities` | array | ✅ | List of capability IDs (can be empty: `[]`) |
| `config_schema` | object | ❌ | JSON Schema for WebUI configuration |
| `min_amo_version` | string | ❌ | Minimum AMO version required (SemVer) |
| `permissions` | array | ❌ | Permission requirements (see below) |

### Capability Declaration

**Rule:** Declare ONLY capabilities you actually use.

```json
{
  "capabilities": [
    "rss.fetch",
    "sql.read"
  ]
}
```

**Available capabilities:**

| Capability | Purpose | Security Level |
|------------|---------|----------------|
| `rss.fetch` | Fetch and parse RSS feeds | Requires network |
| `http.get` | HTTP GET requests | Requires network |
| `http.post` | HTTP POST requests | Requires network |
| `sql.read` | Read-only database queries | Sandboxed |
| `memory.get` | Access conversation memory | Privacy-controlled |
| `memory.set` | Store conversation memory | Privacy-controlled |
| `telegram.send_message` | Send Telegram messages | Policy-gated |
| `telegram.send_photo` | Send images via Telegram | Policy-gated |

**DO NOT** declare capabilities "just in case." Undeclared capability calls will fail.

---

## Do's and Don'ts

### ✅ DO

- ✅ Use **absolute paths** for all file references
- ✅ Validate all inputs before processing
- ✅ Handle timeouts and network failures gracefully
- ✅ Use structured logging via core API
- ✅ Declare exact capability versions (e.g., `rss.fetch` not just `rss`)
- ✅ Test with minimal permissions first
- ✅ Return clear error messages to users
- ✅ Document config options in `config_schema`
- ✅ Clean up temporary resources in `finally` blocks
- ✅ Use type hints in Python code

### ❌ DON'T

- ❌ **Never** log secrets, tokens, or user data
- ❌ **Never** commit private artifacts to the repo
- ❌ **Never** bypass capabilities via direct network/FS access
- ❌ **Never** use relative paths like `./data` or `../config`
- ❌ **Never** declare capabilities you don't use
- ❌ **Never** store secrets in plugin code or config
- ❌ **Never** make blocking calls without timeouts
- ❌ **Never** access files outside plugin directory
- ❌ **Never** execute user-provided code/strings
- ❌ **Never** assume the plugin directory is writeable

---

## Minimal Working Example

### `plugin.json`

```json
{
  "id": "rss_notifier",
  "name": "RSS Notifier",
  "version": "1.0.0",
  "description": "Monitors RSS feeds and sends new items to Telegram",
  "author": "Example Author",
  "entry_point": "plugin.py",
  "capabilities": ["rss.fetch", "telegram.send_message"],
  "config_schema": {
    "type": "object",
    "properties": {
      "feed_url": {
        "type": "string",
        "format": "uri",
        "description": "RSS feed URL to monitor"
      },
      "check_interval_minutes": {
        "type": "integer",
        "minimum": 5,
        "maximum": 1440,
        "default": 60,
        "description": "Check interval in minutes"
      }
    },
    "required": ["feed_url"]
  },
  "min_amo_version": "2026.05.22"
}
```

### `plugin.py`

```python
"""
RSS Notifier Plugin for AMO Telegram Bot.

This is a minimal working example of a userplugin that:
1. Uses the rss.fetch capability to read RSS feeds
2. Sends notifications via telegram.send_message capability
3. Has configurable options via WebUI
"""

from typing import Dict, Any, Optional, List
import logging

# Type hints for better IDE support and documentation
FeedItem = Dict[str, Any]
Config = Dict[str, Any]


class Plugin:
    """
    Main plugin class. Must be named 'Plugin'.
    
    The core calls these lifecycle methods:
    - __init__: Plugin initialization
    - setup: Called with configuration from WebUI
    - run: Called on schedule (if background plugin)
    - handle_command: Called for command plugins
    """
    
    def __init__(self, core_api: Any) -> None:
        """
        Initialize plugin with core API reference.
        
        Args:
            core_api: The AMO core API object. Use this to call capabilities.
        """
        self.core = core_api
        self.logger = logging.getLogger(__name__)
        self.config: Optional[Config] = None
        self.seen_ids: set = set()
    
    def setup(self, config: Config) -> bool:
        """
        Setup plugin with configuration from WebUI.
        
        Args:
            config: Configuration dict matching config_schema
            
        Returns:
            True if setup successful, False otherwise
        """
        # Validate required config
        if not config.get("feed_url"):
            self.logger.error("Missing required config: feed_url")
            return False
        
        self.config = config
        self.logger.info(f"RSS Notifier configured for: {config['feed_url']}")
        return True
    
    def run(self) -> None:
        """
        Main execution method (called on schedule for background plugins).
        
        This example polls the RSS feed and sends new items.
        """
        if not self.config:
            self.logger.error("Plugin not configured")
            return
        
        feed_url = self.config["feed_url"]
        
        try:
            # Call rss.fetch capability
            # This is the ONLY way to fetch RSS feeds - no direct HTTP
            feed_data = self.core.call_capability(
                "rss.fetch",
                {
                    "url": feed_url,
                    "timeout_seconds": 30,
                    "max_items": 10
                }
            )
            
            if not feed_data or "items" not in feed_data:
                self.logger.warning(f"No items fetched from {feed_url}")
                return
            
            # Process new items
            new_items = self._filter_new_items(feed_data["items"])
            
            for item in new_items:
                self._send_notification(item)
                self.seen_ids.add(item["id"])
                
        except Exception as e:
            # Log error WITHOUT exposing sensitive data
            self.logger.error(f"Failed to fetch RSS feed: {type(e).__name__}")
            # DO NOT log: feed_url in full, exception message with URLs, etc.
    
    def _filter_new_items(self, items: List[FeedItem]) -> List[FeedItem]:
        """Filter to only unseen items."""
        return [item for item in items if item.get("id") not in self.seen_ids]
    
    def _send_notification(self, item: FeedItem) -> None:
        """Send Telegram notification for a feed item."""
        title = item.get("title", "No title")[:100]  # Truncate long titles
        link = item.get("link", "")
        
        message = f"📰 <b>{title}</b>\n{link}"
        
        try:
            # Use telegram.send_message capability
            self.core.call_capability(
                "telegram.send_message",
                {
                    "text": message,
                    "parse_mode": "HTML"
                }
            )
        except Exception as e:
            self.logger.error(f"Failed to send notification: {type(e).__name__}")


# Command handler (if plugin exposes commands)
def handle_command(command: str, args: List[str], context: Dict[str, Any]) -> str:
    """
    Handle plugin commands.
    
    Args:
        command: The command name (e.g., "rss")
        args: Command arguments
        context: Message context (user, chat, etc.)
        
    Returns:
        Response message text
    """
    if command == "rss":
        if not args:
            return "Usage: /rss <feed_url>"
        return f"Will check: {args[0]}"
    return "Unknown command"
```

---

## Capability Reference

### `rss.fetch`

**Purpose:** Fetch and parse RSS/Atom feeds.

**Request format:**

```python
{
    "url": "https://example.com/feed.xml",  # REQUIRED: Feed URL
    "timeout_seconds": 30,                   # OPTIONAL: Default 30
    "max_items": 10,                         # OPTIONAL: Max items to return
    "user_agent": "AMO-Bot/1.0"              # OPTIONAL: Custom UA
}
```

**Response format:**

```python
{
    "title": "Feed Title",
    "link": "https://example.com",
    "description": "Feed description",
    "items": [
        {
            "id": "unique-item-id",
            "title": "Item Title",
            "link": "https://example.com/article",
            "description": "Summary or content",
            "published": "2026-05-22T10:00:00Z",
            "author": "Author Name"
        }
    ],
    "last_modified": "Wed, 22 May 2026 10:00:00 GMT"
}
```

**Errors:**
- `CapabilityNotAvailable`: rss.fetch not declared in manifest
- `NetworkError`: HTTP request failed
- `ParseError`: Feed parsing failed
- `TimeoutError`: Request exceeded timeout

**Security:**
- URLs are validated against blocklist
- Response size is limited (max 1MB)
- Redirects are followed but limited (max 5)
- Only HTTP/HTTPS schemes allowed

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

```python
# ✅ Core handles secrets
result = self.core.call_capability("api.request", {"endpoint": "..."})
# The capability manages authentication internally
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
response = requests.get("https://api.example.com")  # BYPASSES CAPABILITIES
```

**REQUIRED:** Use capabilities

```python
# ✅ CORRECT
result = self.core.call_capability("rss.fetch", {"url": "..."})
```

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

### Config Schema

The `config_schema` field in `plugin.json` defines WebUI configuration options.

**JSON Schema format:**

```json
{
  "type": "object",
  "properties": {
    "enabled": {
      "type": "boolean",
      "default": true,
      "description": "Enable this plugin"
    },
    "api_key": {
      "type": "string",
      "description": "API key (stored securely by core, never in plugin)"
    },
    "interval": {
      "type": "integer",
      "minimum": 1,
      "maximum": 60,
      "default": 5,
      "description": "Check interval in minutes"
    },
    "channels": {
      "type": "array",
      "items": {
        "type": "string"
      },
      "description": "Channels to monitor"
    }
  },
  "required": ["enabled"]
}
```

### UI Behavior

- Config is edited in WebUI and persisted by core
- Plugin receives config via `setup()` method
- Schema validation happens before plugin sees the data
- Invalid configs never reach the plugin

### What Belongs in UI Config

| Belongs in UI | Does NOT Belong |
|--------------|-----------------|
| Feature toggles | Secrets (use core storage) |
| Numeric thresholds | File paths |
| List of channels | Raw credentials |
| Boolean flags | Internal state |
| Text templates | Derived/calculated values |

---

## Testing Requirements

### Minimum Test Coverage

Every userplugin MUST have:

1. **Manifest validation test**
   ```python
   def test_manifest_valid_json():
       import json
       with open("plugin.json") as f:
           manifest = json.load(f)
       assert "id" in manifest
       assert "capabilities" in manifest
   ```

2. **Plugin loads without errors**
   ```python
   def test_plugin_imports():
       from plugin import Plugin
       assert Plugin is not None
   ```

3. **Setup validates config**
   ```python
   def test_setup_requires_config():
       plugin = Plugin(mock_core)
       assert plugin.setup({}) is False  # Missing required config
   ```

4. **No direct network calls**
   ```python
   def test_no_direct_requests():
       import ast
       with open("plugin.py") as f:
           tree = ast.parse(f.read())
       for node in ast.walk(tree):
           if isinstance(node, ast.Import):
               for alias in node.names:
                   assert alias.name != "requests"
   ```

5. **No secrets in code**
   ```python
   def test_no_hardcoded_secrets():
       import re
       with open("plugin.py") as f:
           code = f.read()
       # Check for common secret patterns
       assert not re.search(r'[a-zA-Z0-9]{32,}', code)  # API keys
   ```

### QA Checklist Before Submission

- [ ] Plugin loads without errors in AMO
- [ ] All declared capabilities are actually used
- [ ] No capabilities are used that aren't declared
- [ ] Config schema validates correctly
- [ ] Handles missing/invalid config gracefully
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

```python
"""
[Plugin Name] for AMO Telegram Bot.

Generated: [Date]
Purpose: [Brief description]
Capabilities: [List of declared capabilities]
"""

from typing import Dict, Any, Optional
import logging


class Plugin:
    """Main plugin class."""
    
    def __init__(self, core_api: Any) -> None:
        self.core = core_api
        self.logger = logging.getLogger(__name__)
        self.config: Optional[Dict[str, Any]] = None
    
    def setup(self, config: Dict[str, Any]) -> bool:
        """Configure plugin."""
        self.config = config
        return True
    
    def run(self) -> None:
        """Main execution."""
        pass
```

---

## Quick Reference Card

```
MANIFEST (plugin.json)
├── id: lowercase, alphanumeric+underscore
├── version: SemVer (x.y.z)
├── entry_point: must exist
└── capabilities: [] ← declare only what you use

CODE (plugin.py)
├── class Plugin:
│   ├── __init__(self, core_api)
│   ├── setup(self, config) → bool
│   └── run(self) → None
└── NO: direct network, secrets, relative paths

CAPABILITIES
├── rss.fetch → RSS feeds
├── http.get/post → HTTP requests
├── sql.read → Database (read-only)
├── memory.get/set → Conversation memory
└── telegram.send_* → Telegram messages

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
  <sub>AMO Userplugin Guide — Version 2026.05.22</sub>
</p>
