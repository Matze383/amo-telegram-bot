# Release 2026.05.10-Beta

**Status:** Beta / MVP — Private Beta — Not Production-Ready  
**Tag:** `2026.05.10-Beta` on `8bef4c1`

---

## Summary

This release builds on `2026.05.09-Beta` with enhanced group-scoped role management, full WebUI integration for group roles, improved audit trail coverage, and cleaner bilingual documentation. Focus remains on stability and traceability for multi-group deployments.

---

## Highlights

- **Group-Scoped Roles**: Complete separation between global/private roles and per-group roles
- **WebUI Group Management**: Full CRUD for group roles via web interface
- **Audit Events**: All group role changes are now traceable with source attribution
- **Documentation**: Refactored bilingual public docs for clarity

---

## What's Changed

### Group-Scoped Roles

The role system now properly separates global roles from group-specific roles:

- **Global roles** (`owner`, `admin`, `vip`, `normal`, `ignore`) apply in private chats
- In groups, global `owner` and global `ignore` always override; otherwise a group-scoped role applies, or the user falls back to `normal (default)`
- **`/role` command** is now group-aware:
  - In DMs: Shows global role
  - In groups: Shows effective role and its source (global vs. this group)
- **`/setrole` command** respects group context:
  - In DMs: Sets global role
  - In groups: Sets role only for that specific group
- **Permission boundaries**: Group admins can only assign `vip`, `normal`, `ignore` within their own group; `admin` and `owner` require global permissions
- **Cross-group isolation**: Admin status in Group A does not confer admin status in Group B

### WebUI Group Role Management

The WebUI now includes a complete group role management interface:

- **Groups overview page**: Lists all groups/supergroups where the bot is present
- **User role display**: Shows current group role for each member or `normal (default)` if none set
- **Role assignment**: `admin`, `vip`, `normal`, `ignore` can be assigned via dropdown
- **Owner protection**: `owner` role cannot be assigned as a group role (remains `.env`-only)
- **Clearing roles**: Setting to `normal` removes the group-scoped entry, falling back to global role
- **Security**: All mutations require login + CSRF token + owner gate
- **Live tested**: Verified in real Telegram groups and supergroups

### Audit Events & Traceability

Group role changes are now fully auditable:

- **New audit event types**:
  - `group_role_set` — When a group role is assigned or changed
  - `group_role_clear` — When a group role is removed (fallback to global)
- **Source tracking**: Distinguishes between `telegram_command` (Telegram bot commands) and `webui` (Web interface)
- **Complete payload**:
  - `chat_id` — Target group
  - `target_telegram_user_id` — User whose role changed
  - `previous_role` — Role before change (correctly reported even for clears)
  - `new_role` — Role after change
  - `source` — Origin of the change
- **Bugfix**: Previous role is now correctly reported in the response when clearing group roles

### Database Performance

- **Bulk loading** for group role queries in WebUI
- **Database indexes** on `chat_user_roles` for faster lookups
- **`updated_at` hardening** ensures proper timestamp tracking on role mutations

### Documentation

- **Bilingual cleanup**: Public-facing docs refactored and shortened for better readability
- **English + German**: All release notes maintained in both languages
- **Clearer structure**: Separated setup, testing, and release documentation

---

## Tests

Last test run: **141 passed**

---

## Known Limitations

- **Beta / MVP status**: Not production-ready; security hardening ongoing
- **SQLite only**: No PostgreSQL or other database backends yet
- **Local Ollama only**: No cloud AI providers integrated
- **Stateless `/ask`**: No conversation history preserved
- **Text only**: No media handling (images, files, voice)
- **Manual plugin install**: Plugins must be placed in `AMO_PLUGIN_DIR` manually
- **No channels**: Private chats and groups only; channel support not implemented

---

## Upgrade Notes

### From 2026.05.09-Beta

1. **Database**: Schema will auto-migrate on first start (group_roles table, indexes)
2. **No breaking changes**: Existing global roles remain valid
3. **WebUI**: New "Groups" menu item appears automatically

### Fresh Start

```bash
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your values
python main.py
```

### With Cleanup (removes database)

```bash
rm data/amo_bot.db
python main.py
```

---

## Checklist for Testers

- [ ] Group role commands (`/role`, `/setrole`) work in DMs and groups
- [ ] WebUI Groups page loads and shows active groups
- [ ] Group role changes via WebUI are reflected in Telegram
- [ ] Audit events appear for all group role mutations
- [ ] Previous role is correctly shown when clearing group roles
- [ ] `owner` role cannot be set via group role management
- [ ] No sensitive data in logs or responses

---

## Previous Release

See [2026.05.09-Beta Release Notes](RELEASE_NOTES_2026.05.09-Beta_EN.md) for earlier changes.
