"""Model Context Protocol (MCP) server for OrionHarness.

Exposes tools, resources (frozen tables, live KB), and agent prompts
via JSON-RPC 2.0 (stdio transport or HTTP).
"""

from __future__ import annotations

import json
import sys
from typing import Any

from harness.context import get_runtime, new_context
from retrieval.tables import list_tables, load_table, TABLE_ENTITY


def handle_mcp(body: dict[str, Any], *, user: str = "mcp", role: str | None = None) -> dict[str, Any]:
    req_id = body.get("id")
    method = str(body.get("method") or "")
    params = body.get("params") or {}
    runtime = get_runtime()

    if method in {"initialize", "notifications/initialized"}:
        return _ok(
            req_id,
            {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "orion-harness", "version": "4.1.0"},
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"subscribe": False, "listChanged": False},
                    "prompts": {"listChanged": False},
                },
            },
        )

    # 1. Tools
    if method in {"tools/list", "list_tools"}:
        tools = []
        for verb in runtime.tools.verbs():
            manifest = runtime.tools.manifest(verb)
            if manifest is None:
                continue
            tools.append(
                {
                    "name": manifest.verb,
                    "description": manifest.description,
                    "inputSchema": manifest.schema,
                }
            )
        return _ok(req_id, {"tools": tools})

    if method in {"tools/call", "call_tool"}:
        name = str(params.get("name") or params.get("verb") or "")
        arguments = params.get("arguments") or params.get("input") or {}
        ctx = new_context(user=user, role=role)
        ctx.extra["kb_enabled"] = True
        result = runtime.tools.call(name, arguments if isinstance(arguments, dict) else {}, ctx)
        return _ok(
            req_id,
            {
                "ok": result.ok,
                "content": [{"type": "text", "text": result.preview or result.error or ""}],
                "isError": not result.ok,
                "error_class": result.error_class,
            },
        )

    # 2. Resources
    if method in {"resources/list", "list_resources"}:
        resources = []
        for table in list_tables(include_kb=True):
            entity = TABLE_ENTITY.get(table, "KB" if table.startswith("kb_") else "General")
            resources.append(
                {
                    "uri": f"table://{table}",
                    "name": f"Table: {table} ({entity})",
                    "description": f"Frozen enterprise table '{table}' for entity '{entity}'",
                    "mimeType": "application/json",
                }
            )
        return _ok(req_id, {"resources": resources})

    if method in {"resources/read", "read_resource"}:
        uri = str(params.get("uri") or "")
        if uri.startswith("table://"):
            table_name = uri.removeprefix("table://")
            try:
                df = load_table(table_name)
                head = df.head(10).to_dict(orient="records")
                content = json.dumps(
                    {
                        "table": table_name,
                        "rows": len(df),
                        "columns": list(df.columns),
                        "sample": head,
                    },
                    default=str,
                    indent=2,
                )
                return _ok(
                    req_id,
                    {
                        "contents": [
                            {
                                "uri": uri,
                                "mimeType": "application/json",
                                "text": content,
                            }
                        ]
                    },
                )
            except Exception as exc:
                return _error(req_id, -32000, f"Cannot load table resource '{table_name}': {exc}")
        return _error(req_id, -32602, f"Unknown resource uri: {uri}")

    # 3. Prompts
    if method in {"prompts/list", "list_prompts"}:
        prompts = []
        for card in runtime.agents.all():
            prompts.append(
                {
                    "name": card.id,
                    "description": card.description,
                    "arguments": [{"name": "task", "description": "Specific query or instruction", "required": True}],
                }
            )
        return _ok(req_id, {"prompts": prompts})

    if method in {"prompts/get", "get_prompt"}:
        prompt_name = str(params.get("name") or "")
        task_arg = str((params.get("arguments") or {}).get("task") or "")
        card = runtime.agents.get(prompt_name)
        if card is None:
            return _error(req_id, -32602, f"Unknown prompt: {prompt_name}")
        return _ok(
            req_id,
            {
                "description": card.description,
                "messages": [
                    {"role": "system", "content": {"type": "text", "text": card.system_prompt}},
                    {"role": "user", "content": {"type": "text", "text": task_arg or "Help me with enterprise data."}},
                ],
            },
        )

    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"unknown method {method}"}}


def _ok(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def run_stdio() -> None:
    """Stdio transport runner for MCP clients (Claude Desktop, Cursor, etc)."""
    sys.stderr.write("OrionHarness MCP Server listening on stdio...\n")
    sys.stderr.flush()
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            resp = handle_mcp(req)
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        except Exception as exc:
            sys.stderr.write(f"Error handling MCP line: {exc}\n")
            sys.stderr.flush()


if __name__ == "__main__":
    run_stdio()
