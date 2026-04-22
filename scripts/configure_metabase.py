#!/usr/bin/env python3
"""Configure the Metabase Codex plugin without writing secrets to plugin files."""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import getpass
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
MCP_FILE = PLUGIN_ROOT / ".mcp.json"
DEFAULT_URL = "http://localhost:3000"
API_KEY_ACCOUNT = "metabase-api-key"
SESSION_TOKEN_ACCOUNT = "metabase-session-token"


class ConfigError(Exception):
    pass


def normalize_metabase_url(raw_url: str) -> str:
    value = raw_url.strip()
    if not value:
        raise ConfigError("Metabase URL is required.")
    if "://" not in value:
        value = "https://" + value

    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in ("http", "https"):
        raise ConfigError("Metabase URL must use http or https.")
    if not parsed.netloc:
        raise ConfigError("Metabase URL must include a hostname.")

    path = parsed.path.rstrip("/")
    if path.endswith("/api/mcp"):
        path = path[: -len("/api/mcp")]
    elif path.endswith("/api"):
        path = path[: -len("/api")]

    normalized = parsed._replace(path=path, params="", query="", fragment="").geturl().rstrip("/")
    return normalized


def keychain_service(base_url: str, kind: str) -> str:
    return f"codex-metabase:{base_url.rstrip('/')}:{kind}"


def is_macos() -> bool:
    return sys.platform == "darwin"


def run_security(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["security", *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )


def keychain_item_exists(service: str, account: str) -> bool:
    if not is_macos():
        return False
    result = run_security(["find-generic-password", "-s", service, "-a", account])
    return result.returncode == 0


def keychain_status_message(status: int) -> str:
    messages = {
        0: "success",
        -25299: "duplicate item",
        -25300: "item not found",
        -25291: "keychain interaction not allowed",
    }
    return messages.get(status, f"Security.framework status {status}")


def release_cf_ref(ref: ctypes.c_void_p) -> None:
    if not ref.value:
        return
    core_foundation_path = ctypes.util.find_library("CoreFoundation")
    if not core_foundation_path:
        return
    core_foundation = ctypes.CDLL(core_foundation_path)
    core_foundation.CFRelease.argtypes = [ctypes.c_void_p]
    core_foundation.CFRelease.restype = None
    core_foundation.CFRelease(ref)


def store_keychain_secret(service: str, account: str, secret: str) -> None:
    if not is_macos():
        raise ConfigError("macOS Keychain storage is only available on macOS.")

    security_path = ctypes.util.find_library("Security")
    if not security_path:
        raise ConfigError("Could not load macOS Security.framework.")

    security = ctypes.CDLL(security_path)
    sec_keychain_add = security.SecKeychainAddGenericPassword
    sec_keychain_add.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_char_p,
        ctypes.c_uint32,
        ctypes.c_char_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    sec_keychain_add.restype = ctypes.c_int32

    sec_keychain_find = security.SecKeychainFindGenericPassword
    sec_keychain_find.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_char_p,
        ctypes.c_uint32,
        ctypes.c_char_p,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    sec_keychain_find.restype = ctypes.c_int32

    sec_keychain_modify = security.SecKeychainItemModifyAttributesAndData
    sec_keychain_modify.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]
    sec_keychain_modify.restype = ctypes.c_int32

    sec_keychain_free = security.SecKeychainItemFreeContent
    sec_keychain_free.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    sec_keychain_free.restype = ctypes.c_int32

    service_bytes = service.encode("utf-8")
    account_bytes = account.encode("utf-8")
    secret_bytes = secret.encode("utf-8")
    secret_buffer = ctypes.create_string_buffer(secret_bytes)
    secret_ptr = ctypes.cast(secret_buffer, ctypes.c_void_p)
    item_ref = ctypes.c_void_p()

    status = sec_keychain_add(
        None,
        len(service_bytes),
        ctypes.c_char_p(service_bytes),
        len(account_bytes),
        ctypes.c_char_p(account_bytes),
        len(secret_bytes),
        secret_ptr,
        ctypes.byref(item_ref),
    )
    if status == 0:
        release_cf_ref(item_ref)
        return

    if status != -25299:
        raise ConfigError(f"Could not store secret in macOS Keychain: {keychain_status_message(status)}")

    password_length = ctypes.c_uint32()
    password_data = ctypes.c_void_p()
    existing_item_ref = ctypes.c_void_p()
    status = sec_keychain_find(
        None,
        len(service_bytes),
        ctypes.c_char_p(service_bytes),
        len(account_bytes),
        ctypes.c_char_p(account_bytes),
        ctypes.byref(password_length),
        ctypes.byref(password_data),
        ctypes.byref(existing_item_ref),
    )
    if status != 0:
        raise ConfigError(f"Could not find existing Keychain item: {keychain_status_message(status)}")

    try:
        if password_data.value:
            sec_keychain_free(None, password_data)
        status = sec_keychain_modify(existing_item_ref, None, len(secret_bytes), secret_ptr)
        if status != 0:
            raise ConfigError(f"Could not update existing Keychain item: {keychain_status_message(status)}")
    finally:
        release_cf_ref(existing_item_ref)


def delete_keychain_secret(service: str, account: str) -> bool:
    if not is_macos():
        return False
    result = run_security(["delete-generic-password", "-s", service, "-a", account])
    return result.returncode == 0


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def existing_url() -> str:
    configured = read_json(MCP_FILE)
    if isinstance(configured, dict):
        servers = configured.get("mcpServers")
        if isinstance(servers, dict):
            for server_name in ("metabase", "metabase-legacy"):
                server = servers.get(server_name)
                server_env = server.get("env") if isinstance(server, dict) else None
                if isinstance(server_env, dict) and server_env.get("METABASE_URL"):
                    return normalize_metabase_url(str(server_env["METABASE_URL"]))

            for server_name in ("metabase", "metabase-native"):
                server = servers.get(server_name)
                native_url = server.get("url") if isinstance(server, dict) else None
                if native_url:
                    return normalize_metabase_url(str(native_url))
    return normalize_metabase_url(os.environ.get("METABASE_URL", DEFAULT_URL))


def native_mcp_endpoint_exists(status: int | str | None) -> bool:
    if not isinstance(status, int):
        return False
    return status not in (404, 410) and status < 500


def choose_server_mode(base_url: str, requested: str) -> tuple[str, dict[str, Any] | None]:
    if requested in ("native", "legacy"):
        return requested, None
    if requested != "auto":
        raise ConfigError(f"Unsupported server mode: {requested}")

    check = check_instance(base_url)
    if native_mcp_endpoint_exists(check.get("native_mcp_http_status")):
        return "native", check
    return "legacy", check


def make_native_server_config(base_url: str, role: str) -> dict[str, Any]:
    return {
        "type": "http",
        "url": f"{base_url}/api/mcp",
        "note": (
            f"{role} Native Metabase MCP server. Authentication is handled by Metabase OAuth; "
            "do not place API keys, passwords, or tokens in this file."
        ),
    }


def make_legacy_server_config(base_url: str, auth_mode: str, role: str) -> dict[str, Any]:
    return {
        "command": "python3",
        "args": ["./scripts/metabase_legacy_mcp.py"],
        "cwd": ".",
        "env": {
            "METABASE_URL": base_url,
            "METABASE_AUTH_MODE": auth_mode,
            "METABASE_API_KEY_KEYCHAIN_SERVICE": keychain_service(base_url, "api-key"),
            "METABASE_SESSION_TOKEN_KEYCHAIN_SERVICE": keychain_service(base_url, "session-token"),
        },
        "note": (
            f"{role} Read-only stdio MCP bridge for Metabase versions below 60 or instances without "
            "native MCP enabled. It reads secrets from METABASE_API_KEY, METABASE_SESSION_TOKEN, or "
            "macOS Keychain. Do not put secrets in this file."
        ),
    }


def make_mcp_config(base_url: str, auth_mode: str, server_mode: str) -> dict[str, Any]:
    selected_mode, _detection = choose_server_mode(base_url, server_mode)
    servers: dict[str, Any] = {}
    if selected_mode == "native":
        servers["metabase"] = make_native_server_config(base_url, "Canonical server selected by configure_metabase.py.")
        servers["metabase-legacy"] = make_legacy_server_config(base_url, auth_mode, "Compatibility alias.")
    else:
        servers["metabase"] = make_legacy_server_config(base_url, auth_mode, "Canonical server selected by configure_metabase.py.")
        servers["metabase-legacy"] = make_legacy_server_config(base_url, auth_mode, "Compatibility alias.")

    return {"mcpServers": servers}


def write_mcp_config(base_url: str, auth_mode: str, server_mode: str, dry_run: bool) -> None:
    config = make_mcp_config(base_url, auth_mode, server_mode)
    if dry_run:
        print(json.dumps(config, indent=2, sort_keys=True))
        return

    temp_path = MCP_FILE.with_suffix(".json.tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2)
        handle.write("\n")
    temp_path.replace(MCP_FILE)


def prompt_default(label: str, default: str) -> str:
    value = input(f"{label} [{default}]: ").strip()
    return value or default


def prompt_choice(label: str, choices: list[str], default: str) -> str:
    choice_text = "/".join(choices)
    while True:
        value = input(f"{label} ({choice_text}) [{default}]: ").strip().lower() or default
        if value in choices:
            return value
        print(f"Choose one of: {choice_text}")


def prompt_yes_no(label: str, default: bool = False) -> bool:
    suffix = "Y/n" if default else "y/N"
    while True:
        value = input(f"{label} [{suffix}]: ").strip().lower()
        if not value:
            return default
        if value in ("y", "yes"):
            return True
        if value in ("n", "no"):
            return False
        print("Enter yes or no.")


def secret_kind_for_auth_mode(auth_mode: str) -> str | None:
    if auth_mode == "api-key":
        return "api-key"
    if auth_mode == "session-token":
        return "session-token"
    return None


def account_for_secret(kind: str) -> str:
    if kind == "api-key":
        return API_KEY_ACCOUNT
    if kind == "session-token":
        return SESSION_TOKEN_ACCOUNT
    raise ConfigError(f"Unsupported secret kind: {kind}")


def prompt_and_store_secret(base_url: str, kind: str, dry_run: bool) -> None:
    if not is_macos():
        raise ConfigError(
            "This setup script can store secrets in macOS Keychain only. "
            "On this platform, set METABASE_API_KEY or METABASE_SESSION_TOKEN in the environment used to start Codex."
        )

    label = "API key" if kind == "api-key" else "session token"
    service = keychain_service(base_url, kind)
    if dry_run:
        print(f"Would store {label} in macOS Keychain service {service!r}.")
        return

    secret = getpass.getpass(f"Enter Metabase {label}: ").strip()
    if not secret:
        raise ConfigError(f"Metabase {label} was empty.")

    confirm = getpass.getpass(f"Re-enter Metabase {label}: ").strip()
    if secret != confirm:
        raise ConfigError("Secret values did not match.")

    store_keychain_secret(service, account_for_secret(kind), secret)
    print(f"Stored Metabase {label} in macOS Keychain service {service!r}.")


def check_http_status(base_url: str, path: str) -> int | str:
    request = urllib.request.Request(base_url + path, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            response.read()
            return response.status
    except urllib.error.HTTPError as error:
        return error.code
    except urllib.error.URLError as error:
        return str(error.reason)


def get_json(base_url: str, path: str) -> Any:
    request = urllib.request.Request(base_url + path, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=20) as response:
        raw = response.read()
        return json.loads(raw.decode("utf-8")) if raw else None


def check_instance(base_url: str) -> dict[str, Any]:
    health: Any
    properties: Any
    try:
        health = get_json(base_url, "/api/health")
    except Exception as error:  # noqa: BLE001 - this is a diagnostic command.
        health = {"error": str(error)}
    try:
        properties = get_json(base_url, "/api/session/properties")
    except Exception as error:  # noqa: BLE001 - this is a diagnostic command.
        properties = {"error": str(error)}

    version = None
    if isinstance(properties, dict) and isinstance(properties.get("version"), dict):
        version = properties["version"].get("tag")
    native_status = check_http_status(base_url, "/api/mcp")
    return {
        "metabase_url": base_url,
        "health": health,
        "version": version,
        "native_mcp_http_status": native_status,
        "native_mcp_url": f"{base_url}/api/mcp",
    }


def print_status(base_url: str) -> None:
    auth_mode = "auto"
    selected_backend = "unknown"
    canonical_transport = "unknown"
    configured = read_json(MCP_FILE)
    if isinstance(configured, dict):
        servers = configured.get("mcpServers")
        metadata = configured.get("metadata")
        if isinstance(metadata, dict) and metadata.get("selected_backend"):
            selected_backend = str(metadata["selected_backend"])
        canonical = servers.get("metabase") if isinstance(servers, dict) else None
        if isinstance(canonical, dict):
            if canonical.get("type") == "http":
                canonical_transport = "native-http"
                selected_backend = "native" if selected_backend == "unknown" else selected_backend
            elif canonical.get("command"):
                canonical_transport = "legacy-stdio"
                selected_backend = "legacy" if selected_backend == "unknown" else selected_backend
        legacy = servers.get("metabase-legacy") if isinstance(servers, dict) else None
        legacy_env = legacy.get("env") if isinstance(legacy, dict) else None
        if isinstance(legacy_env, dict) and legacy_env.get("METABASE_AUTH_MODE"):
            auth_mode = str(legacy_env["METABASE_AUTH_MODE"])

    api_service = keychain_service(base_url, "api-key")
    session_service = keychain_service(base_url, "session-token")
    status = {
        "plugin_root": str(PLUGIN_ROOT),
        "mcp_config": str(MCP_FILE),
        "metabase_url": base_url,
        "canonical_server": "metabase",
        "selected_backend": selected_backend,
        "canonical_transport": canonical_transport,
        "native_mcp_url": f"{base_url}/api/mcp",
        "legacy_auth_mode": auth_mode,
        "environment_api_key_present": bool(os.environ.get("METABASE_API_KEY")),
        "environment_session_token_present": bool(os.environ.get("METABASE_SESSION_TOKEN")),
        "macos_keychain_available": is_macos(),
        "keychain_api_key_present": keychain_item_exists(api_service, API_KEY_ACCOUNT),
        "keychain_session_token_present": keychain_item_exists(session_service, SESSION_TOKEN_ACCOUNT),
    }
    print(json.dumps(status, indent=2, sort_keys=True))


def clear_keychain_secrets(base_url: str) -> None:
    cleared = []
    for kind, account in (("api-key", API_KEY_ACCOUNT), ("session-token", SESSION_TOKEN_ACCOUNT)):
        service = keychain_service(base_url, kind)
        if delete_keychain_secret(service, account):
            cleared.append(kind)
    print(json.dumps({"metabase_url": base_url, "cleared_keychain_secrets": cleared}, indent=2, sort_keys=True))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Configure the local Metabase Codex plugin.")
    parser.add_argument("--url", help="Metabase base URL, for example https://metabase.example.com")
    parser.add_argument(
        "--server-mode",
        choices=("auto", "native", "legacy"),
        help="Backend used by the canonical metabase server. Auto detects the native MCP endpoint.",
    )
    parser.add_argument(
        "--auth-mode",
        choices=("auto", "api-key", "session-token"),
        help="Legacy auth mode used by the pre-v0.60 bridge.",
    )
    parser.add_argument(
        "--store-secret",
        action="store_true",
        help="Prompt for the selected legacy secret and store it in macOS Keychain.",
    )
    parser.add_argument("--clear-secrets", action="store_true", help="Delete stored macOS Keychain items for this URL.")
    parser.add_argument("--status", action="store_true", help="Show non-secret plugin configuration status.")
    parser.add_argument("--check", action="store_true", help="Check health, version, and native MCP endpoint status.")
    parser.add_argument("--dry-run", action="store_true", help="Print planned changes without writing files or secrets.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    interactive = not any(
        (
            args.url,
            args.server_mode,
            args.auth_mode,
            args.store_secret,
            args.clear_secrets,
            args.status,
            args.check,
            args.dry_run,
        )
    )

    try:
        base_url = normalize_metabase_url(args.url) if args.url else existing_url()

        if interactive:
            base_url = normalize_metabase_url(prompt_default("Metabase URL", base_url))
            server_mode = prompt_choice("Canonical metabase backend", ["auto", "native", "legacy"], "auto")
            auth_mode = prompt_choice("Legacy auth mode for pre-v0.60 Metabase", ["auto", "api-key", "session-token"], "auto")
            write_mcp_config(base_url, auth_mode, server_mode, dry_run=False)

            if is_macos():
                if auth_mode == "auto":
                    secret_choice = prompt_choice("Store a legacy secret in macOS Keychain now", ["none", "api-key", "session-token"], "none")
                else:
                    secret_choice = auth_mode if prompt_yes_no(f"Store {auth_mode} in macOS Keychain now") else "none"
                if secret_choice != "none":
                    prompt_and_store_secret(base_url, secret_choice, dry_run=False)
            else:
                print("Set METABASE_API_KEY or METABASE_SESSION_TOKEN in the environment used to start Codex for legacy mode.")

            print("Configuration updated. Restart Codex so the plugin reloads the new MCP settings.")
            return 0

        auth_mode = args.auth_mode or "auto"
        server_mode = args.server_mode or "auto"

        if args.status:
            print_status(base_url)
            return 0

        if args.clear_secrets:
            clear_keychain_secrets(base_url)
            return 0

        write_mcp_config(base_url, auth_mode, server_mode, dry_run=args.dry_run)

        secret_kind = secret_kind_for_auth_mode(auth_mode)
        if args.store_secret:
            if not secret_kind:
                raise ConfigError("--store-secret requires --auth-mode api-key or --auth-mode session-token.")
            prompt_and_store_secret(base_url, secret_kind, dry_run=args.dry_run)

        if args.check:
            print(json.dumps(check_instance(base_url), indent=2, sort_keys=True))

        if not args.dry_run:
            print("Configuration updated. Restart Codex so the plugin reloads the new MCP settings.")
        return 0
    except (ConfigError, OSError, json.JSONDecodeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
