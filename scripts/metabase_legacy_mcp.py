#!/usr/bin/env python3
"""Read-only MCP bridge for legacy Metabase REST APIs.

This script intentionally uses only the Python standard library. It reads
credentials from environment variables and never logs or prints them.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable


SERVER_NAME = "metabase"
SERVER_VERSION = "1.4.0"
DEFAULT_URL = "http://localhost:3000"
MAX_TEXT_CHARS = 120_000
DEFAULT_MAX_ROWS = 50
MAX_ROWS = 200
MAX_CELL_CHARS = 500
KEYCHAIN_API_KEY_ACCOUNT = "metabase-api-key"
KEYCHAIN_SESSION_TOKEN_ACCOUNT = "metabase-session-token"


class MetabaseError(Exception):
    def __init__(self, message: str, status: int | None = None, body: Any = None):
        super().__init__(message)
        self.status = status
        self.body = body


def env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name, default)
    if isinstance(value, str):
        value = value.strip()
    return value or None


def keychain_service(base_url: str, kind: str) -> str:
    return f"codex-metabase:{base_url.rstrip('/')}:{kind}"


def read_macos_keychain_secret(service: str, account: str) -> str | None:
    if sys.platform != "darwin":
        return None
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(part in lowered for part in ("key", "token", "password", "secret", "session", "cookie")):
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = redact(item)
        return redacted
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def truncate_text(value: Any, limit: int = MAX_TEXT_CHARS) -> str:
    text = value if isinstance(value, str) else json.dumps(value, indent=2, sort_keys=True)
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n[truncated {len(text) - limit} characters]"


def truncate_cell(value: Any) -> Any:
    if isinstance(value, str) and len(value) > MAX_CELL_CHARS:
        return value[:MAX_CELL_CHARS] + f"... [truncated {len(value) - MAX_CELL_CHARS} chars]"
    if isinstance(value, list):
        return [truncate_cell(item) for item in value[:20]]
    if isinstance(value, dict):
        return {key: truncate_cell(item) for key, item in list(value.items())[:50]}
    return value


def normalize_query_value(value: Any) -> Any:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return [normalize_query_value(item) for item in value]
    return value


def native_mcp_endpoint_exists(status: int | str | None) -> bool:
    if not isinstance(status, int):
        return False
    return status not in (404, 410) and status < 500


class MetabaseClient:
    def __init__(self) -> None:
        self.base_url = (env("METABASE_URL", DEFAULT_URL) or DEFAULT_URL).rstrip("/")
        self.auth_mode = (env("METABASE_AUTH_MODE", "auto") or "auto").lower()
        self.timeout = float(env("METABASE_TIMEOUT_SECONDS", "30") or "30")
        self.api_key = env("METABASE_API_KEY")
        self.session_token = env("METABASE_SESSION_TOKEN")
        self.api_key_source = "environment" if self.api_key else None
        self.session_token_source = "environment" if self.session_token else None
        if not self.api_key:
            service = env("METABASE_API_KEY_KEYCHAIN_SERVICE", keychain_service(self.base_url, "api-key"))
            self.api_key = read_macos_keychain_secret(service, KEYCHAIN_API_KEY_ACCOUNT)
            self.api_key_source = "macos-keychain" if self.api_key else None
        if not self.session_token:
            service = env(
                "METABASE_SESSION_TOKEN_KEYCHAIN_SERVICE",
                keychain_service(self.base_url, "session-token"),
            )
            self.session_token = read_macos_keychain_secret(service, KEYCHAIN_SESSION_TOKEN_ACCOUNT)
            self.session_token_source = "macos-keychain" if self.session_token else None

    def configured_auth_mode(self) -> str:
        if self.auth_mode == "api-key":
            return "api-key" if self.api_key else "missing-api-key"
        if self.auth_mode == "session-token":
            return "session-token" if self.session_token else "missing-session-token"
        if self.api_key:
            return "api-key"
        if self.session_token:
            return "session-token"
        return "none"

    def auth_headers(self, required: bool = True) -> dict[str, str]:
        mode = self.configured_auth_mode()
        if mode == "api-key":
            return {"X-API-Key": self.api_key or ""}
        if mode == "session-token":
            return {"X-Metabase-Session": self.session_token or ""}
        if required:
            raise MetabaseError(
                "Legacy Metabase access requires METABASE_API_KEY or METABASE_SESSION_TOKEN in the environment. "
                "Use a least-privilege API key when possible."
            )
        return {}

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        auth_required: bool = True,
    ) -> Any:
        query = ""
        if params:
            clean = {key: normalize_query_value(value) for key, value in params.items() if value is not None}
            query = urllib.parse.urlencode(clean, doseq=True)
        url = self.base_url + path
        if query:
            url += "?" + query

        data = None
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        headers.update(self.auth_headers(required=auth_required))
        if body is not None:
            data = json.dumps(body).encode("utf-8")

        request = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
        started = time.time()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                content_type = response.headers.get("content-type", "")
                raw = response.read()
                if not raw:
                    return None
                if "json" in content_type:
                    return json.loads(raw.decode("utf-8"))
                return raw.decode("utf-8", errors="replace")
        except urllib.error.HTTPError as error:
            raw = error.read()
            parsed: Any = raw.decode("utf-8", errors="replace") if raw else ""
            try:
                parsed = json.loads(parsed) if parsed else ""
            except json.JSONDecodeError:
                pass
            message = f"Metabase API request failed: {method.upper()} {path} returned HTTP {error.code}"
            raise MetabaseError(message, status=error.code, body=redact(parsed)) from None
        except urllib.error.URLError as error:
            raise MetabaseError(f"Metabase API request failed: {method.upper()} {path}: {error.reason}") from None
        finally:
            _ = time.time() - started


def summarize_search(response: Any, limit: int) -> dict[str, Any]:
    items = response.get("data", response) if isinstance(response, dict) else response
    if not isinstance(items, list):
        return {"raw": response}
    return {
        "count_returned": min(len(items), limit),
        "truncated": len(items) > limit,
        "items": [
            {
                key: item.get(key)
                for key in (
                    "id",
                    "model",
                    "name",
                    "description",
                    "collection_id",
                    "collection_name",
                    "database_id",
                    "table_id",
                    "creator_common_name",
                    "updated_at",
                )
                if isinstance(item, dict) and key in item
            }
            for item in items[:limit]
        ],
    }


def summarize_database_metadata(data: dict[str, Any], max_tables: int, max_fields_per_table: int) -> dict[str, Any]:
    tables = data.get("tables", []) if isinstance(data, dict) else []
    summarized_tables = []
    for table in tables[:max_tables]:
        fields = table.get("fields", []) if isinstance(table, dict) else []
        summarized_tables.append(
            {
                "id": table.get("id"),
                "name": table.get("name"),
                "display_name": table.get("display_name"),
                "schema": table.get("schema"),
                "description": table.get("description"),
                "field_count": len(fields),
                "fields": [
                    {
                        "id": field.get("id"),
                        "name": field.get("name"),
                        "display_name": field.get("display_name"),
                        "base_type": field.get("base_type"),
                        "semantic_type": field.get("semantic_type"),
                        "visibility_type": field.get("visibility_type"),
                    }
                    for field in fields[:max_fields_per_table]
                    if isinstance(field, dict)
                ],
                "fields_truncated": len(fields) > max_fields_per_table,
            }
        )
    return {
        "id": data.get("id"),
        "name": data.get("name"),
        "engine": data.get("engine"),
        "table_count": len(tables),
        "tables_returned": len(summarized_tables),
        "tables_truncated": len(tables) > max_tables,
        "tables": summarized_tables,
    }


def summarize_query_result(data: Any, max_rows: int) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {"raw": data}
    result_data = data.get("data") if isinstance(data.get("data"), dict) else data
    rows = result_data.get("rows", []) if isinstance(result_data, dict) else []
    cols = result_data.get("cols", []) if isinstance(result_data, dict) else []
    return {
        "status": data.get("status"),
        "row_count": len(rows),
        "rows_returned": min(len(rows), max_rows),
        "rows_truncated": len(rows) > max_rows,
        "columns": [
            {
                "name": col.get("name"),
                "display_name": col.get("display_name"),
                "base_type": col.get("base_type"),
                "semantic_type": col.get("semantic_type"),
            }
            for col in cols
            if isinstance(col, dict)
        ],
        "rows": [[truncate_cell(cell) for cell in row] for row in rows[:max_rows]],
    }


def tool_connection_status(args: dict[str, Any]) -> dict[str, Any]:
    client = MetabaseClient()
    health = client.request("GET", "/api/health", auth_required=False)
    properties = client.request("GET", "/api/session/properties", auth_required=False)
    version_tag = None
    site_url = None
    if isinstance(properties, dict):
        version = properties.get("version")
        version_tag = version.get("tag") if isinstance(version, dict) else None
        site_url = properties.get("site-url")

    native_status: dict[str, Any]
    try:
        client.request("GET", "/api/mcp", auth_required=False)
        native_status = {"available": True, "status": "reachable"}
    except MetabaseError as error:
        endpoint_exists = native_mcp_endpoint_exists(error.status)
        native_status = {
            "available": endpoint_exists,
            "status": error.status or "error",
            "message": str(error),
        }

    auth_mode = client.configured_auth_mode()
    recommended_backend = "native" if native_status.get("available") else "legacy"
    return {
        "metabase_url": client.base_url,
        "health": health,
        "version": version_tag,
        "site_url": site_url,
        "native_mcp": native_status,
        "legacy_auth_mode": auth_mode,
        "legacy_secret_source": client.api_key_source or client.session_token_source or "none",
        "recommended_server": "metabase",
        "recommended_backend": recommended_backend,
        "legacy_alias": "metabase-legacy",
        "legacy_ready": auth_mode in ("api-key", "session-token"),
    }


def tool_search(args: dict[str, Any]) -> dict[str, Any]:
    client = MetabaseClient()
    limit = clamp_int(args.get("limit", 20), 1, 100)
    params = {
        "q": args.get("query") or args.get("q") or "",
        "models": args.get("models"),
        "archived": bool(args.get("archived", False)),
        "include_metadata": bool(args.get("include_metadata", False)),
    }
    response = client.request("GET", "/api/search/", params=params)
    return summarize_search(response, limit)


def tool_list_databases(args: dict[str, Any]) -> Any:
    client = MetabaseClient()
    response = client.request(
        "GET",
        "/api/database/",
        params={
            "include": "tables" if args.get("include_tables", False) else None,
            "saved": bool(args.get("include_saved", False)),
        },
    )
    if isinstance(response, dict) and isinstance(response.get("data"), list):
        databases = response["data"]
    elif isinstance(response, list):
        databases = response
    else:
        return response
    return {
        "count": len(databases),
        "databases": [
            {
                "id": db.get("id"),
                "name": db.get("name"),
                "engine": db.get("engine"),
                "is_sample": db.get("is_sample"),
                "tables": [
                    {
                        "id": table.get("id"),
                        "name": table.get("name"),
                        "display_name": table.get("display_name"),
                        "schema": table.get("schema"),
                    }
                    for table in (db.get("tables") or [])[:100]
                    if isinstance(table, dict)
                ],
                "tables_truncated": len(db.get("tables") or []) > 100,
            }
            for db in databases
            if isinstance(db, dict)
        ],
    }


def tool_get_database_metadata(args: dict[str, Any]) -> Any:
    database_id = require_int(args, "database_id")
    client = MetabaseClient()
    data = client.request(
        "GET",
        f"/api/database/{database_id}/metadata",
        params={
            "include_hidden": bool(args.get("include_hidden", False)),
            "skip_fields": bool(args.get("skip_fields", False)),
        },
    )
    if args.get("raw"):
        return data
    return summarize_database_metadata(
        data,
        max_tables=clamp_int(args.get("max_tables", 50), 1, 500),
        max_fields_per_table=clamp_int(args.get("max_fields_per_table", 50), 1, 300),
    )


def tool_get_table(args: dict[str, Any]) -> Any:
    table_id = require_int(args, "table_id")
    client = MetabaseClient()
    data = client.request(
        "GET",
        f"/api/table/{table_id}/query_metadata",
        params={
            "include_sensitive_fields": False,
            "include_hidden_fields": bool(args.get("include_hidden_fields", False)),
        },
    )
    if args.get("raw"):
        return data
    fields = data.get("fields", []) if isinstance(data, dict) else []
    return {
        "id": data.get("id"),
        "name": data.get("name"),
        "display_name": data.get("display_name"),
        "schema": data.get("schema"),
        "description": data.get("description"),
        "db_id": data.get("db_id"),
        "field_count": len(fields),
        "fields": [
            {
                "id": field.get("id"),
                "name": field.get("name"),
                "display_name": field.get("display_name"),
                "base_type": field.get("base_type"),
                "semantic_type": field.get("semantic_type"),
                "visibility_type": field.get("visibility_type"),
            }
            for field in fields[: clamp_int(args.get("max_fields", 100), 1, 300)]
            if isinstance(field, dict)
        ],
    }


def tool_get_dashboard(args: dict[str, Any]) -> Any:
    dashboard_id = require_id(args, "dashboard_id")
    client = MetabaseClient()
    data = client.request("GET", f"/api/dashboard/{dashboard_id}")
    if args.get("raw"):
        return data
    dashcards = data.get("dashcards") or data.get("ordered_cards") or []
    return {
        "id": data.get("id"),
        "name": data.get("name"),
        "description": data.get("description"),
        "collection_id": data.get("collection_id"),
        "parameters": data.get("parameters"),
        "card_count": len(dashcards),
        "cards": [
            {
                "dashcard_id": dashcard.get("id"),
                "card_id": (dashcard.get("card") or {}).get("id") if isinstance(dashcard.get("card"), dict) else dashcard.get("card_id"),
                "name": (dashcard.get("card") or {}).get("name") if isinstance(dashcard.get("card"), dict) else None,
                "display": (dashcard.get("card") or {}).get("display") if isinstance(dashcard.get("card"), dict) else None,
            }
            for dashcard in dashcards
            if isinstance(dashcard, dict)
        ],
    }


def tool_get_card(args: dict[str, Any]) -> Any:
    card_id = require_id(args, "card_id")
    client = MetabaseClient()
    data = client.request("GET", f"/api/card/{card_id}")
    if args.get("raw"):
        return data
    return {
        "id": data.get("id"),
        "name": data.get("name"),
        "description": data.get("description"),
        "type": data.get("type"),
        "display": data.get("display"),
        "database_id": data.get("database_id"),
        "table_id": data.get("table_id"),
        "collection_id": data.get("collection_id"),
        "parameters": data.get("parameters"),
        "dataset_query": data.get("dataset_query"),
        "result_metadata": data.get("result_metadata"),
    }


def tool_run_card(args: dict[str, Any]) -> Any:
    card_id = require_id(args, "card_id")
    max_rows = clamp_int(args.get("max_rows", DEFAULT_MAX_ROWS), 1, MAX_ROWS)
    body: dict[str, Any] = {
        "ignore_cache": bool(args.get("ignore_cache", False)),
    }
    if "parameters" in args:
        body["parameters"] = args["parameters"]
    if "dashboard_id" in args:
        body["dashboard_id"] = args["dashboard_id"]
    client = MetabaseClient()
    data = client.request("POST", f"/api/card/{card_id}/query", body=body)
    return summarize_query_result(data, max_rows)


def tool_get_current_user(args: dict[str, Any]) -> Any:
    client = MetabaseClient()
    data = client.request("GET", "/api/user/current")
    if isinstance(data, dict):
        return {
            "id": data.get("id"),
            "email": data.get("email"),
            "first_name": data.get("first_name"),
            "last_name": data.get("last_name"),
            "is_superuser": data.get("is_superuser"),
            "is_active": data.get("is_active"),
        }
    return data


def clamp_int(value: Any, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = minimum
    return max(minimum, min(maximum, parsed))


def require_int(args: dict[str, Any], name: str) -> int:
    if name not in args:
        raise MetabaseError(f"Missing required argument: {name}")
    try:
        value = int(args[name])
    except (TypeError, ValueError):
        raise MetabaseError(f"Argument {name} must be an integer") from None
    if value < 1:
        raise MetabaseError(f"Argument {name} must be greater than zero")
    return value


def require_id(args: dict[str, Any], name: str) -> str:
    if name not in args:
        raise MetabaseError(f"Missing required argument: {name}")
    value = str(args[name]).strip()
    if not value:
        raise MetabaseError(f"Argument {name} must not be empty")
    if not (value.isdigit() or re.match(r"^[A-Za-z0-9_-]{21}$", value)):
        raise MetabaseError(f"Argument {name} must be a numeric ID or 21-character Metabase entity ID")
    return value


def schema_object(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


TOOLS: dict[str, tuple[str, dict[str, Any], Callable[[dict[str, Any]], Any]]] = {
    "connection_status": (
        "Check Metabase version, native MCP availability, and legacy auth readiness.",
        schema_object({}),
        tool_connection_status,
    ),
    "search": (
        "Search permitted Metabase cards, dashboards, models, metrics, tables, databases, and collections.",
        schema_object(
            {
                "query": {"type": "string", "description": "Search text. Empty string lists broadly permitted objects."},
                "models": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["dashboard", "table", "dataset", "segment", "collection", "database", "action", "indexed-entity", "metric", "card"],
                    },
                    "description": "Optional Metabase model filters.",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
                "archived": {"type": "boolean", "default": False},
                "include_metadata": {"type": "boolean", "default": False},
            }
        ),
        tool_search,
    ),
    "list_databases": (
        "List permitted Metabase databases, optionally including visible tables.",
        schema_object(
            {
                "include_tables": {"type": "boolean", "default": False},
                "include_saved": {"type": "boolean", "default": False},
            }
        ),
        tool_list_databases,
    ),
    "get_database_metadata": (
        "Inspect permitted database tables and fields with conservative truncation.",
        schema_object(
            {
                "database_id": {"type": "integer", "minimum": 1},
                "include_hidden": {"type": "boolean", "default": False},
                "skip_fields": {"type": "boolean", "default": False},
                "max_tables": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50},
                "max_fields_per_table": {"type": "integer", "minimum": 1, "maximum": 300, "default": 50},
                "raw": {"type": "boolean", "default": False},
            },
            ["database_id"],
        ),
        tool_get_database_metadata,
    ),
    "get_table": (
        "Inspect one permitted Metabase table's query metadata and fields.",
        schema_object(
            {
                "table_id": {"type": "integer", "minimum": 1},
                "include_hidden_fields": {"type": "boolean", "default": False},
                "max_fields": {"type": "integer", "minimum": 1, "maximum": 300, "default": 100},
                "raw": {"type": "boolean", "default": False},
            },
            ["table_id"],
        ),
        tool_get_table,
    ),
    "get_dashboard": (
        "Fetch dashboard metadata and its cards.",
        schema_object(
            {
                "dashboard_id": {"oneOf": [{"type": "integer", "minimum": 1}, {"type": "string"}]},
                "raw": {"type": "boolean", "default": False},
            },
            ["dashboard_id"],
        ),
        tool_get_dashboard,
    ),
    "get_card": (
        "Fetch saved question, metric, or model metadata without executing it.",
        schema_object(
            {
                "card_id": {"oneOf": [{"type": "integer", "minimum": 1}, {"type": "string"}]},
                "raw": {"type": "boolean", "default": False},
            },
            ["card_id"],
        ),
        tool_get_card,
    ),
    "run_card": (
        "Run a saved Metabase card/question and return a truncated result set. Does not run arbitrary SQL.",
        schema_object(
            {
                "card_id": {"oneOf": [{"type": "integer", "minimum": 1}, {"type": "string"}]},
                "parameters": {"type": "array", "items": {"type": "object"}, "description": "Optional Metabase parameter values."},
                "dashboard_id": {"type": "integer", "minimum": 1},
                "ignore_cache": {"type": "boolean", "default": False},
                "max_rows": {"type": "integer", "minimum": 1, "maximum": MAX_ROWS, "default": DEFAULT_MAX_ROWS},
            },
            ["card_id"],
        ),
        tool_run_card,
    ),
    "get_current_user": (
        "Verify the authenticated Metabase actor without returning tokens.",
        schema_object({}),
        tool_get_current_user,
    ),
}


def mcp_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "description": description,
            "inputSchema": input_schema,
        }
        for name, (description, input_schema, _handler) in TOOLS.items()
    ]


def result_text(value: Any) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": truncate_text(redact(value)),
            }
        ]
    }


def error_text(message: str, body: Any = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"error": message}
    if body not in (None, ""):
        payload["details"] = redact(body)
    return {
        "isError": True,
        "content": [
            {
                "type": "text",
                "text": truncate_text(payload),
            }
        ],
    }


def handle_request(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    request_id = message.get("id")
    is_notification = request_id is None

    try:
        if method == "initialize":
            params = message.get("params") if isinstance(message.get("params"), dict) else {}
            result = {
                "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": mcp_tools()}
        elif method == "tools/call":
            params = message.get("params") if isinstance(message.get("params"), dict) else {}
            name = params.get("name")
            args = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
            if name not in TOOLS:
                result = error_text(f"Unknown tool: {name}")
            else:
                handler = TOOLS[name][2]
                try:
                    result = result_text(handler(args))
                except MetabaseError as error:
                    result = error_text(str(error), error.body)
        elif method in ("resources/list", "prompts/list"):
            result = {"resources": []} if method == "resources/list" else {"prompts": []}
        elif method and method.startswith("notifications/"):
            return None
        else:
            if is_notification:
                return None
            return jsonrpc_error(request_id, -32601, f"Method not found: {method}")
    except Exception as error:  # noqa: BLE001 - keep MCP server alive.
        if env("METABASE_LEGACY_DEBUG"):
            traceback.print_exc(file=sys.stderr)
        if is_notification:
            return None
        return jsonrpc_error(request_id, -32603, f"Internal error: {error}")

    if is_notification:
        return None
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def jsonrpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def read_message() -> tuple[dict[str, Any] | None, bool]:
    first = sys.stdin.buffer.readline()
    if not first:
        return None, False
    if first.lower().startswith(b"content-length:"):
        framed = True
        length = int(first.split(b":", 1)[1].strip())
        while True:
            line = sys.stdin.buffer.readline()
            if line in (b"\r\n", b"\n", b""):
                break
            if line.lower().startswith(b"content-length:"):
                length = int(line.split(b":", 1)[1].strip())
        raw = sys.stdin.buffer.read(length)
    else:
        framed = False
        raw = first
    if not raw.strip():
        return None, framed
    return json.loads(raw.decode("utf-8")), framed


def write_message(message: dict[str, Any], framed: bool) -> None:
    raw = json.dumps(message, separators=(",", ":")).encode("utf-8")
    if framed:
        sys.stdout.buffer.write(f"Content-Length: {len(raw)}\r\n\r\n".encode("ascii"))
        sys.stdout.buffer.write(raw)
    else:
        sys.stdout.buffer.write(raw + b"\n")
    sys.stdout.buffer.flush()


def main() -> int:
    while True:
        try:
            message, framed = read_message()
        except Exception as error:  # noqa: BLE001 - malformed client input.
            if env("METABASE_LEGACY_DEBUG"):
                traceback.print_exc(file=sys.stderr)
            write_message(jsonrpc_error(None, -32700, f"Parse error: {error}"), False)
            continue
        if message is None:
            break
        response = handle_request(message)
        if response is not None:
            write_message(response, framed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
