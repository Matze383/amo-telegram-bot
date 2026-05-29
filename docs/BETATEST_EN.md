# AMO Telegram Bot — Beta Test Guide

[Deutsche Version](BETATEST_DE.md)

### Beta Test Goal

This guide helps you test the MVP status of the bot:

- Commands work in private chats and groups
- Role management via Telegram and WebUI
- Plugin activation through WebUI
- Ollama integration for `/ask`

---

### Prerequisites

- Python 3.12 or higher
- Linux/macOS development environment
- A Telegram bot token (from @BotFather)
- Optional: AI provider for `/ask`:
  - Local Ollama instance, **OR**
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
  - AWS credentials/profile for Amazon Bedrock

---

### .env Configuration

Copy the example file:

```bash
cp .env.example .env
```

Edit `.env` with your values:

```
# Telegram (Required)
BOT_TOKEN=your_bot_token_here
BOT_USERNAME=your_bot_username
TELEGRAM_API_BASE=https://api.telegram.org

# AI Provider Configuration
AI_PROVIDER=ollama  # ollama (default), openai, anthropic, google, openrouter, groq, mistral, xai or deepseek, amazon-bedrock

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

# Optional: Ollama (for /ask command)
OLLAMA_URL=http://127.0.0.1:11434
OLLAMA_MODEL=llama3.1
OLLAMA_TIMEOUT_SECONDS=20
OLLAMA_MAX_PROMPT_CHARS=4000
OLLAMA_MAX_PREDICT_TOKENS=512
OLLAMA_MAX_RESPONSE_CHARS=1500

# Database
DATABASE_URL=sqlite:///./data/amo_bot.db

# Plugins
AMO_PLUGIN_DIR=./plugins

# WebUI (Required for beta test)
WEBUI_HOST=127.0.0.1
WEBUI_PORT=8080
WEBUI_PASSWORD=your_secure_password
WEBUI_OWNER_TELEGRAM_ID=your_telegram_user_id
WEBUI_SESSION_TTL_SECONDS=3600

# Security settings (Block 1)
# WEBUI_PUBLIC_MODE=false
# WEBUI_REQUIRE_HTTPS=false
# WEBUI_SESSION_COOKIE_SECURE=

# Security settings (Block 2 - Login Protection)
# WEBUI_LOGIN_DELAY_BASE_SECONDS=0.25
# WEBUI_LOGIN_DELAY_MAX_SECONDS=2.0

# Polling Configuration
POLL_TIMEOUT_SECONDS=30
POLL_LIMIT=100
POLL_RETRY_MAX_SECONDS=30
OFFSET_STATE_FILE=.state/offset.json
```

---

### Security Features (Block 1 + Block 2)

The WebUI now includes hardened security:

**Security Headers (always active):**
- Content-Security-Policy (CSP)
- X-Frame-Options: DENY
- X-Content-Type-Options: nosniff
- Referrer-Policy
- Permissions-Policy
- HSTS (in HTTPS contexts)

**Session Cookie Security:**
- HttpOnly flag
- SameSite=Lax
- Secure flag (auto-enabled for public/HTTPS)

**Login Protection (Block 2):**
- Progressive delay after failed login attempts (exponential backoff / brute-force protection)
- Configurable via `WEBUI_LOGIN_DELAY_BASE_SECONDS` (default: 0.25s) and `WEBUI_LOGIN_DELAY_MAX_SECONDS` (default: 2.0s)
- Delay is capped at the maximum value
- Successful login resets the delay counter
- Per-IP tracking using `remote_addr` (conservative keying)

**Configuration:**
- `WEBUI_PUBLIC_MODE=false` — Local development default
- `WEBUI_REQUIRE_HTTPS=false` — Local development default
- `WEBUI_LOGIN_DELAY_BASE_SECONDS=0.25` — Initial delay after first failure
- `WEBUI_LOGIN_DELAY_MAX_SECONDS=2.0` — Maximum delay cap

**⚠️ Production Warning:** Flask should not be exposed directly to the internet. Use a reverse proxy with HTTPS.

---

### Setup Steps

1. **Create virtual environment:**

```bash
cd AMO-telegram-bot
python3.12 -m venv venv
source venv/bin/activate
```

2. **Install dependencies:**

```bash
pip install -r requirements.txt
```

3. **Create directories:**

```bash
mkdir -p data
mkdir -p .state
mkdir -p plugins
```

---

### Local Preflight

Before the first Telegram test:

```bash
source venv/bin/activate

# Run unit tests
pytest -q

# Smoke test (local checks without real API calls)
python -m amo_bot.smoke
```

**Expected Results:**
- pytest: All tests passed
- smoke: Bootstrap and basic commands OK

---

### Start Bot

```bash
source venv/bin/activate
python main.py
```

**Success indicators:**
- Bot reports "Bot started"
- Polling begins without errors
- Offset loaded from `.state/offset.json` or newly created

---

### Start WebUI

In a **second terminal**:

```bash
source venv/bin/activate
python main.py --webui
```

**Important:** WebUI runs locally only (`127.0.0.1`). Do not expose to the internet.

**URL:** http://127.0.0.1:8080

---

### Private Chat Tests

Start a private chat with your bot:

**Test 1: /ping**
- Send: `/ping`
- Expected: `Pong!`

**Test 2: /help**
- Send: `/help`
- Expected: List of available commands (depends on your role)

**Test 3: /consent**
- Send: `/consent`
- Expected: Shows your current consent status and available commands
  - **Private chat**: Full status and details shown
  - **Groups**: Privacy notice only (no status details shown in groups for data protection)

**Test 4: /accept**
- Send: `/accept`
- Expected: Consent accepted confirmation
- Note: If you previously declined, you can use `/accept` again to re-consent

**Test 5: /decline**
- Send: `/decline`
- Expected: Consent declined confirmation
- Note: You can use `/accept` later if you change your mind

**Test 6: /role**
- Send: `/role`
- Expected: Your current role (e.g., "owner")

---

### Group Test

1. Add the bot to a test group
2. Ensure the bot has admin rights (to read all messages)
3. Test commands:
   - `/ping`
   - `/help`
   - `/role`

**Important:** Commands in groups often need the bot username: `/ping@your_bot_username`

---

### Role Test with /setrole

**Prerequisite:** You are Owner or Admin

**Role Scoping Rules:**
- In **private chats (DM)**: Global role applies everywhere
- In **groups**: Global `owner` or `ignore` overrides everything; otherwise group-specific role applies; otherwise `normal`
- Group admins can only set `vip`, `normal`, `ignore` within their own group, **not** `admin` or `owner`
- An admin in Group A is **not** automatically admin in Group B

**As Owner:**
- `/setrole <user_id> admin` – User becomes Admin (global in DM, group-scoped in groups)
- `/setrole <user_id> vip` – User becomes VIP (global in DM, group-scoped in groups)
- `/setrole <user_id> normal` – User becomes Normal (global in DM, group-scoped in groups)
- `/setrole <user_id> ignore` – User is ignored (global, overrides everything)

**As Admin:**
- `/setrole <user_id> vip` – Allowed
- `/setrole <user_id> normal` – Allowed
- `/setrole <user_id> ignore` – Allowed
- `/setrole <user_id> admin` – **Not allowed**
- `/setrole <user_id> owner` – **Not allowed**

**Test Workflow:**
1. Create a second Telegram account or ask a friend
2. Get the Telegram user ID of the test account
3. In a **private chat**: Set role to `normal` → `/role` shows "normal (global)"
4. In **Group A**: Set role to `vip` → `/role` shows "vip (this group)"
5. In **Group B**: `/role` shows global role (or "normal") unless explicitly set
6. Test if account can use `/ask` (with `vip`: yes, with `normal`: no)

**Group-Scoped Role Tests:**
- [ ] `/role` in DM shows global role with source
- [ ] `/role` in Group A shows group-specific or global role
- [ ] `/setrole` in DM sets global role
- [ ] `/setrole` in Group A sets role only for Group A
- [ ] User with `vip` in Group A has `normal` permissions in Group B (if no global role)
- [ ] Group admin cannot promote to `admin`/`owner` (only `vip`/`normal`/`ignore`)

**Audit Events (optional check):**
- [ ] Group role changes via `/setrole` are auditable (check logs or database if applicable)

---

### /ask Test with AI Provider (optional)

**Prerequisite:** AI provider configured (Ollama, OpenAI, Anthropic, Google, OpenRouter, Groq, Mistral or xAI)

**For OpenRouter:**
- Ensure `OPENROUTER_API_KEY` is set in `.env`
- Ensure `AI_PROVIDER=openrouter` is set in `.env`
- Optional: `OPENROUTER_MODEL` can be customized (default: `openrouter/auto`)
- Optional: `OPENROUTER_BASE_URL` can be customized (default: `https://openrouter.ai/api/v1`)

**For Ollama:**
```bash
# Check Ollama status
curl http://127.0.0.1:11434/api/tags
```

**For Anthropic:**
- Ensure `ANTHROPIC_API_KEY` is set in `.env`
- Ensure `AI_PROVIDER=anthropic` is set in `.env`
- Optional: `ANTHROPIC_MODEL` can be customized (default: `anthropic/claude-opus-4-6`)

**For Google/Gemini:**
- Ensure that with `AI_PROVIDER=google` either `GEMINI_API_KEY` or `GOOGLE_API_KEY` is set in `.env`
- Ensure `AI_PROVIDER=google` is set in `.env`
- Optional: `GEMINI_MODEL` can be customized (default: `google/gemini-3-flash-preview`)

**For OpenAI:**
- Ensure `OPENAI_API_KEY` is set in `.env`
- Ensure `AI_PROVIDER=openai` is set in `.env`

**For Groq:**
- Ensure `GROQ_API_KEY` is set in `.env`
- Ensure `AI_PROVIDER=groq` is set in `.env`
- Optional: `GROQ_MODEL` can be customized (default: `groq/llama-3.1-8b-instant`)
- Optional: `GROQ_BASE_URL` can be customized (default: `https://api.groq.com/openai/v1`)

**For Mistral:**
- Ensure `MISTRAL_API_KEY` is set in `.env`
- Ensure `AI_PROVIDER=mistral` is set in `.env`
- Optional: `MISTRAL_MODEL` can be customized (default: `mistral/mistral-large-latest`)
- Optional: `MISTRAL_BASE_URL` can be customized (default: `https://api.mistral.ai/v1`)

**For xAI:**
- Ensure `XAI_API_KEY` is set in `.env`
- Ensure `AI_PROVIDER=xai` is set in `.env`
- Optional: `XAI_MODEL` can be customized (default: `xai/grok-4.3`)
- Optional: `XAI_BASE_URL` can be customized (default: `https://api.x.ai/v1`)

**For DeepSeek:**
- Ensure `DEEPSEEK_API_KEY` is set in `.env`
- Ensure `AI_PROVIDER=deepseek` is set in `.env`
- Optional: `DEEPSEEK_MODEL` can be customized (default: `deepseek/deepseek-v4-flash`)
- Optional: `DEEPSEEK_BASE_URL` can be customized (default: `https://api.deepseek.com/v1`)

**Scoped AI Sessions:**
- **Private chats:** Each user has an isolated session (not shared)
- **Groups:** All users in a group share the same session
- **Session lifecycle:** Sessions are automatically reset after 8 hours of inactivity or on day rollover
- **Manual reset:** Users can use `/new` or `/reset` at any time to start a fresh session

**Test – Basic functionality:**
- Send: `/ask What is Python?`
- Expected: A short response from the AI model

**Test – Session isolation in private chats:**
- User A sends: `/ask Remember: My name is Alice`
- User B sends: `/ask What is my name?` (in their own private chat)
- Expected: User B receives no response related to Alice

**Test – Session reset:**
- Send: `/ask Remember: My favorite animal is a dog`
- Send: `/new` or `/reset`
- Send: `/ask What is my favorite animal?`
- Expected: Model doesn't know the previous information anymore (new session)

**MVP Limitations:**
- Timeout after 20 seconds (Ollama) or 30 seconds (OpenAI)
- Maximum response length: 1500 characters
- Sessions are automatically reset after 8h inactivity or day rollover

---

### AI Auto-Reply (Mention/Reply)

The bot can auto-respond via AI when mentioned or replied to in **active scopes** (topics or private chats with AI enabled).

**How it works:**
- **Mention:** Type `@YourBotName` in an active topic or private chat
- **Reply:** Reply to one of the bot's messages in an active scope
- The bot sends the message text to the configured AI and returns the response

**Requirements:**
- User must have role `vip`, `admin`, or `owner`
- User must have accepted consent (`/accept`)
- The scope (topic or private chat) must have AI enabled in the configuration
- The AI service must be configured (Ollama, OpenAI, Anthropic, Google, OpenRouter, Groq, Mistral, xAI or DeepSeek)

**Audit Events:**
- `ai_autoreply_sent` — Response successfully sent
- `ai_autoreply_denied` — Blocked (role or consent)
- `ai_autoreply_error` — AI service error

**Note:** This is separate from the `/ask` command. Auto-reply is triggered implicitly by mentions/replies; `/ask` is an explicit command.

---

### Plugin Test via WebUI

1. Open http://127.0.0.1:8080 in browser
2. Log in with your `WEBUI_PASSWORD`
3. Go to plugin overview – shows all plugins
4. Activate/Deactivate plugins via the management interface

**Note:** Plugins must first exist in `AMO_PLUGIN_DIR`. The plugin system supports Command, Scheduled, and Worker runtimes (MVP).

### Group Role Management via WebUI

1. Open http://127.0.0.1:8080 and log in
2. Go to "Groups" page – shows all groups/supergroups with topic count
3. Click **"Details"** on a group – users with current role are displayed
4. Change role: `admin`, `vip`, `normal`, `ignore`

**Important:**
- `owner` cannot be assigned as a group role (only via `.env`)
- `normal` removes the group-scoped entry → fallback to `normal`
- Roles are group-scoped, not global
- Mutation protection: Login + CSRF token + Owner gate required

---

### WebUI Access Control via Telegram (Block 3)

The `/webui` commands allow the owner to control WebUI access via Telegram.

**Test Steps:**

1. **Test `/webui status` (closed state):**
   - Send: `/webui status`
   - Expected: "CLOSED" or similar message indicating the access window is not open

2. **Test `/webui on`:**
   - Send: `/webui on`
   - Expected: Confirmation that the WebUI access window is now OPEN for 60 minutes

3. **Test `/webui status` (open state):**
   - Send: `/webui status`
   - Expected: "OPEN" with remaining minutes displayed

4. **Test `/webui off`:**
   - Send: `/webui off`
   - Expected: Confirmation that the WebUI access window is now CLOSED

**Negative Tests:**

5. **Test in a group (should be denied):**
   - Add the bot to a test group
   - Send: `/webui status` in the group
   - Expected: Access denied, possibly with a message that this only works in private chats

6. **Test as non-owner (should be denied):**
   - Have a non-owner user send `/webui status`
   - Expected: Access denied, possibly with an authorization error

**Important Notes:**
- These commands only work in **private chats** with the owner
- The access window state is persisted in the database (survives bot restarts)

**Checklist:**
- [ ] `/webui status` shows CLOSED initially
- [ ] `/webui on` opens the access window
- [ ] `/webui status` shows OPEN with remaining time
- [ ] `/webui off` closes the access window
- [ ] `/webui` commands in groups are denied
- [ ] `/webui` commands for non-owners are denied

---

### WebUI HTTP Request Gate (Block 3C)

When `WEBUI_PUBLIC_MODE=true`, the HTTP Request Gate blocks access to protected WebUI pages when the access window is closed.

**Prerequisites:**
- Set `WEBUI_PUBLIC_MODE=true` in `.env`
- Restart the bot/WebUI

**Test Steps:**

1. **With access window CLOSED:**
   - Ensure window is closed: `/webui off` in Owner DM
   - Try to access: `http://127.0.0.1:8080/login`
   - Expected: **403 Forbidden** page

2. **Protected routes also blocked:**
   - Try to access: `http://127.0.0.1:8080/groups`
   - Expected: **403 Forbidden**

3. **Open access window:**
   - Send: `/webui on` in Owner DM
   - Try to access: `http://127.0.0.1:8080/login`
   - Expected: Login page loads normally
   - Log in with password: Should work as usual

4. **Access window expires or closed:**
   - Wait for expiration (or send `/webui off`)
   - Try to access protected pages again
   - Expected: **403 Forbidden** returned

5. **Whitelisted paths remain accessible:**
   - With window CLOSED, test:
     - `http://127.0.0.1:8080/health` — Expected: Health check response (not 403)
     - `http://127.0.0.1:8080/static/css/style.css` — Expected: Static file served
     - `/logout` — Expected: Logout works (if logged in before window closed)

6. **JSON/API responses:**
   - Send request with `Accept: application/json` header to blocked path
   - Expected: `{"error":"forbidden","status":403}`

**Public Mode Off (default):**

7. **With `WEBUI_PUBLIC_MODE=false`:**
   - Close access window: `/webui off`
   - Access `http://127.0.0.1:8080/login`
   - Expected: Login page loads normally (gate is inactive in non-public mode)

**Checklist:**
- [ ] Public mode + closed window → `/login` returns 403
- [ ] Public mode + closed window → `/groups` returns 403
- [ ] `/webui on` in Owner DM → `/login` becomes reachable
- [ ] Normal password login works when window is open
- [ ] `/webui off` or expiration → 403 returned again
- [ ] `/health` remains reachable when window is closed
- [ ] Static assets remain reachable when window is closed
- [ ] JSON requests receive `{"error":"forbidden","status":403}`
- [ ] Non-public mode (`WEBUI_PUBLIC_MODE=false`) → gate inactive, local usage unchanged

**Important Notes:**
- The gate is **only active when `WEBUI_PUBLIC_MODE=true`**
- When the window is open, normal password authentication is still required
- The gate controls whether the login page is *reachable*, not the login itself
- For production/internet deployment, a reverse proxy (nginx, Caddy, Traefik) with HTTPS is still required — Flask should not be exposed directly to the internet

**⚠️ Deployment Note:** This feature enables access control for public deployments, but does not replace proper reverse proxy and HTTPS setup. Nginx/internet deployment configuration is outside the scope of this beta.

---

### Memory Profile Commands (Block C)

- `/memory_profile` shows your coarse private memory profile (only your own `private_user` scope).
- `/memory_profile_set key=value[, key=value]` updates allowed coarse fields (e.g. `language=en,verbosity=high`). Disallowed fields are ignored/rejected.
- `/memory_profile_delete` deletes only your own private profile.

### Consent Commands (Block 1)

The bot now includes user consent management via Telegram commands.

**Test Steps:**

1. **Test `/consent` in private chat:**
   - Send: `/consent`
   - Expected: Shows current consent status, details, and available commands

2. **Test `/accept`:**
   - Send: `/accept`
   - Expected: Confirmation that consent has been accepted

3. **Test `/decline`:**
   - Send: `/decline`
   - Expected: Confirmation that consent has been declined

4. **Test `/consent` after declining:**
   - Send: `/consent`
   - Expected: Shows declined status and reminds that you can `/accept` again

5. **Test re-accepting:**
   - Send: `/accept` (after previously declining)
   - Expected: Consent accepted again successfully

6. **Test `/consent` in groups:**
   - Send: `/consent` in a group where the bot is present
   - Expected: Privacy notice only — no consent status details shown in groups for data protection reasons

**Checklist:**
- [ ] `/consent` in private chat shows full status
- [ ] `/accept` confirms consent accepted
- [ ] `/decline` confirms consent declined
- [ ] `/consent` shows declined status correctly
- [ ] `/accept` works after previous decline
- [ ] `/consent` in groups shows only privacy hint (no details)

---

### Automatic Private Consent DM Prompt (Block 2)

The bot automatically sends a private consent prompt to users who are in "pending" status (not yet accepted or declined).

**How it works:**
- When a pending user is seen in a group, the bot automatically sends them a private DM with a consent notice
- The DM includes **inline buttons** (✅ Accept / ❌ Decline) for quick consent, plus fallback commands: `/accept`, `/decline`, `/consent`
- **One-shot policy:** Exactly 1 automatic DM per user — only sent if `consent_prompt_count == 0`. After successful delivery, `prompt_count` is set to 1 and no further automatic DMs are sent.
- **Unreachable users:** If the bot cannot initiate a private conversation (user hasn't started the bot), the user is marked as `unreachable` and won't receive prompts. The user must start the bot privately and use `/accept` (or the Accept button) to consent.

**Test Steps:**

1. **Test automatic prompt:**
   - Have a new user join a group where the bot is present
   - User should receive exactly one private DM from the bot with the consent notice

2. **Test one-shot policy:**
   - After receiving the first (and only) automatic prompt, the user will not receive any further automatic DMs
   - The `consent_prompt_count` is set to 1 after successful delivery

3. **Test unreachable handling:**
   - If the user hasn't started a private chat with the bot, the DM cannot be delivered
   - User is marked as `unreachable` in the system
   - To become reachable and consent, the user must start the bot privately first and use `/accept`

**Checklist:**
- [ ] Pending users receive exactly one automatic DM prompt when first seen in groups
- [ ] DM contains **inline buttons** (Accept/Decline) and `/accept`, `/decline`, `/consent` fallback commands
- [ ] Inline buttons work: Accept button sets consent to accepted, Decline button sets consent to declined
- [ ] Fallback commands remain usable alongside buttons
- [ ] One-shot policy enforced: only 1 automatic prompt per user (when `consent_prompt_count == 0`)
- [ ] No automatic retries after successful delivery or failure
- [ ] Unreachable users are marked appropriately and must start the bot privately to consent
- [ ] Runtime gate blocks normal usage for `pending`/`declined`/`unreachable` users
- [ ] Allowed commands work despite gate: `/accept`, `/decline`, `/consent`, `/start`
- [ ] `accepted` users can use all commands normally
- [ ] Owner bypass works for consent (owner can always use the bot)
- [ ] Global `ignore` role remains blocking regardless of consent

**Runtime Consent Gate:** The runtime gate is **now active**. Users with status `pending`, `declined`, or `unreachable` cannot use normal bot functions until they `/accept`.

**Allowed commands despite gate:** `/accept`, `/decline`, `/consent`, `/start` — these always work.

**Group behavior:** In groups, only a privacy-preserving notice is shown. No status details are revealed.

**Private block message:** Blocked users in private chats are told to use `/accept` or `/consent`. For `unreachable` users: Start the bot privately first, then `/accept`.

---

### Bot-to-Bot Approval

When AMO is allowed to receive messages from other Telegram bots, new bot senders are not answered automatically.

**Test Steps:**

1. **Test a new bot sender:**
   - Have a second test bot send a message or command to AMO
   - Expected: AMO does not answer that bot
   - Expected: The owner receives a private approval DM with `Allow Bot` and `Block Bot` buttons

2. **Test one-shot behavior:**
   - Have the same pending bot write again
   - Expected: No second owner DM for the same bot

3. **Test allow:**
   - Owner clicks `Allow Bot`
   - The bot may then trigger the diagnostic commands `/ping` and `/help`

4. **Test block:**
   - Owner clicks `Block Bot`
   - Messages from that bot stay unanswered

**Checklist:**
- [ ] New bots are detected as `pending`
- [ ] Pending bots receive no answer
- [ ] Owner DM contains allow/block buttons
- [ ] Only the owner can change bot approvals
- [ ] Other commands such as `/accept` stay blocked for bot peers
- [ ] Blocked bots stay silent
- [ ] Human consent flows stay unchanged
- [ ] Audit/logs contain metadata only (no message texts, no secrets)

---

### WebUI: Topic Soul Editor (KI-F2)

The group detail page includes a **Topic Soul Editor** for configuring topic-specific AI behavior instructions.

**Prerequisites:**
- `WEBUI_OWNER_TELEGRAM_ID` must be set in `.env`
- At least one group with topics (supergroup with topics/threads)

**Test Steps:**

1. **Navigate to group detail page:**
   - Open http://127.0.0.1:8080 and log in
   - Go to "Groups" page
   - Click **"Details"** on a group with topics
   - Expected: Group detail page with topic section is displayed

2. **View Topic Soul:**
   - Find the topic section on the detail page
   - Look at the "Topic Soul" field
   - Expected: Shows current soul text or "-" if not set
   - Note: Content is HTML-escaped (safe rendering)

3. **Edit as Owner:**
   - Enter text in "Topic Soul" textarea (max 4000 chars)
   - Enter optional Display Name and Notes
   - Toggle "enabled" checkbox if needed
   - Click "Speichern" (Save)
   - Expected: Page reloads, changes persisted

4. **Verify persistence:**
   - Reload the detail page
   - Expected: Edited values are displayed

5. **Verify HTML escaping:**
   - Try entering: `<script>alert(1)</script>`
   - Save and reload
   - Expected: Text is escaped, no alert dialog

**Negative Tests:**

6. **Non-owner cannot edit (if applicable):**
   - When `WEBUI_OWNER_TELEGRAM_ID` is not set or different user
   - Expected: Save button is disabled

7. **Length validation:**
   - Try entering >4000 characters
   - Expected: Form validation rejects or truncates

**Checklist:**
- [ ] Groups page shows groups with Details links
- [ ] Group detail page shows topics with Topic Soul form
- [ ] Topic Soul textarea accepts input (max 4000 chars)
- [ ] Display Name and Notes can be edited
- [ ] Enabled checkbox works
- [ ] Changes persist after reload
- [ ] HTML content is properly escaped
- [ ] Save button disabled when owner not configured
- [ ] Form requires CSRF token

---

### WebUI: KI Memory Controls (KI-F3)

The Dashboard includes a **KI Memory** section for inspecting and managing AI memory entries.

**Prerequisites:**
- `WEBUI_OWNER_TELEGRAM_ID` must be set in `.env` for deactivation actions
- Authenticated WebUI session

**Test Steps:**

1. **View Memory Section:**
   - Open http://127.0.0.1:8080 and log in
   - Navigate to Dashboard
   - Expected: "KI Memory (Read-Only + Deactivate Long Memory)" section is visible

2. **Daily Memory (Redacted):**
   - Look at the "Daily memory" entries for any scope
   - Expected: Only dates are shown (e.g., "2026-05-14, 2026-05-13")
   - Expected: No raw summary text is displayed (privacy/conservative default)

3. **Long Memory List:**
   - Check "Long Memories" table for any scope with memory entries
   - Expected: Columns show ID, Summary (fact_text), Status, Created, Updated, Action
   - Expected: Status shows "active" or "inactive"

4. **Deactivate Long Memory as Owner:**
   - Ensure `WEBUI_OWNER_TELEGRAM_ID` is configured in `.env`
   - Find an active long memory entry
   - Click "Deactivate" button (CSRF-protected form)
   - Expected: Page reloads, entry now shows "inactive" status

5. **Verify Deactivation Persistence:**
   - Reload Dashboard
   - Expected: Deactivated entry remains "inactive"

**Negative Tests:**

6. **Deactivate without Owner Config:**
   - Temporarily remove `WEBUI_OWNER_TELEGRAM_ID` from `.env` (or set to empty)
   - Restart WebUI
   - Attempt to deactivate a long memory entry
   - Expected: **403 Forbidden** — mutation is disabled

7. **CSRF Protection:**
   - Try sending POST to `/memory/long/<id>/deactivate` without CSRF token
   - Expected: **400 Bad Request** or redirect with error

**Checklist:**
- [ ] Dashboard shows KI Memory section
- [ ] Daily memory shows dates only (no raw text)
- [ ] Long memory shows fact_text, status, timestamps
- [ ] Deactivate button visible for active entries (with owner config)
- [ ] Deactivation works via CSRF-protected POST
- [ ] Deactivated entries show "inactive" status
- [ ] Without owner config, deactivation returns 403
- [ ] CSRF token required for deactivation

---

### Image Analysis Coreplugin (IMG-B4..IMG-B8)

The image analysis coreplugin provides secure, default-off image analysis for AI and plugins.

**Status:** IMG-B8 implements runtime quota enforcement with rolling 24h window
- Image analysis is disabled by default
- In enabled topics, Telegram photos and image documents are analyzed automatically
- All policy checks happen before provider invocation (deny-before-provider)
- Quota deny writes audit, provider is not called

**Prerequisites:**
- `vip`, `admin` or `owner` role (`ignore` is always blocked)
- Consent granted (`/accept`)

**Role-Based Limits (IMG-B8):**
| Role | Limit |
|------|-------|
| `owner` | unlimited |
| `admin` | unlimited (if enabled) |
| `vip` | configurable (rolling 24h window) |
| `normal` | configurable (rolling 24h window) |
| `ignore` | 0 (always blocked) |

**Rolling 24h Semantics (IMG-B8):**
- The limit applies to a **rolling 24h window** based on audit timestamps
- An event from 23h59m ago counts toward the current limit
- An event from 24h01m ago no longer counts
- No hard daily reset at midnight UTC

**Deny-Before-Provider (IMG-B8):**
- **Check order:** Image validity → topic gate → quota deny → provider invocation
- On quota exceeded, an audit entry is written, provider is **not** called
- Image content is not stored in audit

**Topic Gate (IMG-B2b/IMG-B8):**
- Image analysis can be enabled per-topic
- Default: disabled
- Database-managed

**Telegram Tests:**

1. **Without image (should fail):**
   - Send: `/analyze_image` without an image
   - Expected: Error message or notice that no image was found

2. **Automatic analysis in enabled topic:**
   - Enable image analysis for the test topic in the WebUI
   - Upload a Telegram photo or image document to that topic
   - Expected: Image analysis response (when role, consent, and quota allow it)

3. **With image attachment:**
   - Upload an image with `/analyze_image` as caption
   - Expected: Image analysis response (when enabled and quota available)

4. **As reply to image:**
   - Reply to an image in chat with `/analyze_image`
   - Expected: Image analysis response (when enabled and quota available)

5. **Trusted Telegram photo octet-stream hotfix:**
   - Upload a normal Telegram photo whose Telegram download is returned as `application/octet-stream`
   - Expected: The photo is accepted only when the trusted Telegram photo path has an allowed image suffix; other octet-stream documents remain rejected

**IMG-B8 Quota Tests (when topic enabled):**

6. **Rolling 24h Limit Test (NORMAL role):**
   - As NORMAL user: Run `/analyze_image` with image
   - Expected: Success (or analysis response)
   - After reaching limit: Expected `quota_exceeded`
   - No automatic reset at midnight

7. **Rolling 24h Limit Test (VIP role):**
   - As VIP user: Run `/analyze_image` with image
   - Expected: Success until limit reached
   - After limit: Expected `quota_exceeded`

8. **Limit Reset:**
   - After 24 hours without analysis, limit should reset

**Audit Persistence (IMG-B8):**
- All requests logged to `image_analyze_audit_events`
- Outcome codes: `allowed`, `quota_exceeded`, `topic_disabled`, `role_disabled`
- No image content in audit events (metadata only)
- Quota deny writes audit without provider invocation

**Security Checklist:**
- [ ] Image analysis is default-off (no automatic activation)
- [ ] Minimum role is checked
- [ ] Consent is checked
- [ ] Role IGNORE → `role_disabled` (always blocked)
- [ ] Rolling 24h quota is checked (no hard daily reset)
- [ ] Topic gate is checked
- [ ] No raw image data in logs/audit events
- [ ] Attachment context contains metadata only
- [ ] Audit events are written (DB check optional)
- [ ] Quota deny writes audit without provider invocation

---

### Image Sending (IMG-B4)

The bot supports sending images via Telegram with policy/role/topic gates.

**Prerequisites:**
- `vip`, `admin` or `owner` role
- Consent granted (`/accept`)
- Image sending enabled for the topic

**Policy Gates:**
- Same role checks as text messages (`send_message` capability)
- Topic-safe: Respects `message_thread_id`
- Mime-type aware: Uses `send_photo` for images, `send_document` for files

**Test Steps:**

1. **Send via Plugin Command (if available):**
   - Trigger a plugin that sends an image response
   - Expected: Image appears in chat (topic-safe)

2. **Topic context preservation:**
   - In a topic/thread, trigger image send
   - Expected: Image appears in the same thread (not main chat)

3. **Deny test (insufficient role):**
   - As `normal` user without consent, try to trigger image send
   - Expected: `role_forbidden` or `consent_required`

**Security Checklist:**
- [ ] Image sending requires `send_message` capability
- [ ] Topic-safe: Images respect `message_thread_id`
- [ ] Proper MIME-type selection (photo vs document)
- [ ] Deny reasons communicated generically to users
- [ ] Audit events written with metadata only

---

**IMG-B5 WebUI Test Steps (Image Analysis per Topic):**

1. **Groups Overview:** Open `/groups` in the WebUI. Verify that an "Image Analysis" column (or similar) is shown per group.

2. **Open Group Details:** Click "Details" for a group.

3. **Image Analysis Setting Display:** Per topic, a dropdown or selection is shown with options:
   - **Inherit** — Default, effectively disabled until runtime resolver (IMG-B6) is active
   - **Enabled** — Image analysis explicitly allowed for this topic
   - **Disabled** — Image analysis explicitly denied for this topic

4. **Change Setting:** Select a topic, change the mode to "enabled", save.

5. **Verify Persistence:** Reload the page. The setting should be preserved.

6. **Safe Default Behavior Test:** Create a new topic (or check an unused topic) — it should show "inherit" and be effectively disabled.

---

**IMG-B7 WebUI Test Steps (Image Analysis Role Quotas):**

1. **Open Users Page:** Open http://127.0.0.1:8080 and log in. Navigate to the "Users" page.

2. **View Image Analysis Role Quotas:** Scroll to the "Image analysis role quotas" section. Configuration fields should be displayed for each role (`owner`, `admin`, `vip`, `normal`, `ignore`).

3. **Check Quota Modes:** For each role, the following modes should be available:
   - **Disabled** — Image analysis disabled
   - **Unlimited** — No limit (only allowed for Owner)
   - **Limited** — Limit with positive integer value (rolling 24h window)

4. **Rolling 24h Semantics (IMG-B8):**
   - The limit applies to a **rolling 24h window** based on audit timestamps
   - An event from 23h59m ago counts toward the current limit
   - An event from 24h01m ago no longer counts

5. **Check Conservative Defaults:**
   - Owner should be set to `unlimited` (or changeable)
   - Admin, VIP, Normal should be set to `disabled`
   - Ignore should be set to `disabled` and cannot be set to `unlimited` (always blocked)

6. **Test Limited Mode:**
   - Select a role (e.g., VIP) and set to "Limited"
   - Enter a positive value (e.g., 5)
   - Save
   - Expected: Setting is persisted

7. **Test Validation:**
   - Try to select "Limited" for a role but enter no value or 0
   - Expected: Validation error, cannot save

8. **Ignore Validation:**
   - Try to set the "ignore" role to "Unlimited"
   - Expected: "Unlimited" is not available for Ignore (dropdown disabled or error)

9. **IMG-B8 Runtime Enforcement:**
   - Changes take effect immediately for new requests
   - Quota deny writes audit without provider invocation
   - No image content in audit
   - **Temporary image handling:** Downloaded images are automatically cleaned up after analysis (no persistent storage)

10. **Verify Persistence:**
    - Reload the page
    - Expected: All saved quotas are displayed correctly

**Checklist:**
- [ ] Image Analysis Role Quotas visible on /users
- [ ] All 5 roles configurable (owner, admin, vip, normal, ignore)
- [ ] Modes: disabled, unlimited, limited available
- [ ] Rolling 24h semantics understood (no hard daily reset)
- [ ] Owner can be set to unlimited
- [ ] Ignore is always blocked (regardless of quota)
- [ ] Limited requires positive integer
- [ ] Save persists to database
- [ ] Changes take effect immediately (no restart)
- [ ] IMG-B8 Runtime Enforcement active (quota deny before provider)
- [ ] This is the source of truth for runtime enforcement (IMG-B8)

---

### Webtool Quotas (Issue #48)

Role-based usage quotas for webtools (websearch, webscraping). **Note:** These quotas apply only to webtools, not to normal AI responses via `/ask`.

**Quota Modes:**
| Mode | Description |
|------|-------------|
| `disabled` | Webtool usage disabled |
| `unlimited` | No limit (owner only) |
| `limited` | Daily limit with positive value |

**Command Test:**
- Send: `/webtoolquota`
- Expected: Shows current webtool usage and remaining quota per role

**WebUI Test:**
1. Open http://127.0.0.1:8080 and log in
2. Navigate to the "Users" page
3. Scroll to the "Webtool Role Quotas" section
4. Configure for each role (owner, admin, vip, normal, ignore):
   - **Disabled:** No webtool usage
   - **Unlimited:** Recommended for owner only
   - **Limited:** Positive limit (e.g., 10)

**Privacy/Security:**
- Audit logging is **metadata-only**
- No queries, URLs, prompt/message text, secrets, tokens, or memory content in audit
- Only metadata (role, outcome, timestamp) is logged

**Checklist:**
- [ ] `/webtoolquota` shows current usage
- [ ] WebUI /users shows webtool quota section
- [ ] All 5 roles configurable (disabled/unlimited/limited)
- [ ] Limited requires positive integer
- [ ] Changes take effect immediately (no restart)
- [ ] Audit contains no queries/URLs/prompts (metadata only)

---

### Future Features (Not Yet Implemented)

The following features are planned for future releases and are **not available** in the current beta:

- Additional security enhancements — future blocks
- Video/audio file sending — future enhancement

---

### Block 2 Security Testing Notes

**Login behavior:**
- Wrong credentials return a **generic error message** — no detailed information is revealed
- Repeated failed logins are **progressively delayed** (exponential backoff, capped at max)
- Successful login after failures works normally — the delay counter resets immediately

**Audit Events (internal/optional):**
- Login attempts generate audit events: `webui_login_failure` and `webui_login_success`
- Events include IP address (`remote_addr`) only
- No passwords or sensitive data is logged
- These events are for internal logging/monitoring and do not affect user-facing behavior

---

### What is NOT Tested in MVP

The following features are **not** included in the MVP:

- Channels (private chats and groups only)
- Media sending (images, videos, documents)
- Production-ready security hardening
- Chat history for `/ask`
- Multi-user WebUI (owner login only)

---

### Security Rules

- **Never post your token:** Your `BOT_TOKEN` never belongs in chats, logs, or Git
- **WebUI local only:** Do not bind to `0.0.0.0` or public IPs
- **Check owner ID:** `WEBUI_OWNER_TELEGRAM_ID` must be set correctly
- **Strong passwords:** `WEBUI_PASSWORD` should not be "password123"
- **No secrets in repo:** `.env` is in `.gitignore`

---

### Troubleshooting

**Bot does not respond:**
- Check terminal: Is `python main.py` running?
- Check `.env`: Is `BOT_TOKEN` correct?
- Check Telegram: Did you start the bot (clicked "Start" in chat)?

**DB/SQLite errors:**
- Does the `data/` folder exist?
- Write permissions available?
- Delete DB file (test only!): `rm data/amo_bot.db`

**Ollama not reachable:**
- Is Ollama running? `curl http://127.0.0.1:11434/api/tags`
- Correct URL in `.env`?
- Firewall blocking port 11434?

**WebUI login fails:**
- Is `WEBUI_PASSWORD` set in `.env`?
- Is the value not "change_me" or empty?
- Are you calling `http://127.0.0.1:8080`?

---

### Beta Test Protocol

Use this checklist for your test:

- [ ] Setup complete (venv, pip install)
- [ ] .env configured correctly
- [ ] pytest: All tests passed
- [ ] Smoke test: OK
- [ ] Bot starts without errors
- [ ] WebUI starts without errors
- [ ] Private chat /ping: OK
- [ ] Private chat /help: OK
- [ ] Private chat /consent: OK
- [ ] Private chat /accept: OK
- [ ] Private chat /decline: OK
- [ ] Private chat /role: OK
- [ ] Group test /ping: OK
- [ ] Group test /help: OK
- [ ] Role test /setrole normal: OK
- [ ] Role test /setrole vip: OK
- [ ] Role test restriction admin/owner: OK
- [ ] /ask test (optional): OK / Not tested
- [ ] /new and /reset session management (optional): OK / Not tested
- [ ] AI auto-reply via mention in active scope (optional): OK / Not tested
- [ ] AI auto-reply via reply in active scope (optional): OK / Not tested
- [ ] WebUI login: OK
- [ ] WebUI plugin list: OK
- [ ] WebUI plugin activate/deactivate: OK / Not tested
- [ ] WebUI group role management: OK / Not tested
- [ ] WebUI KI Topic-Agent Status visible on Dashboard: OK / Not tested
- [ ] WebUI Topic Soul Editor (owner-only, in Groups): OK / Not tested
- [ ] WebUI KI Memory Controls (redacted daily, long memory deactivate): OK / Not tested
- [ ] CP-G2 Memory Privacy: Scope isolation, default-deny policy, redacted outputs verified: OK / Not tested
- [ ] Image Analysis Coreplugin (default-off): OK / Not tested
- [ ] IMG-B8 Rolling-24h Quota (no hard daily reset): OK / Not tested
- [ ] IMG-B8 Topic Gate: OK / Not tested
- [ ] IMG-B8 Audit Persistence (quota deny without provider): OK / Not tested
- [ ] IMG-B8 Ignore role always blocked: OK / Not tested
- [ ] IMG-B3 Real provider path (vision analysis works): OK / Not tested
- [ ] IMG-B3 Provider timeout handling: OK / Not tested
- [ ] IMG-B3 Provider error (generic user output): OK / Not tested
- [ ] IMG-B3 MIME-Type validation (JPEG/PNG/WebP/GIF only): OK / Not tested
- [ ] IMG-B3 Size limit (oversize handling): OK / Not tested
- [ ] IMG-B4 Image sending via Telegram API: OK / Not tested
- [ ] IMG-B4 Topic-safe image sending (message_thread_id): OK / Not tested
- [ ] IMG-B4 Deny behavior (role/consent/topic gates): OK / Not tested
- [ ] IMG-B5 WebUI image analysis per topic (inherit/enabled/disabled): OK / Not tested
- [ ] IMG-B7 WebUI Image Analysis Role Quotas (/users page, disabled/unlimited/limited): OK / Not tested
- [ ] Security headers present (check browser dev tools): OK
- [ ] Issue #48 Webtool Quotas (`/webtoolquota` command, WebUI disabled/unlimited/limited): OK / Not tested
- [ ] Issue #48 Webtool Metadata-only Logging (no queries/URLs/prompts in audit): OK / Not tested

**Notes:**

```
Date: ___________
Tester: __________
Result: Passed / Failed / Partial
Observations: _________________________________
_________________________________________________
```


Fireworks provider (GH38): AI_PROVIDER=fireworks with FIREWORKS_API_KEY, FIREWORKS_MODEL, FIREWORKS_BASE_URL, FIREWORKS_TIMEOUT_SECONDS.
