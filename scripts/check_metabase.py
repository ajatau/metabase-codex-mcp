#!/usr/bin/env python3
"""Quick Metabase compatibility check for the plugin."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request


METABASE_URL = os.environ.get("METABASE_URL", "http://localhost:3000").rstrip("/")
API_KEY_ACCOUNT = "metabase-api-key"
SESSION_TOKEN_ACCOUNT = "metabase-session-token"


def keychain_service(kind: str) -> str:
    return f"codex-metabase:{METABASE_URL}:{kind}"


def keychain_item_exists(service: str, account: str) -> bool:
    if sys.platform != "darwin":
        return False
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-a", account],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def get_json(path: str):
    request = urllib.request.Request(METABASE_URL + path, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=20) as response:
        raw = response.read()
        return json.loads(raw.decode("utf-8")) if raw else None


def get_status(path: str):
    request = urllib.request.Request(METABASE_URL + path, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            response.read()
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def native_mcp_endpoint_exists(status: int | str | None) -> bool:
    if not isinstance(status, int):
        return False
    return status not in (404, 410) and status < 500


def main() -> int:
    health = get_json("/api/health")
    properties = get_json("/api/session/properties")
    version = (properties.get("version") or {}).get("tag") if isinstance(properties, dict) else None
    mcp_status = get_status("/api/mcp")
    native_available = native_mcp_endpoint_exists(mcp_status)
    api_key_present = bool(os.environ.get("METABASE_API_KEY")) or keychain_item_exists(keychain_service("api-key"), API_KEY_ACCOUNT)
    session_token_present = bool(os.environ.get("METABASE_SESSION_TOKEN")) or keychain_item_exists(
        keychain_service("session-token"),
        SESSION_TOKEN_ACCOUNT,
    )
    auth_mode = "api-key" if api_key_present else "session-token" if session_token_present else "none"
    result = {
        "metabase_url": METABASE_URL,
        "health": health,
        "version": version,
        "native_mcp_http_status": mcp_status,
        "recommended_server": "metabase",
        "recommended_backend": "native" if native_available else "legacy",
        "legacy_alias": "metabase-legacy",
        "legacy_auth_mode": auth_mode,
        "legacy_secret_source": "environment-or-keychain" if auth_mode != "none" else "none",
        "legacy_ready": auth_mode in ("api-key", "session-token"),
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
