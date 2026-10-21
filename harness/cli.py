"""`orion` command line: run the server, manage settings and users, inspect connectors and MCP servers.

    orion serve --port 8000
    orion mcp                      # MCP server over stdio
    orion check --ping
    orion settings list | get KEY | set KEY VALUE | reset KEY
    orion users list | create NAME [--role super_user]
    orion connectors list | test ID
    orion version

Settings and users are written with the same stores the API uses, so a change here is a change there.
Passwords are read from a prompt or `--password-env`, never from a command-line argument.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from typing import Any, Optional, Sequence

ACTOR = "cli"
VERSION = "5.0.0"


def _print(data: Any) -> None:
    print(json.dumps(data, indent=2, default=str))


def cmd_version(args: argparse.Namespace) -> int:
    print(f"orion-harness {VERSION}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ImportError:
        print("uvicorn is not installed. Install the API extra: pip install 'orion-harness[api]'", file=sys.stderr)
        return 2
    uvicorn.run("backend.api:app", host=args.host, port=args.port, reload=args.reload, log_level=args.log_level)
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    from harness.mcp_server import run_stdio

    run_stdio()
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    from harness.check import ping, snapshot

    report = snapshot()
    if args.ping:
        try:
            report["ping"] = ping()
        except Exception as exc:  # report the failure, do not crash the doctor
            report["ping"] = {"ok": False, "error": str(exc)[:300]}
    _print(report)
    return 0 if report["frozen_ok"] else 2


def cmd_settings(args: argparse.Namespace) -> int:
    from harness import settings_store as store

    try:
        if args.action == "list":
            rows = store.describe()
            width = max(len(r["key"]) for r in rows)
            for r in rows:
                print(f"{r['key']:<{width}}  {json.dumps(r['value']):<24} [{r['source']}]  {r['description']}")
        elif args.action == "get":
            print(json.dumps(store.effective(args.key)))
        elif args.action == "set":
            print(json.dumps(store.set_value(args.key, args.value, ACTOR)))
        elif args.action == "reset":
            store.reset(args.key, ACTOR)
            print(json.dumps(store.effective(args.key)))
    except store.SettingsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _password(args: argparse.Namespace) -> str:
    if args.password_env:
        value = os.environ.get(args.password_env, "")
        if not value:
            raise SystemExit(f"environment variable {args.password_env} is empty")
        return value
    first = getpass.getpass("Password: ")
    if first != getpass.getpass("Repeat password: "):
        raise SystemExit("passwords do not match")
    return first


def cmd_users(args: argparse.Namespace) -> int:
    from backend import database as db

    if args.action == "list":
        for user in db.get_all_users():
            state = "active" if user["is_active"] else "disabled"
            print(f"{user['username']:<24} {user['role']:<12} {user.get('tenant') or 'default':<12} {state}")
        return 0
    password = _password(args)
    problem = db.validate_password_strength(password)
    if problem:
        print(f"error: {problem}", file=sys.stderr)
        return 1
    if not db.create_user(args.name, password, args.role, args.tenant):
        print(f"error: could not create '{args.name}' (name taken, or invalid role)", file=sys.stderr)
        return 1
    print(f"created {args.name} ({args.role})")
    return 0


def cmd_connectors(args: argparse.Namespace) -> int:
    from harness.connectors import registry

    reg = registry()
    if args.action == "list":
        _print([c.describe() for c in reg.all()])
        return 0
    connector = reg.get(args.id)
    if connector is None:
        print(f"error: unknown connector '{args.id}'", file=sys.stderr)
        return 1
    report = connector.test()
    _print(report)
    return 0 if report["ok"] else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="orion", description="OrionHarness command line", allow_abbrev=False)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("version", help="print the version").set_defaults(func=cmd_version)

    serve = sub.add_parser("serve", help="run the HTTP API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")
    serve.add_argument("--log-level", default="info")
    serve.set_defaults(func=cmd_serve)

    sub.add_parser("mcp", help="run the MCP server over stdio").set_defaults(func=cmd_mcp)

    check = sub.add_parser("check", help="doctor: frozen data, models, optional LLM ping")
    check.add_argument("--ping", action="store_true")
    check.set_defaults(func=cmd_check)

    settings = sub.add_parser("settings", help="runtime settings")
    settings_sub = settings.add_subparsers(dest="action", required=True)
    settings_sub.add_parser("list")
    get = settings_sub.add_parser("get")
    get.add_argument("key")
    set_ = settings_sub.add_parser("set")
    set_.add_argument("key")
    set_.add_argument("value")
    reset = settings_sub.add_parser("reset")
    reset.add_argument("key")
    settings.set_defaults(func=cmd_settings)

    users = sub.add_parser("users", help="user accounts")
    users_sub = users.add_subparsers(dest="action", required=True)
    users_sub.add_parser("list")
    create = users_sub.add_parser("create", allow_abbrev=False)  # `--password` must not expand to `--password-env`
    create.add_argument("name")
    create.add_argument("--role", choices=["limited", "super_user"], default="limited")
    create.add_argument("--tenant", default="default")
    create.add_argument("--password-env", help="read the password from this environment variable instead of prompting")
    users.set_defaults(func=cmd_users)

    connectors = sub.add_parser("connectors", help="data connectors")
    connectors_sub = connectors.add_subparsers(dest="action", required=True)
    connectors_sub.add_parser("list")
    test = connectors_sub.add_parser("test")
    test.add_argument("id")
    connectors.set_defaults(func=cmd_connectors)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
