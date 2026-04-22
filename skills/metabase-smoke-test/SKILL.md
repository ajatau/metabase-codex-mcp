---
name: metabase-smoke-test
description: Run and interpret Metabase Codex plugin smoke tests. Use when the user asks to test, verify, validate, smoke-test, or troubleshoot the Metabase plugin, especially after configuring a Metabase URL, storing an API key/session token, restarting Codex, or checking whether the canonical metabase server is mapped to native or legacy backend mode.
---

# Metabase Smoke Test

Use this skill to verify that the Metabase Codex plugin is installed, configured, authenticated, and able to read permitted metadata.

## Rules

- Never ask the user to paste secrets into chat.
- Never print, store, or echo API keys, session tokens, cookies, OAuth tokens, or passwords.
- Prefer the canonical metabase server when it is available in the current session.
- Use metabase-legacy only as a compatibility fallback when the canonical server is stale or unavailable.
- Keep smoke-test queries read-only and small.
- Do not run raw SQL, arbitrary MBQL, exports, admin tools, writes, or destructive operations.

## Core Workflow

1. Run `metabase connection_status` when that tool is available.
   - If the current Codex session has stale server metadata, run `metabase-legacy connection_status` as a temporary fallback and tell the user to restart Codex.
2. Report the active mode:
   - Canonical server: `metabase`.
   - Backend: `native` when native MCP is available.
   - Backend: `legacy` when native MCP is unavailable or `/api/mcp` returns `404`.
3. For legacy mode, require `legacy_ready: true` and `legacy_auth_mode` equal to `api-key` or `session-token`.
4. Run `metabase get_current_user`.
5. Warn if the authenticated actor is a superuser; recommend a least-privilege API key/group for production use.
6. Run `metabase search` with `query: "revenue"` and `limit: 5`.
7. Run `metabase list_databases` without tables.
8. Summarize pass/fail status, active mode, auth readiness, user identity, database visibility, and warnings.

The diagnostic tool names above are available when the canonical `metabase` server is mapped to the legacy bridge. If `metabase` is mapped to native MCP and a diagnostic tool name is absent, use the closest native metadata/search tools available and mark legacy-specific auth checks as not applicable.

## Optional Deeper Checks

Run these only when the user asks for broader validation or the core workflow passes:

- `metabase list_databases` with `include_tables: true`.
- Search for a known table such as `transactions`, then call `get_table` for the discovered table ID.
- Run a known saved card only when the card was discovered by search or the user supplied the card ID.

## Pass Criteria

- `connection_status` returns Metabase health `ok`.
- The recommended canonical server is `metabase` and the backend matches discovered availability.
- Legacy mode has `legacy_ready: true`.
- `get_current_user` returns an active user or API-key actor.
- `search revenue` returns successfully. Zero results can still mean search works; treat it as missing fixture data, not an auth failure.
- `list_databases` returns successfully. Zero databases usually indicates a permission issue, not a bridge failure.

## Local Bridge Fallback

When direct MCP tool calls are unavailable, call the local bridge with one JSON-RPC request per process:

```bash
printf '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"connection_status","arguments":{}}}\n' | python3 ~/plugins/metabase/scripts/metabase_legacy_mcp.py
```

Use the same request shape for other tools by changing `name` and `arguments`.

## Report Format

Start with:

- `PASS` when connection, auth, user lookup, search, and database listing all succeed.
- `PARTIAL` when configuration is correct but auth or permissions are missing.
- `FAIL` when the bridge cannot reach Metabase or required tools fail.

Then include:

- Active mode.
- Metabase version and URL.
- Native MCP availability.
- Legacy auth mode and readiness.
- Authenticated actor, excluding any secret values.
- Database count and database names.
- Warnings, especially superuser API key usage or broad permissions.
