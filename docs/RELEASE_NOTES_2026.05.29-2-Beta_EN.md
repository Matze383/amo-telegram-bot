# Release Notes 2026.05.29-2-Beta

---

## 🇬🇧 English

### Overview

This release publishes the work since `2026.05.29`: scoped user-profile memory, bot-peer approval, webtool execution through a separate quota-checked path, and the YT-RSS user plugin as a tracked repository plugin.

### New

- **Scoped user-profile memory:** User-profile memory is scope/context-aware and avoids unintended mixing between private, group, and topic contexts.
- **Bot-peer approval:** Other bots are no longer served broadly. Bot peers require owner approval; approved bots only receive the intended interactions.
- **Webtool dispatcher/subagent path:** Websearch and webscraping run through a separate dispatcher/provider-adapter path instead of the normal answer path directly.
- **`/webtoolquota`:** New webtool quota management per role with `disabled`, `limited`, and `unlimited` modes.
- **YT-RSS user plugin in the repository:** The existing `plugins/yt_rss` user plugin is now tracked and available on GitHub.

### Improved

- **WebUI quota configuration:** Webtool quotas can be managed through the WebUI in the user/role area.
- **Provider fail-closed behavior:** Webtool providers fail in a controlled way when provider/configuration is unavailable; there is no direct fallback.
- **Plugin repository rule:** User plugins are versioned by default going forward unless a plugin is explicitly marked private/local.

### Security & Privacy

- **Quota before execution:** Webtool role quotas are checked before provider/subagent execution.
- **Sanitized output:** Webtool results are compacted and sanitized against prompt injection before being returned to the main path.
- **Metadata-only logging/audit:** Logs and audit entries contain no prompts, message text, queries, URLs, secrets, tokens, `.env` contents, or private context.
- **Bot-peer protection:** Bot peers remain blocked or silent without explicit owner approval.
- **No secrets in the YT-RSS plugin:** The tracked user plugin contains no runtime state files, caches, tokens, or local secrets.

### Architecture / Internal

- New webtool facade/dispatcher path with provider adapters for the existing websearch/webscraping coreplugins.
- Webtool usage is separate from normal `/ask` AI responses; there is no general AI-response quota as final scope.
- `plugins/` is no longer globally ignored; cache/runtime artifacts remain excluded by existing ignore rules.

### Quality Assurance

- Issue #48 main verification: `git diff --check` clean; targeted webtool/quota/dispatcher/i18n/db tests: `131 passed`.
- YT-RSS/userplugin verification: relevant plugin/userplugin tests: `95 passed`; additional YT-RSS tests: `30 passed`.
- QA gates: PASS for webtool architecture, docs, and the tracked user plugin.

### Operational Notes

- GitHub CI remains manually disabled per maintainer decision.
- Normal `/ask` AI responses are not limited by webtool quotas.
- No breaking changes expected for end users.

---

*Last updated: 2026-05-29*
