# Metabase Codex MCP Plugin

Connect Codex to Metabase so an agent can inspect dashboards, metrics, models, saved questions, tables, and query results through Metabase permissions.

The plugin uses Metabase's native MCP server when it is available. For older Metabase instances, it falls back to a local read-only MCP bridge that calls Metabase's REST API with a user-provided API key or session token.

## What This Plugin Does

- Adds a canonical Codex MCP server named `metabase`.
- Connects to native Metabase MCP at `/api/mcp` for Metabase v0.60 or later.
- Falls back to a bundled read-only Python bridge for older Metabase versions or instances without native MCP enabled.
- Provides Codex skills for safe Metabase analysis and connection smoke tests.
- Keeps Metabase permissions authoritative. Codex only sees objects and results the connected Metabase user or API key can access.
- Avoids raw SQL, writes, admin actions, exports, and destructive operations in the legacy bridge.
- Stores no credentials in plugin files.

## How It Works

The plugin has two modes.

### Native Mode

Use this when your Metabase instance supports the native MCP server.

1. Codex reads `.mcp.json`.
2. The canonical `metabase` server points to `https://your-metabase.example.com/api/mcp`.
3. Metabase handles OAuth authorization.
4. Codex uses the MCP tools returned by Metabase.
5. Metabase enforces user permissions.

Native mode is the preferred mode for Metabase v0.60 or later.

### Legacy Mode

Use this when native MCP is not available.

1. Codex starts `scripts/metabase_legacy_mcp.py` as a local stdio MCP server.
2. The bridge reads credentials from `METABASE_API_KEY`, `METABASE_SESSION_TOKEN`, or macOS Keychain.
3. The bridge calls Metabase REST API endpoints.
4. Metabase enforces API key or session-user permissions.
5. The bridge returns small, read-only, truncated responses to Codex.

Legacy mode exposes conservative tools only:

- `connection_status`
- `search`
- `list_databases`
- `get_database_metadata`
- `get_table`
- `get_dashboard`
- `get_card`
- `run_card`
- `get_current_user`

`run_card` only runs saved Metabase cards/questions. The bridge does not include arbitrary SQL execution.

## Requirements

- Codex with local plugin support.
- A Metabase instance.
- For native mode: Metabase v0.60 or later with native MCP enabled.
- For legacy mode: Metabase REST API access and either an API key or session token.
- Python 3.9 or later.
- HTTPS for production Metabase URLs.

Local development can use `http://localhost:3000`.

## Install In Codex

Use one of these paths to install the plugin into Codex.

### Install From The Marketplace Repo

Recent Codex versions can add a Git-backed marketplace directly:

```bash
codex plugin marketplace add ajatau/metabase-codex-mcp --ref main
```

Then open the Codex plugin directory, choose the `Metabase Codex MCP` marketplace, install `Metabase`, and restart Codex if prompted.

After installation, configure the plugin for your Metabase instance:

```bash
cd ~/.codex/plugins/cache/metabase-codex-mcp/metabase/1.4.0
python3 scripts/configure_metabase.py --url https://your-metabase.example.com --server-mode auto --auth-mode auto
```

If your Codex version does not support `codex plugin marketplace add`, use the manual local install below.

### Manual Local Install

1. Clone the plugin into your Codex plugins directory:

```bash
mkdir -p ~/plugins
git clone https://github.com/ajatau/metabase-codex-mcp.git ~/plugins/metabase
```

2. Configure the plugin for your Metabase instance:

```bash
cd ~/plugins/metabase
python3 scripts/configure_metabase.py --url https://your-metabase.example.com --server-mode auto --auth-mode auto
```

3. Restart Codex so it reloads the plugin manifest and `.mcp.json`.

4. Open Codex and enable or select the `Metabase` plugin if your Codex build shows a plugin picker.

5. Ask Codex to verify the connection:

```text
Run the Metabase plugin smoke test.
```

If the plugin does not appear in Codex after restarting, add it to your local Codex marketplace file.

Create or update `~/.agents/plugins/marketplace.json`:

```json
{
  "name": "local",
  "interface": {
    "displayName": "Local Plugins"
  },
  "plugins": [
    {
      "name": "metabase",
      "source": {
        "source": "local",
        "path": "./plugins/metabase"
      },
      "policy": {
        "installation": "AVAILABLE",
        "authentication": "ON_INSTALL"
      },
      "category": "Data"
    }
  ]
}
```

Then restart Codex again. The marketplace path is relative to your home directory, so `./plugins/metabase` points to `~/plugins/metabase`.

## Configuration

Interactive setup:

```bash
python3 scripts/configure_metabase.py
```

Automatic native-or-legacy setup:

```bash
python3 scripts/configure_metabase.py \
  --url https://your-metabase.example.com \
  --server-mode auto \
  --auth-mode auto
```

Force native MCP:

```bash
python3 scripts/configure_metabase.py \
  --url https://your-metabase.example.com \
  --server-mode native
```

Force legacy mode:

```bash
python3 scripts/configure_metabase.py \
  --url https://your-metabase.example.com \
  --server-mode legacy \
  --auth-mode api-key
```

Show non-secret configuration status:

```bash
python3 scripts/configure_metabase.py --status
```

Check Metabase health, version, and MCP endpoint availability:

```bash
python3 scripts/configure_metabase.py --check
```

## Legacy Authentication

For legacy mode, prefer an API key assigned to a dedicated least-privilege Metabase group.

Set an API key in the environment used to start Codex:

```bash
export METABASE_API_KEY="your-api-key"
```

Or set a session token:

```bash
export METABASE_SESSION_TOKEN="your-session-token"
```

On macOS, you can store a legacy API key in Keychain:

```bash
python3 scripts/configure_metabase.py \
  --url https://your-metabase.example.com \
  --server-mode legacy \
  --auth-mode api-key \
  --store-secret
```

Clear Keychain secrets for the configured URL:

```bash
python3 scripts/configure_metabase.py --clear-secrets
```

Do not commit API keys, session tokens, cookies, OAuth tokens, passwords, or JWT secrets.

## Using The Plugin

In Codex, ask normal analytics questions that refer to Metabase content:

```text
Find the metric behind this KPI and explain its trend.
```

```text
Summarize this dashboard and include the source object IDs.
```

```text
Compare this month's revenue to last month using Metabase.
```

```text
Find the saved question for monthly active users and explain how it is calculated.
```

The `metabase` skill guides Codex to:

- Prefer curated metrics, models, questions, and dashboards before raw tables.
- Inspect object definitions and fields before querying.
- Keep result sets small.
- Cite Metabase object names and IDs when available.
- State filters, date ranges, assumptions, and caveats.
- Avoid exposing sensitive row-level data unless explicitly needed.

## Smoke Testing

Use the bundled `metabase-smoke-test` skill after configuration:

```text
Run the Metabase plugin smoke test.
```

The smoke test checks:

- Connection mode and backend selection.
- Metabase health and version.
- Native MCP availability.
- Legacy auth readiness when legacy mode is active.
- Authenticated user or API-key actor.
- Search access.
- Database metadata visibility.
- Permission or security warnings.

You can also run the standalone compatibility check:

```bash
python3 scripts/check_metabase.py
```

## Security Model

- Native mode delegates authentication to Metabase OAuth.
- Legacy mode reads credentials from environment variables or macOS Keychain.
- `.mcp.json` contains only non-secret configuration.
- Authorization is enforced by Metabase.
- The legacy bridge is read-only.
- Large responses and cell values are truncated.
- Hidden fields are excluded by default.
- The plugin does not include write, admin, export, permission-management, or raw SQL tools.

For production use:

- Use HTTPS.
- Use least-privilege Metabase groups.
- Review `All users` group permissions.
- Review collection, data, row, and column permissions.
- Avoid shared admin accounts.
- Prefer native MCP when available.

See [SECURITY.md](SECURITY.md) for more detail.

## Files

```text
.
├── .codex-plugin/plugin.json        # Codex plugin manifest
├── .mcp.json                        # Local MCP server configuration
├── scripts/
│   ├── configure_metabase.py         # Setup and status helper
│   ├── check_metabase.py             # Compatibility check
│   └── metabase_legacy_mcp.py        # Read-only legacy MCP bridge
├── skills/
│   ├── metabase/                     # Main analysis skill
│   └── metabase-smoke-test/          # Connection validation skill
├── LICENSE
└── SECURITY.md
```

## Development Checks

If you also have the `skill-builder` plugin installed locally, run:

```bash
python3 ~/plugins/skill-builder/scripts/check_plugin.py ~/plugins/metabase
```

This syntax-checks scripts and audits the bundled skills.

## Troubleshooting

If native MCP does not connect:

- Confirm your Metabase instance is v0.60 or later.
- Confirm native MCP and AI features are enabled in Metabase.
- Confirm `https://your-metabase.example.com/api/mcp` does not return `404`.
- Confirm the Metabase Site URL or `MB_SITE_URL` matches the URL Codex uses.
- Rerun `python3 scripts/configure_metabase.py --server-mode auto`.
- Restart Codex after configuration changes.

If legacy mode fails authentication:

- Confirm `METABASE_API_KEY` or `METABASE_SESSION_TOKEN` is available to the Codex process.
- Confirm any macOS Keychain secret was stored for the same Metabase URL.
- Confirm the API key's Metabase group has access to the relevant collections and databases.
- Run `python3 scripts/configure_metabase.py --status`.
- Ask Codex to run the `metabase-smoke-test` skill.

If results are missing:

- Check Metabase collection permissions.
- Check database, schema, table, row, and column permissions.
- Search for the dashboard, metric, model, or saved question by name.
- Prefer verified Metabase objects when multiple candidates exist.

## License

MIT. See [LICENSE](LICENSE).

## References

- [Metabase MCP server](https://www.metabase.com/docs/latest/ai/mcp)
- [Metabase Agent API](https://www.metabase.com/docs/latest/ai/agent-api)
- [Metabase permissions](https://www.metabase.com/docs/latest/permissions/introduction)
