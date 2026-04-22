# Security Notes

## Summary

This plugin connects Codex to Metabase in two modes:

- Native mode for Metabase v0.60 or later, using Metabase's native MCP server.
- Legacy mode for Metabase versions below v0.60, using a local read-only stdio MCP bridge that calls Metabase REST APIs.

It does not include third-party fallback packages, dependency managers, raw SQL tools, write tools, or plugin-file credential storage.

## Data Flow

Native mode:

1. Codex reads `.mcp.json` to find the Metabase MCP endpoint.
2. Codex connects to the configured `https://<metabase-host>/api/mcp` endpoint.
3. Metabase handles OAuth authorization.
4. Metabase returns metadata and query results permitted for the connected user.
5. Codex uses the returned data to answer the user's request.

Legacy mode:

1. Codex starts `scripts/metabase_legacy_mcp.py` over stdio.
2. The bridge reads `METABASE_API_KEY` or `METABASE_SESSION_TOKEN` from the process environment. On macOS, if environment secrets are absent, it can read the configured secret from Keychain.
3. The bridge calls the configured `https://<metabase-host>/api/...` endpoints.
4. Metabase enforces the permissions attached to the API key group or session user.
5. The bridge truncates results and returns read-only MCP tool responses to Codex.

## Trust Boundaries

- Plugin files are local configuration and instructions only.
- Metabase owns authorization, query execution, and permission enforcement.
- Native mode owns OAuth authentication.
- Legacy mode owns only request signing with an environment-provided or Keychain-provided API key or session token.
- Codex receives only the MCP responses returned by Metabase.
- Any AI provider handling depends on the user's Codex deployment and product settings.

## Authentication

- Use Metabase OAuth through the native MCP server when running Metabase v0.60 or later.
- For legacy mode, prefer `METABASE_API_KEY` assigned to a dedicated least-privilege group.
- `METABASE_SESSION_TOKEN` is acceptable for user-scoped legacy access.
- Do not store API keys, passwords, session tokens, cookies, OAuth access tokens, refresh tokens, or JWT secrets in plugin files.
- Do not authorize with a shared admin account.
- Do not use Metabase admin credentials for legacy mode.
- Do not use username/password login from this plugin.

## Configuration Model

- `scripts/configure_metabase.py` writes the Metabase base URL, the selected backend for the canonical `metabase` server, legacy auth mode, and non-secret Keychain service names to `.mcp.json`.
- `metabase` is always the canonical server name. It points to native MCP when available and to the local legacy bridge when native MCP is unavailable.
- `metabase-legacy` is retained as a compatibility and diagnostic alias only.
- The setup script never writes API keys or session tokens to `.mcp.json`, `plugin.json`, README files, or skill files.
- On macOS, the setup script can prompt for a legacy API key or session token and store it in Keychain through the native Security framework.
- The legacy bridge checks environment variables first, then macOS Keychain.
- On non-macOS systems, use process environment variables or the platform secret manager that launches Codex.
- Restart Codex after changing `.mcp.json` or the environment used to start Codex.

## Authorization

Metabase permissions remain authoritative. Before deployment, review:

- Data permissions for databases, schemas, and tables.
- Collection permissions for dashboards, questions, models, and metrics.
- Row and column security for sensitive datasets.
- Download permissions for users who may request result exports outside this plugin.
- The `All users` group, which should not grant broad default access.
- API key group assignment. If a group is deleted, Metabase can reassign associated keys to `All users`; review keys after group changes.

## Network Requirements

- Use HTTPS for production Metabase MCP URLs.
- `http://localhost` is acceptable only for local development.
- Confirm `MB_SITE_URL` or the Metabase Site URL matches the address used by Codex, especially in Docker deployments.

## Audit Checklist

- Runtime code is limited to small standard-library Python scripts included in this plugin.
- No package manager manifest or lockfile is present.
- No third-party fallback MCP server is included.
- No secrets are committed, documented as example values, or written by the setup script to plugin files.
- `.mcp.json` points native backend mode only to the native `/api/mcp` endpoint.
- Legacy mode uses only the local stdio bridge and the configured Metabase base URL.
- Plugin capabilities are limited to `Interactive` and `Read`.
- The legacy bridge exposes no create, update, delete, admin, user-management, permission-management, export, or raw SQL tools.
- User-facing guidance discourages raw row dumps and bulk exports.
- Admin guidance covers Metabase permissions and `MB_SITE_URL`.
- Keychain service names are non-secret and scoped to the configured Metabase URL.
- Keychain writes do not pass secret values through a subprocess command line.

## Operational Guidance

- Use a least-privilege Metabase account for each user.
- Use a dedicated low-privilege group for legacy API keys.
- Prefer curated metrics, models, and verified content over raw tables.
- Keep exploratory queries limited.
- Treat row samples as potentially sensitive.
- Investigate unexpected access by checking Metabase group membership and the effective permissions inherited from all groups.

## Legacy Bridge Controls

- Credentials are read from environment variables or macOS Keychain only.
- Auth headers are redacted from errors and outputs.
- Saved card execution is capped at 200 returned rows.
- Long cell values and large responses are truncated.
- Hidden fields are excluded by default.
- Sensitive fields are excluded from table metadata requests.
- Arbitrary SQL execution is not implemented.
