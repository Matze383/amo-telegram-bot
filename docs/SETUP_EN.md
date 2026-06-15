# AMO Telegram Bot — Setup Guide

Complete setup instructions for running the bot locally.

---

## Prerequisites

- Python 3.12 or higher
- Windows, macOS, or Linux
- Telegram Bot Token (from [@BotFather](https://t.me/BotFather))
- Optional: AI provider for `/ask` command:
  - Local [Ollama](https://ollama.com/) instance, **OR**
  - OpenAI API key, **OR**
  - Anthropic API key, **OR**
  - Google/Gemini API key, **OR**
  - OpenRouter API key, **OR**
  - [Groq](https://groq.com/) API key, **OR**
  - Mistral API key, **OR**
  - xAI API key, **OR**
  - DeepSeek API key, **OR**
  - Together API key, **OR**
  - Fireworks API key, or
  - AWS credentials/profile for Amazon Bedrock, or
  - SGLang local server instance

---

## Platform-Specific Quick Start

### Linux / macOS

```bash
# Clone repository
git clone <repository-url>
cd AMO-telegram-bot

# Create virtual environment
python3.12 -m venv venv

# Activate virtual environment
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Copy and edit configuration
cp .env.example .env
# Edit .env with your BOT_TOKEN, WEBUI_PASSWORD, etc.

# Run the bot
python main.py
```

### Windows (PowerShell)

```powershell
# Clone repository
git clone <repository-url>
cd AMO-telegram-bot

# Create virtual environment
python -m venv venv

# Activate virtual environment
.\venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt

# Copy and edit configuration
copy .env.example .env
# Edit .env with your BOT_TOKEN, WEBUI_PASSWORD, etc. (use Notepad, VS Code, etc.)

# Run the bot
python main.py
```

### Windows (Command Prompt / cmd.exe)

```cmd
REM Clone repository
git clone <repository-url>
cd AMO-telegram-bot

REM Create virtual environment
python -m venv venv

REM Activate virtual environment
venv\Scripts\activate.bat

REM Install dependencies
pip install -r requirements.txt

REM Copy configuration
copy .env.example .env
REM Edit .env with your BOT_TOKEN, WEBUI_PASSWORD, etc.

REM Run the bot
python main.py
```

> **Windows Note:** If PowerShell execution policy blocks script execution, run PowerShell as Administrator and execute: `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`

---

---

## Installation

### 1. Clone and Setup

**Linux / macOS:**

```bash
git clone <repository-url>
cd AMO-telegram-bot
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

**Windows (PowerShell):**

```powershell
git clone <repository-url>
cd AMO-telegram-bot
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**Windows (Command Prompt):**

```cmd
git clone <repository-url>
cd AMO-telegram-bot
python -m venv venv
venv\Scripts\activate.bat
pip install -r requirements.txt
```

### 2. Environment Configuration

Copy the example file and edit:

**Linux / macOS:**

```bash
cp .env.example .env
```

**Windows (PowerShell):**

```powershell
copy .env.example .env
```

**Windows (Command Prompt):**

```cmd
copy .env.example .env
```

Edit `.env` with your values:

```ini
# Required: Telegram
BOT_TOKEN=your_bot_token_here
BOT_USERNAME=your_bot_username

# Required: WebUI
WEBUI_PASSWORD=your_secure_password
WEBUI_OWNER_TELEGRAM_ID=your_telegram_user_id

# AI Provider Configuration
AI_PROVIDER=ollama  # ollama (default), openai, anthropic, google, openrouter, groq, mistral, xai, deepseek, together, fireworks, amazon-bedrock, litellm, lmstudio, vllm, or sglang

# Optional: OpenAI (for /ask command)
# OPENAI_API_KEY=your-openai-api-key-here
# OPENAI_MODEL=gpt-4o-mini
# OPENAI_TIMEOUT_SECONDS=30

# Optional: Anthropic (for /ask command)
# ANTHROPIC_API_KEY=your-anthropic-api-key-here
# ANTHROPIC_MODEL=anthropic/claude-opus-4-6
# ANTHROPIC_TIMEOUT_SECONDS=30
# ANTHROPIC_BASE_URL=https://api.anthropic.com

# Optional: Google/Gemini (for /ask command)
# GEMINI_API_KEY=your-google-api-key-here
# GEMINI_MODEL=google/gemini-3-flash-preview
# GEMINI_TIMEOUT_SECONDS=30
# GEMINI_BASE_URL=https://generativelanguage.googleapis.com

# Optional: OpenRouter (for /ask command)
# OPENROUTER_API_KEY=your-openrouter-api-key-here
# OPENROUTER_MODEL=openrouter/auto
# OPENROUTER_TIMEOUT_SECONDS=30
# OPENROUTER_BASE_URL=https://openrouter.ai/api/v1

# Optional: Groq (for /ask command)
# GROQ_API_KEY=
# GROQ_MODEL=groq/llama-3.1-8b-instant
# GROQ_TIMEOUT_SECONDS=30
# GROQ_BASE_URL=https://api.groq.com/openai/v1

# Optional: Mistral (for /ask command)
# MISTRAL_API_KEY=
# MISTRAL_MODEL=mistral/mistral-large-latest
# MISTRAL_TIMEOUT_SECONDS=30
# MISTRAL_BASE_URL=https://api.mistral.ai/v1

# Optional: xAI (for /ask command)
# XAI_API_KEY=
# XAI_MODEL=xai/grok-4.3
# XAI_TIMEOUT_SECONDS=30
# XAI_BASE_URL=https://api.x.ai/v1

# Optional: DeepSeek (for /ask command)
# DEEPSEEK_API_KEY=
# DEEPSEEK_MODEL=deepseek/deepseek-v4-flash
# DEEPSEEK_TIMEOUT_SECONDS=30
# DEEPSEEK_BASE_URL=https://api.deepseek.com/v1

# Optional: Together AI (for /ask command)
# TOGETHER_API_KEY=
# TOGETHER_MODEL=together/moonshotai/Kimi-K2.5
# TOGETHER_TIMEOUT_SECONDS=30
# TOGETHER_BASE_URL=https://api.together.xyz/v1

# Optional: LiteLLM (for /ask command)
# LITELLM_API_KEY=
# LITELLM_MODEL=openai/gpt-4o-mini
# LITELLM_TIMEOUT_SECONDS=30
# LITELLM_BASE_URL=https://api.litellm.ai

# Optional: LM Studio (for /ask command) - local OpenAI-compatible server
# LMSTUDIO_API_KEY=           # optional; omit for unauthenticated local servers
# LMSTUDIO_MODEL=local-model  # model name exposed by LM Studio
# LMSTUDIO_TIMEOUT_SECONDS=60  # higher timeout recommended for local inference
# LMSTUDIO_BASE_URL=http://127.0.0.1:1234/v1

# Optional: vLLM (for /ask command) - local OpenAI-compatible server (e.g. OpenClaw backend)
# VLLM_API_KEY=               # optional; omit for unauthenticated local servers
# VLLM_MODEL=                 # model name exposed by the vLLM server
# VLLM_TIMEOUT_SECONDS=60     # higher timeout recommended for local inference
# VLLM_BASE_URL=http://127.0.0.1:8000/v1

# Optional: SGLang (for /ask command) - local OpenAI-compatible server
# SGLANG_API_KEY=             # optional; omit for unauthenticated local servers
# SGLANG_MODEL=               # model name exposed by the SGLang server
# SGLANG_TIMEOUT_SECONDS=60   # higher timeout recommended for local inference
# SGLANG_BASE_URL=http://127.0.0.1:8000/v1

# Optional: Amazon Bedrock (for /ask command) - AWS Cloud
# AWS_ACCESS_KEY_ID=          # optional; omit when using AWS profile
# AWS_SECRET_ACCESS_KEY=      # optional; omit when using AWS profile
# AWS_REGION=                 # e.g. us-east-1 (default: us-east-1)
# BEDROCK_MODEL=              # e.g. anthropic.claude-3-5-sonnet-20241022-v2:0
# BEDROCK_TIMEOUT_SECONDS=60  # higher timeout recommended for AWS API

# Optional: Ollama (for /ask command)
OLLAMA_URL=http://127.0.0.1:11434
OLLAMA_MODEL=llama3.1
OLLAMA_TIMEOUT_SECONDS=20
OLLAMA_MAX_PROMPT_CHARS=4000
OLLAMA_MAX_PREDICT_TOKENS=512
OLLAMA_MAX_RESPONSE_CHARS=1500
# OLLAMA_REQUEST_ENDPOINT=generate  # generate (default) or chat; invalid values fail startup validation
# OLLAMA_STREAMING_MODE=off  # off (default), collect_only, live_edit (parsed gate only; no live Telegram streaming)

# Optional: Ollama Model Policy (task-based model selection)
# OLLAMA_MODEL_POLICY_ENABLED=false  # false (default), true
# OLLAMA_THINKING_MODEL=             # Model for complex tasks (e.g., deepseek-r1:14b)
# OLLAMA_NON_THINKING_MODEL=         # Model for simple tasks (e.g., qwen2.5-coder:14b)
# OLLAMA_THINKING_TASK_TYPES=web_research,sports,news,answer_synthesis
# OLLAMA_SIMPLE_PROMPT_MAX_CHARS=240
# OLLAMA_THINKING_TIMEOUT_SECONDS=      # optional; default: OLLAMA_TIMEOUT_SECONDS
# OLLAMA_NON_THINKING_TIMEOUT_SECONDS=  # optional; default: OLLAMA_TIMEOUT_SECONDS
# OLLAMA_THINKING_BUDGET_MAX_PROMPT_CHARS=      # optional; default: OLLAMA_MAX_PROMPT_CHARS
# OLLAMA_NON_THINKING_BUDGET_MAX_PROMPT_CHARS=  # optional; default: OLLAMA_MAX_PROMPT_CHARS

# Optional: Database (defaults to SQLite)
DATABASE_URL=sqlite:///./data/amo_bot.db

# Optional: MariaDB/MySQL (instead of SQLite)
# DATABASE_URL=mysql+pymysql://amo_bot:<password>@<mariadb-host>:3306/amo_bot?charset=utf8mb4

# Optional: Plugin directory
AMO_PLUGIN_DIR=./plugins

# Optional: Logging
LOG_LEVEL=info                # debug|info|warning|error (default: info)
LOG_FORMAT=text               # text|json (default: text)
# LOG_FILE=./logs/amo.log     # Optional file path for log output
LOG_DEBUG_SCOPES=             # Comma-separated components to set to DEBUG (e.g.: ai.router,plugin.runtime)
LOG_INCLUDE_PRIVATE_IDS=0     # Set to 1 to include unmasked IDs in structured logs

# Optional: WebUI settings
WEBUI_HOST=127.0.0.1
WEBUI_PORT=8080
WEBUI_SESSION_TTL_SECONDS=3600

# Security settings (Block 1)
# WEBUI_PUBLIC_MODE=false
# WEBUI_REQUIRE_HTTPS=false
# WEBUI_SESSION_COOKIE_SECURE=

# Security settings (Block 2 - Login Protection)
# WEBUI_LOGIN_DELAY_BASE_SECONDS=0.25
# WEBUI_LOGIN_DELAY_MAX_SECONDS=2.0
```

> **Config Priority:** When starting locally, `.env` overrides shell environment variables. Set `AMO_ENV_OVERRIDE=0` to disable this behavior.

---

## Security Settings (Block 1 + Block 2)

The WebUI includes configurable security features:

## Environment Variables

### Block 1: Session Security

| Variable | Default | Description |
|----------|---------|-------------|
| `WEBUI_PUBLIC_MODE` | `false` | Enable for public/internet-facing deployments. Enforces stricter security checks. |
| `WEBUI_REQUIRE_HTTPS` | `false` | Require HTTPS. Should be `true` when public. |
| `WEBUI_SESSION_COOKIE_SECURE` | *(auto)* | Override cookie Secure flag. Empty = auto (true if public OR require_https). |

### Block 2: Login Protection

| Variable | Default | Constraints | Description |
|----------|---------|-------------|-------------|
| `WEBUI_LOGIN_DELAY_BASE_SECONDS` | `0.25` | non-negative | Base delay after first failed login (seconds) |
| `WEBUI_LOGIN_DELAY_MAX_SECONDS` | `2.0` | non-negative, must be >= base | Maximum delay cap (seconds) |

---

## Logging Configuration

The bot uses structured logging with configurable output format and filtering.

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | `info` | Minimum log level: `debug`, `info`, `warning`, `error` |
| `LOG_FORMAT` | `text` | Output format: `text` (human-readable) or `json` (structured) |
| `LOG_FILE` | *(none)* | Optional file path for log output (default: stderr only) |
| `LOG_DEBUG_SCOPES` | *(none)* | Comma-separated component names to force DEBUG level (e.g., `ai.router,plugin.runtime`) |
| `LOG_INCLUDE_PRIVATE_IDS` | `0` | Set to `1`/`true`/`yes` to include unmasked IDs in structured logs. **Privacy warning:** Enabling this may expose sensitive identifiers in log files. |

---

## Dreaming / Memory-Curation Runtime (KI-F4)

The Dreaming system performs **nightly** automatic curation of daily memory entries. It identifies relevant conversation patterns and promotes important information to long-term memory. The worker runs within a configurable night window in batches.

### Activation

By default, the Dreaming system is **disabled** (`DREAMING_ENABLED=0`). To activate it:

```ini
# Dreaming / Memory-Curation Runtime (disabled by default)
DREAMING_ENABLED=1
```

### Configuration Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DREAMING_ENABLED` | `0` | `1` to enable automatic memory curation |
| `DREAMING_WINDOW_START` | `02:00` | Night window start time (HH:MM, Europe/Berlin) |
| `DREAMING_WINDOW_END` | `05:00` | Night window end time (HH:MM, Europe/Berlin) |
| `DREAMING_TIMEZONE` | `Europe/Berlin` | Timezone for the night window |
| `DREAMING_MAX_SCOPES_PER_BATCH` | `3` | Maximum scopes per batch |
| `DREAMING_BATCH_PAUSE_SECONDS` | `300` | Pause between batches (seconds) |
| `DREAMING_JITTER_SECONDS` | `120` | Random delay per batch (seconds) |
| `DREAMING_MIN_DAILY_MEMORIES` | `1` | Minimum daily memory entries for scope eligibility |
| `DREAMING_LOOKBACK_DAYS` | `7` | How many days back for memory check |
| `DREAMING_TIMEOUT_SECONDS` | `300` | Timeout for a single curation run |
| `DREAMING_MAX_DAILY_CANDIDATES_PER_SCOPE` | `3` | Maximum candidates per scope per day |
| `DREAMING_MAX_PROMOTIONS_PER_SCOPE` | `2` | Maximum promotions per scope per day |
| `DREAMING_AUTO_APPROVE_MODE` | `0` | `1` bypasses human review — **use with extreme caution** |

### Safety Behavior

- **Default-Off:** The system is disabled by default — explicit activation required
- **Nightly Worker:** Only runs within the configured night window (e.g., 02:00–05:00)
- **No Max-Scopes-Per-Night:** The worker runs in batches until no eligible scopes remain or the window ends
- **Scope-Isolation:** Memory curation happens strictly per topic/private chat — no cross-scope access
- **Bounded Batches:** Maximum scopes per batch is configurable; pause and jitter between batches
- **Eligibility Filter:** Only scopes with sufficient daily memory material are processed
- **Timeout Protection:** Individual runs have fixed timeouts to prevent infinite loops
- **Auto-Approve:** Disabled by default; enabling bypasses human review and should only be used in trusted environments
- **No-Overlap Enforcement:** Only one curation run executes at a time; concurrent runs are blocked by an internal lock
- **Metadata-only Logs:** Audit events contain only metadata, no memory contents

### Recommended Configuration

**For local testing:**
```ini
DREAMING_ENABLED=0  # Disabled (default)
```

**For activated Dreaming with secure defaults:**
```ini
DREAMING_ENABLED=1
DREAMING_WINDOW_START=02:00
DREAMING_WINDOW_END=05:00
DREAMING_TIMEZONE=Europe/Berlin
DREAMING_MAX_SCOPES_PER_BATCH=3
DREAMING_BATCH_PAUSE_SECONDS=300
DREAMING_JITTER_SECONDS=120
DREAMING_MIN_DAILY_MEMORIES=1
DREAMING_LOOKBACK_DAYS=7
DREAMING_TIMEOUT_SECONDS=300
DREAMING_MAX_DAILY_CANDIDATES_PER_SCOPE=3
DREAMING_MAX_PROMOTIONS_PER_SCOPE=2
DREAMING_AUTO_APPROVE_MODE=0  # Human review required
```

> **Note:** The Nightly Worker periodically checks if the night window is active. Within the window, it processes scopes in batches (max. 3 per batch), pauses between batches, and adds random jitter. There is no global limit per night — the worker runs until all eligible scopes are processed or the window ends. Results are logged to audit events (no memory contents, only metadata).

**Privacy Note:**
- By default, `LOG_INCLUDE_PRIVATE_IDS` is disabled to protect user privacy
- When enabled, sensitive identifiers (user IDs, chat IDs) are logged without masking
- Log files should be protected with appropriate file permissions if IDs are included
- JSON format logs contain structured data; when `LOG_INCLUDE_PRIVATE_IDS=1` they include unmasked IDs
- **Recommended:** Keep `LOG_INCLUDE_PRIVATE_IDS=0` for production environments; enable only when needed

---

## Daily Memory Runtime (OpenClaw-like Light Memory)

The Daily Memory system provides a **lighter alternative to Dreaming** — it creates bounded daily summaries per scope (topic/private chat) without full memory curation/promotion. Like Dreaming, it runs during the night window but produces different output (digestible summaries vs. long-term memory promotions). It can run alongside Dreaming or standalone.

### Comparison: Daily Memory vs Dreaming

| Feature | Daily Memory | Dreaming (Long Memory) |
|---------|--------------|------------------------|
| Output | Bounded daily summaries | Curated long-term memory entries |
| Scope | Per scope (topic/private) | Per scope (topic/private) |
| Auto-approve | Always bounded; no approve needed | Optional human review gate |
| Runtime | Same night window | Same night window |
| Storage | DB summary table (bounded) | Long-term memory table |
| Privacy | Summary metadata only; logs metadata-only | Metadata-only logs |

### Activation

By default, Daily Memory is **disabled** (`MEMORY_DAILY_ENABLED=0`). To activate:

```ini
# Daily Memory Runtime (disabled by default)
MEMORY_DAILY_ENABLED=1
```

### Configuration Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MEMORY_DAILY_ENABLED` | `0` | `1` to enable daily memory summarization |
| `MEMORY_DAILY_INTERVAL_SECONDS` | `21600` | Interval between runs in seconds (6 hours) |
| `MEMORY_DAILY_MAX_INPUT_MESSAGES` | `200` | Max messages to process per scope per run |
| `MEMORY_DAILY_MAX_CHARS_PER_MESSAGE` | `500` | Truncate messages exceeding this length |
| `MEMORY_DAILY_MAX_SUMMARY_CHARS` | `6000` | Max length of generated daily summary |
| `MEMORY_DAILY_MIN_MESSAGES` | `1` | Minimum messages required to generate a summary |
| `MEMORY_DAILY_MAX_SCOPES_PER_RUN` | `10` | Max scopes to process in a single run |

### Safety Behavior

- **Default-Off:** Disabled by default — explicit activation required
- **Bounded Input:** Messages per scope are capped; long messages are truncated
- **Bounded Output:** Summary length is strictly capped
- **Minimum Threshold:** Scopes with fewer than `MIN_MESSAGES` messages are skipped
- **No Overlap:** Runs respect the same internal lock as Dreaming (at most one memory job at a time)
- **Metadata-only Logs:** Audit events contain only metadata (scope, message count, summary length); no raw message content in logs
- **Same Window:** Uses the Dreaming night window configuration (shares `DREAMING_WINDOW_*` settings)

### Recommended Configuration

**For local testing (disabled):**
```ini
MEMORY_DAILY_ENABLED=0  # Disabled (default)
```

**For activated Daily Memory with secure defaults:**
```ini
MEMORY_DAILY_ENABLED=1
MEMORY_DAILY_MAX_INPUT_MESSAGES=200
MEMORY_DAILY_MAX_CHARS_PER_MESSAGE=500
MEMORY_DAILY_MAX_SUMMARY_CHARS=6000
MEMORY_DAILY_MIN_MESSAGES=1
MEMORY_DAILY_MAX_SCOPES_PER_RUN=10
```

**To run both Daily Memory and Dreaming:**
```ini
# Daily Memory (light summaries)
MEMORY_DAILY_ENABLED=1
MEMORY_DAILY_MAX_INPUT_MESSAGES=200
MEMORY_DAILY_MAX_SUMMARY_CHARS=6000

# Dreaming (long-term curation) — optional
DREAMING_ENABLED=1
DREAMING_WINDOW_START=02:00
DREAMING_WINDOW_END=05:00
```

> **Privacy Note:** Daily Memory summaries may contain bounded digest text derived from conversations. Raw message content is never logged. Results are stored in the database with configurable retention; logs contain metadata only (message counts, timestamps, scope IDs).

---

## Websearch / SearXNG (optional)

The bot optionally supports web search functionality via a SearXNG instance. This enables AI-powered research with current web content.

### Prerequisites

- A running [SearXNG](https://github.com/searxng/searxng) instance (self-hosted or public)
- Network access from the bot to the SearXNG instance

### Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `AMO_WEBSEARCH_SEARXNG_BASE_URL` | *(empty)* | Base URL of your SearXNG instance (e.g., `http://localhost:8080` or `https://searx.example.com`) |
| `AMO_WEBSEARCH_SEARXNG_TIMEOUT_SECONDS` | `30` | Timeout for search requests in seconds |
| `AMO_WEBSEARCH_MAX_RESULTS` | `10` | Maximum number of search results |
| `AMO_WEBSEARCH_SEARXNG_LANGUAGE` | `auto` | Language preference for search results (e.g., `de-DE`, `en-US`, `auto`) |
| `AMO_WEBSEARCH_SEARXNG_CATEGORIES` | `general` | Comma-separated list of search categories (e.g., `general`, `news`, `images`) |

### Example Configuration

```ini
# Websearch / SearXNG
AMO_WEBSEARCH_SEARXNG_BASE_URL=http://localhost:8080
AMO_WEBSEARCH_SEARXNG_TIMEOUT_SECONDS=30
AMO_WEBSEARCH_MAX_RESULTS=10
AMO_WEBSEARCH_SEARXNG_LANGUAGE=en-US
AMO_WEBSEARCH_SEARXNG_CATEGORIES=general,news
```

> **Note:** If `AMO_WEBSEARCH_SEARXNG_BASE_URL` is not set, web search functionality is disabled.

---

## Security Headers

The WebUI sets the following HTTP security headers:

- **Content-Security-Policy (CSP):** Restricts resource loading
- **X-Frame-Options: DENY:** Prevents clickjacking
- **X-Content-Type-Options: nosniff:** Prevents MIME sniffing
- **Referrer-Policy: strict-origin-when-cross-origin:** Limits referrer leakage
- **Permissions-Policy:** Restricts browser features
- **HSTS:** Only in HTTPS/secure contexts

---

## Session Cookie Security

Session cookies use:
- **HttpOnly:** Prevents JavaScript access
- **SameSite=Lax:** CSRF protection
- **Secure:** Auto-enabled for public mode or HTTPS; override via `WEBUI_SESSION_COOKIE_SECURE`

---

## Login Protection (Block 2)

To prevent brute-force attacks, the WebUI implements **progressive delays** after failed login attempts (exponential backoff).

| Variable | Default | Description |
|----------|---------|-------------|
| `WEBUI_LOGIN_DELAY_BASE_SECONDS` | `0.25` | Base delay after first failure (seconds) |
| `WEBUI_LOGIN_DELAY_MAX_SECONDS` | `2.0` | Maximum delay cap (seconds). Must be >= base. |

**Behavior:**
- Delay increases progressively after each failed login attempt (exponential backoff)
- Delay is capped at `WEBUI_LOGIN_DELAY_MAX_SECONDS`
- Successful login resets the counter
- Delays are per IP address (`remote_addr`)
- `LoginAttemptTracker` is in-memory per process with `max_keys` limit and oldest eviction
- Multi-process/shared state remains a future enhancement

**Audit Events:**
- `webui_login_failure` — Logged on failed login attempt
- `webui_login_success` — Logged on successful login

Both events include `remote_addr` only. No passwords or other sensitive data is logged.

> **Reverse Proxy Note:** When running behind a reverse proxy, ensure `remote_addr` is set correctly and trustworthily by your infrastructure. The WebUI uses `remote_addr` directly without parsing `X-Forwarded-For`. Never expose Flask directly to the public internet.

---

## Local Development Defaults

For local testing, keep defaults:

```ini
WEBUI_PUBLIC_MODE=false
WEBUI_REQUIRE_HTTPS=false
# WEBUI_SESSION_COOKIE_SECURE=  # leave empty for auto
```

---

## Production/Internet Deployment

**⚠️ Warning:** Do not expose Flask directly to the internet. Use a reverse proxy (nginx, Caddy, Traefik) with HTTPS.

Recommended production configuration:

```ini
WEBUI_PUBLIC_MODE=true
WEBUI_REQUIRE_HTTPS=true
# WEBUI_SESSION_COOKIE_SECURE=  # auto-enabled
```

The WebUI will fail fast with a clear error if an unsafe configuration is detected in public mode.

---

## Running the Bot

### Bot Only (Polling)

**Linux / macOS:**

```bash
source venv/bin/activate
python main.py
```

**Windows (PowerShell):**

```powershell
.\venv\Scripts\Activate.ps1
python main.py
```

**Windows (Command Prompt):**

```cmd
venv\Scripts\activate.bat
python main.py
```

### WebUI Only

**Linux / macOS:**

```bash
source venv/bin/activate
python main.py --webui
```

**Windows (PowerShell):**

```powershell
.\venv\Scripts\Activate.ps1
python main.py --webui
```

**Windows (Command Prompt):**

```cmd
venv\Scripts\activate.bat
python main.py --webui
```

### Bot + WebUI Together (Default)

**Linux / macOS:**

```bash
source venv/bin/activate
python main.py
```

**Windows (PowerShell):**

```powershell
.\venv\Scripts\Activate.ps1
python main.py
```

**Windows (Command Prompt):**

```cmd
venv\Scripts\activate.bat
python main.py
```

### Stopping the Bot (for systemd/service integration)

To stop a running bot via the PID file (sends SIGTERM for graceful shutdown):

**Linux / macOS:**

```bash
source venv/bin/activate
python main.py --stop
```

**Windows (PowerShell):**

```powershell
.\venv\Scripts\Activate.ps1
python main.py --stop
```

**Alias:** `--stop-running` also works.

**Optional:** `--pid-file PATH` overrides the default PID file path.

**Default PID file:** `.state/amo_bot.pid` (configurable via `BOT_PID_FILE` in `.env`)

---

## Telegram Bot Setup

1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. Create a new bot: `/newbot`
3. Copy the token provided
4. Set your bot username in `.env`

### Bot-to-Bot Communication

If Telegram is configured so AMO can receive messages from other bots, AMO uses a separate safety gate:

- New bot senders are stored in `bot_peers` with status `pending`.
- AMO sends the configured owner a private message with inline buttons to allow or block that bot.
- `pending` and `blocked` bots are not answered.
- `allowed` bots may only trigger the explicitly approved diagnostic commands `/ping` and `/help` in V1; normal human user consent flows are not reused for bots.
- This approval is intentionally separate from the privacy consent flow for human users.

**Audit Events (metadata-only):**
- `bot_peer_detected` — When a new bot peer is first seen (payload: telegram_bot_id, username, first_name, chat_id, chat_type, message_thread_id)
- `bot_peer_status_set` — When owner changes the bot status (payload: telegram_bot_id, previous_status, new_status)

**Structured Runtime Logs:**
- `bot_peer.message.denied` — Bot message denied (e.g., database unavailable)
- `bot_peer.message.gate` — Gate check result (includes status, allowed flags)
- `bot_peer.message.skipped` — Allowed bot sent non-command or disallowed command

> **Privacy:** All bot-peer audit events and logs contain metadata only (IDs, status, timestamps). No message content, prompts, or secrets are logged.

---

## Preflight Tests

Before connecting to real Telegram APIs:

**Linux / macOS:**

```bash
source venv/bin/activate
pytest -q
python -m amo_bot.smoke
```

**Windows (PowerShell):**

```powershell
.\venv\Scripts\Activate.ps1
pytest -q
python -m amo_bot.smoke
```

**Windows (Command Prompt):**

```cmd
venv\Scripts\activate.bat
pytest -q
python -m amo_bot.smoke
```

Expected results:
- pytest: All tests pass
- smoke: Bootstrap and basic commands OK

---

## Troubleshooting

### Bot does not respond
- Check terminal: Is `python main.py` running?
- Verify `.env`: Is `BOT_TOKEN` correct?
- Check Telegram: Did you click "Start" in the bot chat?

### Virtual environment activation fails (Windows)

**PowerShell execution policy error:**
```
.\venv\Scripts\Activate.ps1 : cannot be loaded because running scripts is disabled
```

**Solution:** Run PowerShell as Administrator and execute:
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

Then retry activation.

### Python not found
- Ensure Python 3.12+ is installed and on PATH
- Windows: Use `py` or full path, e.g., `C:\Python312\python.exe`
- Linux/macOS: Use `python3` if `python` points to Python 2

### Permission denied when creating `data/` directory

**Linux / macOS:**
```bash
mkdir -p data
chmod 755 data
```

**Windows:**
- Create folder manually in Explorer
- Or run Command Prompt/PowerShell as Administrator

### Database/SQLite errors

**Linux / macOS:**
- Does the `data/` directory exist?
- Are write permissions available?
- For testing only: `rm data/amo_bot.db` and restart

**Windows:**
- Does the `data\` directory exist?
- Check folder permissions (right-click → Properties → Security)
- For testing only: `del data\amo_bot.db` and restart

---

## Database: MariaDB Support (Optional)

In addition to SQLite, AMO also supports MariaDB/MySQL via SQLAlchemy.

### Configuration

```ini
DATABASE_URL=mysql+pymysql://amo_bot:<password>@<mariadb-host>:3306/amo_bot?charset=utf8mb4
```

### Prerequisites Before Cutover

1. **Install dependencies** (in venv):
   ```bash
   pip install pymysql
   ```

2. **Create database and user** (recommended rights: database-scoped only, not global):
   ```sql
   CREATE DATABASE amo_bot CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
   CREATE USER 'amo_bot'@'%' IDENTIFIED BY '<strong-password>';
   GRANT ALL PRIVILEGES ON amo_bot.* TO 'amo_bot'@'%';
   FLUSH PRIVILEGES;
   ```

3. **Migrate/verify data** before cutover (do not delete SQLite DB except for testing):
   ```bash
   python -m amo_bot.db.migrate \
     --source-url sqlite:///./data/amo_bot.db \
     --target-url 'mysql+pymysql://amo_bot:<password>@<mariadb-host>:3306/amo_bot?charset=utf8mb4' \
     --dry-run
   ```
   The migration tool prints table names, row counts, and status only. After a verified backup and dry-run, remove `--dry-run` to copy into an empty target database. By default it refuses same source/target URLs and refuses non-empty targets unless `--allow-nonempty-target` is explicitly supplied.

### Notes

- SQLite remains the default and is recommended for local instances.
- MariaDB is prepared for future production deployments.
- Do not delete the SQLite file (`data/amo_bot.db`) before successful migration.

### Retrievable Memory Backfill

After migration, existing Daily Memories can be moved into retrievable memory:

```bash
# Preview (metadata-only: table names, counts, scopes)
python -m amo_bot.db.retrievable_memory_backfill --dry-run

# After review: perform the transfer
python -m amo_bot.db.retrievable_memory_backfill --apply
```

**Note:** The backfill does not read `topic_recent_messages`. Output contains metadata only (counts, scopes, types) — never memory text.

### Ollama not reachable
- Is Ollama running? `curl http://127.0.0.1:11434/api/tags`
- Is the URL in `.env` correct?
- Firewall blocking port 11434?

### WebUI login fails
- Is `WEBUI_PASSWORD` set in `.env`?
- Is the value not empty or "change_me"?
- Are you accessing `http://127.0.0.1:8080`?

---

## WebUI: KI Topic-Agent Status (Read-Only)

The WebUI Dashboard displays the current KI Topic-Agent configuration status:

- **Scope:** Shows whether the configuration applies to a topic (`topic`) or private chat (`private`)
- **Chat ID:** Telegram chat identifier (for topic scopes)
- **Topic ID:** Topic/thread identifier within the chat (for topic scopes)
- **User ID:** User identifier (for private scopes)
- **AI Status:** Shows `active` or `inactive` — whether AI auto-reply is enabled for this scope
- **Response Mode:** Current response mode (e.g., `command` for explicit commands only, or other configured modes)

This is a **read-only** view. Editing AI status and response mode via WebUI requires future implementation.

---

## WebUI: Topic Soul Editor (Owner-Only)

The WebUI allows the owner to edit topic-specific **Soul text** on the group detail page:

- **Location:** Groups → Details link → Group detail page → Topic section
- **Editable fields:**
  - Display Name (optional)
  - Notes (optional)
  - Topic Soul text (optional, max 4000 characters)
  - Enabled checkbox
- **Security:**
  - Only the configured `WEBUI_OWNER_TELEGRAM_ID` can edit
  - Requires login + CSRF token
  - Input is HTML-escaped and length-bounded
- **Behavior:**
  - Changes take effect immediately (no restart required)
  - Empty Topic Soul removes the custom soul text

**Note:** Non-owners can view the Topic Soul but cannot edit it. The save button is disabled when `WEBUI_OWNER_TELEGRAM_ID` is not configured.

---

## WebUI: KI Memory Controls (KI-F3 + CP-G2)

The WebUI Dashboard includes a **KI Memory** section for inspecting and managing AI memory entries with privacy-hardened controls.

**Daily Memory (Redacted):**
- Shows only memory dates (e.g., "2026-05-14, 2026-05-13")
- Raw summary text is not displayed (privacy-conscious default)
- No raw memory content exposed in MVP

**Long Memory:**
- Lists long-term memory entries with fact text, status, and timestamps
- Shows "active" or "inactive" status for each entry
- Owner can deactivate entries via CSRF-protected button
- Deletion and deactivation are auditable (no memory text in audit events)

**Memory Management Policy (CP-G2):**
- **Default-deny:** Memory operations require explicit policy approval (CP-G1)
- **Scope isolation:** Memory is strictly scoped to topics/private chats; no cross-scope access
- **Bounded operations:** put/get/search/delete/deactivate are size/time bounded
- **TTL/Retention:** Automatic pruning via maintenance hooks
- **Redacted outputs:** Only metadata placeholders shown; raw memory text never exposed
- **Audit events:** Include scope and entry ID only — never memory content

**Requirements:**
- Authenticated WebUI session to view memory
- `WEBUI_OWNER_TELEGRAM_ID` configured to deactivate entries

**Security:**
- Deactivation requires CSRF token
- Without owner configuration, deactivation returns 403 Forbidden
- Audit events: Memory operation audit decisions record reason codes for put/get/search/delete/deactivate operations (e.g., `memory_put_ok`, `memory_get_ok`, etc.). These events are metadata-only and contain no memory content.

---

## Next Steps

- See [BETATEST_EN.md](BETATEST_EN.md) for detailed testing instructions
- See [RELEASE_NOTES_2026.06.03_EN.md](RELEASE_NOTES_2026.06.03_EN.md) for the current changelog

## WebUI Security — Access Window (Block 3)

The WebUI access can be controlled via Telegram commands. This allows the owner to open or close access to the WebUI from anywhere.

### Telegram Commands

| Command | Description | Requirements |
|---------|-------------|--------------|
| `/webui status` | Shows whether the WebUI access window is OPEN or CLOSED, and the remaining time if open | Private chat, Owner only |
| `/webui on` | Opens the WebUI access window for 60 minutes (extends if already open) | Private chat, Owner only |
| `/webui off` | Closes the WebUI access window immediately | Private chat, Owner only |
| `/restart` | Sends an acknowledgement and exits the bot process for a controlled supervisor restart | Private chat, Owner only |

**Important:** These commands only work in **private chats** (not in groups) and only for the **owner**.

For `/restart`, AMO sends an acknowledgement before the process exits. It also persists the current polling offset before restarting so the same Telegram update ID cannot trigger a restart loop after startup.

### Access Denied Reasons

If access is denied, an audit event is logged with one of these reasons:
- `not_private` — Command was used in a group or channel
- `not_owner` — User is not the configured owner

### Audit Events

The following audit events are generated:

| Event | Description |
|-------|-------------|
| `webui_access_enabled` | WebUI access window opened via `/webui on` |
| `webui_access_disabled` | WebUI access window closed via `/webui off` |
| `webui_access_status` | Status checked via `/webui status` |
| `webui_access_denied` | Access denied (wrong chat type or unauthorized user) |

### Status Information

When running `/webui status`, you receive:
- **OPEN** with remaining minutes if the access window is active
- **CLOSED** if no access window is currently open

The access window is stored persistently in the database, so it survives bot restarts.

---

## WebUI Security — HTTP Request Gate (Block 3C)

When `WEBUI_PUBLIC_MODE=true`, the WebUI uses an **HTTP Request Gate** that blocks access to protected pages when the access window is closed.

### How It Works

| Scenario | Behavior |
|----------|----------|
| `WEBUI_PUBLIC_MODE=false` | Gate is inactive; local/LAN usage unchanged |
| `WEBUI_PUBLIC_MODE=true` + Access Window **closed** | `/login` and protected pages return **403 Forbidden** |
| `WEBUI_PUBLIC_MODE=true` + Access Window **open** | Normal password login works; access granted |

### Whitelisted Paths

The following paths are always accessible (gate does not block):
- `/health` — Health check endpoint
- `/static/*` — Static assets (CSS, JS, images)
- `/logout` — Logout endpoint

### 403 Responses

When access is blocked, the gate returns:

**HTML/Plain text requests:**
```
403 Forbidden
```

**JSON/API requests:**
```json
{"error":"forbidden","status":403}
```

### Configuration

```ini
# Enable public mode to activate the gate
WEBUI_PUBLIC_MODE=true

# Access window controlled via Telegram commands:
# /webui on  - opens the window for 60 minutes
# /webui off - closes the window immediately
# /webui status - shows current state
```

> **Note:** When the access window is open, normal password authentication is still required. The gate only controls *whether* the login page is reachable, not the login itself.

---

## WebUI: Group Management

After logging in, the "Groups" page shows a compact status overview of all groups with topic count and details links.

### Overview /groups

- List of all groups/supergroups
- Topic count per group
- **"Details" link** per group for editing

### Group Detail Page /groups/<chat_id>

Via the Details link you access a group's detail page. All editing functions are located there:

- **Group roles:** View users and their current roles; set roles: `admin`, `vip`, `normal`, `ignore`
  - `owner` cannot be assigned as a group role (only via `.env`)
  - `normal` removes the group-scoped entry → fallback to `normal`
  - Roles are group-scoped, not global
- **Topic metadata:** Display Name, Notes, Enabled status
- **Topic soul:** Topic-specific AI behavior instructions (owner-only)
- **AI controls:** AI status and response mode per topic

**Note on user list:** The WebUI only shows users the bot has seen in that group. Existing role assignments remain visible and are marked `[assigned/not seen]` if the user has not yet been active in the group.

---

## WebUI: Users – Private Chat Role Thresholds

The "Users" page in the WebUI configures role thresholds for **private bot chats** (direct messages):

- Applies only to private chats, not groups or topics
- `owner` remains the only global special role
- Group/topic permissions continue to be managed on their respective context pages

**Configurable thresholds:**
- **AI/KI minimum role** for private chats (default: `vip`)
- **General/built-in commands** – minimum role (default: `normal`)
- **Plugin commands** – minimum role (default: `normal`)

**Allowed threshold roles:** `owner` > `admin` > `vip` > `normal`

- `ignore` is not selectable as a threshold and remains denied
- Hierarchy: `owner` > `admin` > `vip` > `normal` > `ignore`

---

## WebUI: Image Analysis Role Quotas (IMG-B7 + IMG-B8)

The "Users" page in the WebUI includes an **"Image analysis role quotas"** section for configuring role-based limits for image analyses. IMG-B8 implements runtime enforcement with rolling 24h window.

### Quota Modes

Each role (`owner`, `admin`, `vip`, `normal`, `ignore`) can have one of the following modes:

| Mode | Description |
|------|-------------|
| `disabled` | Image analysis disabled for this role |
| `unlimited` | No limit (only allowed for `owner`) |
| `limited` | Limit with positive integer value (rolling 24h window) |

### Rolling 24h Semantics (IMG-B8)

- The limit applies to a **rolling 24h window** based on audit timestamps
- An event from 23 hours 59 minutes ago counts toward the current limit
- An event from 24 hours 1 minute ago no longer counts
- No hard daily reset at midnight UTC

### Conservative Defaults

- **Owner:** `unlimited` – full access
- **Admin:** `disabled` – must be explicitly enabled
- **VIP:** `disabled` – must be explicitly enabled
- **Normal:** `disabled` – must be explicitly enabled
- **Ignore:** `disabled` – always blocked, regardless of quota configuration

### Deny-Before-Provider (IMG-B8)

- **Check order:** Image validity → topic gate → quota deny → provider invocation
- On quota exceeded, an audit entry is written, provider is **not** called
- Image content is not stored in audit (only metadata: user_id, chat_id, outcome, timestamp)

### Configuration

1. Open http://127.0.0.1:8080 and log in
2. Navigate to the "Users" page
3. Scroll to the "Image analysis role quotas" section
4. For each role, select the mode:
   - `disabled` – Set dropdown to "Disabled"
   - `unlimited` – Set dropdown to "Unlimited" (owner only)
   - `limited` – Set dropdown to "Limited" and enter a positive limit
5. Click "Save quotas"

**Notes:**
- When mode is `limited`, a positive value (≥ 1) must be entered
- `ignore` cannot be set to `unlimited` (always remains `disabled`)
- These settings are the **source of truth** for runtime enforcement (IMG-B8)
- Changes take effect immediately for new requests (no restart required)

---

## WebUI: Plugin AI Tool Toggle (Read-Only)

The Plugins page displays an **AI Tool** toggle indicator for each plugin:

- **Read-only indicator:** Shows whether the plugin is currently allowed as an AI tool
- **Default-off:** Disabled tools remain denied at runtime
- **Policy-gated:** Actual enablement is governed by KI-E policy gates, not via WebUI toggle

This is a transparency/security feature to help owners understand which plugins can be invoked by the AI system. To change AI tool permissions, configure the appropriate policy gates.

---

## Image Analysis Coreplugin (IMG-B4..IMG-B8)

The `image_analyze` coreplugin provides a secure image analysis interface for AI and user plugins.

### Security Model

**Default-off with explicit topic enablement:**
- Image analysis is disabled by default
- Must be explicitly enabled per topic
- In enabled topics, the bot automatically analyzes Telegram photos and image documents
- Outside enabled topics, no automatic image analysis is performed

**Usage Policy:**
- `min_role` (default: admin) — Minimum role for image analysis
- Supported roles: `owner` > `admin` > `vip` > `normal` > `ignore`

**Role-Based Limits (IMG-B8):**
| Role | Limit | Description |
|------|-------|-------------|
| `owner` | unlimited | No limit |
| `admin` | unlimited | No limit (if enabled) |
| `vip` | configurable | Limit with rolling 24h window |
| `normal` | configurable | Limit with rolling 24h window |
| `ignore` | 0 | Always blocked, regardless of quota configuration |

**Rolling 24h Semantics (IMG-B8):**
- The limit applies to a **rolling 24h window** based on audit timestamps
- An event from 23 hours 59 minutes ago counts toward the current limit
- An event from 24 hours 1 minute ago no longer counts
- No hard daily reset at midnight UTC

**Topic Gate (IMG-B2b/IMG-B8):**
- Image analysis can be enabled per-topic
- Key: `(chat_id, message_thread_id)`
- Default: disabled (no image analysis without explicit activation)
- Database-managed (no `.env` configuration)

**Vision Provider Configuration (Ollama):**
When using Ollama for image analysis, the vision model must be explicitly allowlisted:

| Variable | Default | Description |
|----------|---------|-------------|
| `IMAGE_ANALYSIS_OLLAMA_VISION_MODELS` | `llava,llama3.2-vision,qwen2.5vl` | Comma-separated list of vision-capable models allowed for image analysis |

- Default allowlist includes common Ollama vision models: `llava`, `llama3.2-vision`, `qwen2.5vl`
- Non-standard vision model names (e.g., `kimi-k2.5:cloud`) can be explicitly allowed by adding to the list
- Generic refusal/policy/unusable responses from the provider are treated as failures and mapped to a truthful "unavailable" message

**Example configuration:**
```ini
# Standard vision models (default)
IMAGE_ANALYSIS_OLLAMA_VISION_MODELS=llava,llama3.2-vision,qwen2.5vl

# With custom vision model
IMAGE_ANALYSIS_OLLAMA_VISION_MODELS=llava,llama3.2-vision,qwen2.5vl,kimi-k2.5
```

**Input Validation:**
- `image_ref` required and non-empty
- `prompt` optional, max 512 characters
- `locale` optional, max 16 characters, letters and `-`/`_` only

**Deterministic Reason Codes:**
- `not_enabled` — Image analysis is disabled
- `role_forbidden` — User role insufficient
- `role_disabled` — Role is `ignore` or set to `disabled`
- `quota_exceeded` — Rolling 24h limit reached (NORMAL/VIP only)
- `topic_disabled` — Topic gate is disabled for this context
- `network_not_allowed` — Network access not allowed
- `provider_not_allowed` — Vision provider not configured/allowed
- `not_configured` — Image analysis not configured (stub behavior)
- `invalid_image_ref` — Invalid image reference
- `invalid_prompt` — Prompt too long or invalid
- `invalid_locale` — Invalid locale format

**User-Facing Deny Reasons (IMG-B3):**
The following errors are explicitly communicated to users:
- `missing_image` — No image found in context (e.g., `/analyze_image` without image attachment)
- `invalid_type` — Attachment is not a supported image format (JPEG, PNG, WebP, GIF only)
- `oversize` — Image exceeds maximum file size (configurable, default: 10 MB)
- `topic_disabled` — Image analysis is disabled for this topic
- `role_disabled` — Your role has no permission for image analysis
- `quota_exceeded` — Image analysis limit reached (rolling 24h window)
- `provider_timeout` — Image analysis provider unreachable (timeout)
- `provider_error` — Provider error (technical details not shown for security reasons)
- `provider_empty` — Provider returned no response (technical details not shown for security reasons)

>Note: Provider errors (`provider_error`, `provider_empty`) are shown as generic/redacted to avoid leaking internal details. Audit logs contain outcome codes for diagnostic purposes.

**Deny-Before-Provider (IMG-B8):**
- **Check order:** Image validity → topic gate → quota deny → provider invocation
- On quota exceeded, an audit entry is written, provider is **not** called
- Blocked requests incur no provider costs
- Fail-fast on invalid inputs

**Audit Persistence (IMG-B8):**
- All requests are logged with:
  - `user_id`, `chat_id`, `message_thread_id`
  - `outcome` (e.g., `allowed`, `quota_exceeded`, `topic_disabled`)
  - Timestamp (UTC)
- Audit events contain no image content (metadata only)
- Persisted in `image_analyze_audit_events` table
- Quota deny writes audit without provider invocation
- **Temporary image handling:** Downloaded images are automatically cleaned up after analysis (no persistent storage)

**Scope Isolation:**
- Images are processed scope-specific
- No cross-scope image sharing
- Audit events contain metadata only, no image content

### Telegram Integration

**Image Attachment Detection:**
- `photo` and `document` with image MIME types are recognized as attachments
- Telegram photos and image documents are considered for automatic analysis in enabled topics
- `application/octet-stream` is accepted only on the trusted Telegram photo path when the file path has an allowed image suffix
- Metadata only: `file_id`, `file_unique_id`, dimensions, file size
- Downloads are limited to allowed image types and stored in a short-lived temp directory with TTL cleanup

**Triggers:**
- Automatic: Telegram photo or image document in a topic with image analysis enabled
- `/analyze_image` — Analyzes an image in the current context
- Reply-to-image — Reply to an image with bot mention

**Attachment Context:**
- Plugin commands receive secure attachment context
- `media_ref` contains only: `reason_code`, `mime_type`, `bytes_stored`
- No raw image data or file paths in plugin context

**Error Handling:**
- `missing_image` — No image found in context
- `invalid_type` — Attachment is not a supported image format
- `oversize` — Image exceeds maximum file size
- `invalid_image` — Image validation failed

### MediaStore Limits

**Download Policy:**
- MIME type whitelist: `image/jpeg`, `image/png`, `image/webp`, `image/gif`
- Maximum file size: Configurable (default: 10 MB)
- Timeout: Configurable (default: 30 seconds)
- Temporary storage with TTL cleanup

**Security Boundaries:**
- No raw image data in logs or audit events
- No persistent storage without explicit configuration
- Automatic cleanup after processing

### WebUI Status (Read-Only)

The WebUI displays the image analysis status:
- **Enabled:** `true`/`false` — Is image analysis enabled?
- **Min Role:** Current minimum role requirement

**Note:** Configuration is done via settings/policy, not directly through WebUI toggles.

---

## WebUI: Per-Topic Image Analysis Setting (IMG-B5)

The WebUI allows configuring image analysis per topic via the group detail page.

### Image Analysis Mode

Each topic can be configured with an `image_analysis_mode`:

| Mode | Behavior |
|------|----------|
| `inherit` (default) | Inherits from global default — effectively disabled until the runtime resolver (IMG-B6) becomes active |
| `enabled` | Image analysis explicitly enabled for this topic |
| `disabled` | Image analysis explicitly disabled for this topic |

### WebUI Configuration

1. **Groups Overview:** `/groups` displays the effective image analysis status per group.

2. **Group Details:** `/groups/<chat_id>` shows per topic:
   - Current `image_analysis_mode`
   - Selection options: inherit / enabled / disabled
   - Save button (only with configured `WEBUI_OWNER_TELEGRAM_ID`)

3. **Safe Default:** Topics with `inherit` or missing configuration remain effectively disabled until explicitly enabled.

### Note

The setting is stored in the database (`topic_agent_configs.image_analysis_mode`). Changes take effect immediately (no restart required). Actual enforcement of image analysis policies is handled by the runtime resolver (IMG-B6).

---

## Image Sending (IMG-B4)

The bot supports sending images via Telegram's `send_photo` and `send_document` APIs, with full policy/role/topic gate integration.

### Security Model

**Capability-gated:**
- Sending images requires the `send_message` capability
- Specific `send_image` capability can be configured for granular control
- All policy checks happen before sending (deny-before-send)

**Topic-Safe:**
- Images respect the current `message_thread_id` context
- Replies in topics remain in the correct thread
- Cross-topic image sending is blocked

**File Type Handling:**
- Images (JPEG, PNG, WebP, GIF) → `send_photo`
- Documents/generic files → `send_document`
- MIME type validation before sending

### Reason Codes

- `role_forbidden` — User role insufficient to send images
- `topic_disabled` — Image sending disabled for this topic
- `rate_limited` — Too many image sends in short time
- `invalid_file` — File type or size not allowed
- `send_failed` — Telegram API error (generic user message)

### Plugin Integration

Plugins can send images via the `send_image` capability:

```json
{
  "capability": "send_image",
  "params": {
    "file_path": "/path/to/image.jpg",
    "caption": "Optional caption",
    "reply_to_message_id": 123
  }
}
```

**Audit:** All image sends are logged with metadata only (file_id, mime_type, size).

---

## SQL Capability Templates (CP-H1)

The SQL coreplugin provides a **template-only, read-only** SQL execution interface for AI and user plugins. Raw SQL is never executed directly.

### Security Model

**Default-deny:**
- All SQL execution is blocked unless explicitly allowed by capability policy and template allowlist
- Unknown templates are rejected

**Template-only execution:**
- Only pre-defined templates with bound parameters can execute
- No raw SQL injection possible
- Template SQL is validated for `SELECT` statements only

**Read-only views:**
- Templates can only query allowlisted views (e.g., `v_topic_activity_summary`, `v_plugin_health_overview`)
- Forbidden tables (sensitive data like `users`, `user_secrets`, `topic_daily_memories`, `plugin_settings`) are blocked

**Bounded results:**
- Row limits enforced (default 100, global max 500)
- Column limits enforced (max 12 columns, capped at 24)
- Results truncated safely when limits exceeded

**Column masking:**
- Sensitive columns (`chat_id`, `user_id`, `topic_id`) are masked by default
- Output shows `***MASKED***` instead of actual values

**Actor/scope validation:**
- Requires valid `actor_type` (`ki` or `user_plugin`)
- Requires valid `scope_type` (`chat` or `topic`)
- Elevated context flags (`admin`, `tunnel`, `elevated`) are explicitly denied
- KI does not inherit admin privileges
- UserPlugins cannot tunnel through KI privileges

**Injection protection:**
- Parameter validation rejects SQL injection attempts (`--`, `;`, `/*`, `*/`, `UNION`, `DROP`)
- Parameter length capped (120 characters)
- Only scalar values (string, int, float, bool) accepted

### Reason Codes

Audit events include reason codes for transparency:
- `unknown_template` — Template ID not in allowlist
- `forbidden_table` — SQL references sensitive tables
- `invalid_sql_template` — SQL is not a safe SELECT over allowlisted views
- `invalid_params` — Parameters outside allowed set or malformed
- `injection_detected` — Suspicious patterns in parameters
- `missing_or_invalid_actor` — Actor/scope not provided or invalid
- `elevated_context_denied` — Attempt to use elevated privileges
- `db_error` — Database execution error (safe failure)
- `ok` — Execution successful

### No Autonomous Operations

The SQL capability:
- **Cannot** modify data (INSERT/UPDATE/DELETE blocked)
- **Cannot** access raw memory tables
- **Cannot** escalate privileges
- **Cannot** execute arbitrary SQL
- **Cannot** bypass audit logging

---

## Troubleshooting

### Bot does not respond
- Check terminal: Is `python main.py` running?
- Verify `.env`: Is `BOT_TOKEN` correct?
- Check Telegram: Did you click "Start" in the bot chat?

### Virtual environment activation fails (Windows)

**PowerShell execution policy error:**
```
.\venv\Scripts\Activate.ps1 : cannot be loaded because running scripts is disabled
```

**Solution:** Run PowerShell as Administrator and execute:
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

Then retry activation.

### Python not found
- Ensure Python 3.12+ is installed and on PATH
- Windows: Use `py` or full path, e.g., `C:\Python312\python.exe`
- Linux/macOS: Use `python3` if `python` points to Python 2

### Permission denied when creating `data/` directory

**Linux / macOS:**
```bash
mkdir -p data
chmod 755 data
```

**Windows:**
- Create folder manually in Explorer
- Or run Command Prompt/PowerShell as Administrator

### Database/SQLite errors

**Linux / macOS:**
- Does the `data/` directory exist?
- Are write permissions available?
- For testing only: `rm data/amo_bot.db` and restart

**Windows:**
- Does the `data\` directory exist?
- Check folder permissions (right-click → Properties → Security)
- For testing only: `del data\amo_bot.db` and restart
