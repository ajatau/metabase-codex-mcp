---
name: metabase
description: Use when working with a Metabase MCP connection to analyze metrics, models, tables, dashboards, or query results while preserving permissions, limiting data exposure, and favoring curated semantic objects.
---

# Metabase

Use this skill when the user asks to analyze, explain, compare, validate, or investigate data available through Metabase.

## Operating Principles

- Treat Metabase as the source of truth for available analytics objects and permissions.
- Use the canonical `metabase` server name for normal work.
- Treat `metabase-legacy` only as a compatibility and diagnostic alias.
- The plugin setup maps `metabase` to native MCP when available, or to the local legacy bridge when `/api/mcp` is unavailable.
- Do not ask for, store, print, or infer Metabase passwords, API keys, session tokens, cookies, OAuth tokens, or JWT secrets.
- If the plugin needs configuration, point the user to `~/plugins/metabase/scripts/configure_metabase.py`; it writes only non-secret URL settings to `.mcp.json`.
- Do not suggest bypassing Metabase permissions. Results are limited to the connected user's access.
- Prefer curated Metabase metrics, models, questions, and dashboards before querying raw tables.
- Prefer aggregated answers over raw row dumps.
- Keep result sets small. Ask before retrieving large samples or repeated pages of data.
- Avoid exposing direct personal data unless the user explicitly requests it and the returned Metabase data permits it.
- Do not attempt admin, mutation, destructive, or write workflows through Metabase.
- In legacy mode, do not ask for raw SQL execution; use saved cards/questions, curated objects, and metadata tools.

## Workflow

1. Clarify the business question when the requested metric, date range, grouping, or comparison is ambiguous.
2. Search Metabase for relevant metrics, models, tables, questions, and dashboards.
3. Prefer verified or curated objects when multiple candidates exist.
4. Inspect object fields and dimensions before constructing or running queries.
5. Use existing metrics or models when possible instead of re-creating logic from raw tables.
6. Run the smallest query that answers the question.
7. Summarize the result with the source object names and IDs available from Metabase.
8. State assumptions, filters, date ranges, and limitations in the answer.

For connection or routing questions, first call `metabase connection_status` when that tool is available. If the canonical `metabase` server is not yet refreshed in the current Codex session, use `metabase-legacy` only as a temporary diagnostic fallback and tell the user to restart Codex after running `~/plugins/metabase/scripts/configure_metabase.py --server-mode auto`.

## Query Discipline

- Do not construct a query until the target table or metric and relevant fields are understood.
- Use explicit date filters for time-based analysis.
- Use limits for exploratory queries.
- For comparisons, keep groupings and filters consistent across periods.
- Validate surprising results by checking definitions, filters, joins, or sample field values before presenting conclusions.
- When data is incomplete or ambiguous, say so directly instead of filling gaps with guesses.

## Security And Privacy

- Never include secrets in generated files, code snippets, logs, or chat answers.
- Never recommend using a shared admin account for OAuth authorization.
- Treat rows, field samples, and query results as potentially sensitive.
- Redact or aggregate personal data unless the user has a clear need for row-level output.
- Do not export bulk data or instruct another tool to do so unless the user explicitly asks and confirms the destination.
- If Metabase denies access, explain that the user's Metabase permissions control availability.
- For legacy API key mode, remind the user that access is scoped to the API key's Metabase group, not an individual user.
- Legacy secrets may come from environment variables or macOS Keychain; never ask the user to place them in plugin files.

## Answer Format

When presenting analysis, include:

- The direct answer first.
- The Metabase objects used, including names and IDs when available.
- The filters, date ranges, and grouping dimensions applied.
- Any caveats about permissions, row limits, missing fields, or ambiguous definitions.
- Suggested follow-up checks only when they would materially improve confidence.
