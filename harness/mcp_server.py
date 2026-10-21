"""Model Context Protocol server for OrionHarness (JSON-RPC 2.0 over stdio or HTTP POST).

Implements protocol revision 2025-06-18 and negotiates down to 2025-03-26 and 2024-11-05.
Tools, resources (frozen tables, connector tables, live KB) and prompts (agent cards) are exposed.

Contract:
- Tool calls run as the authenticated caller: role limits, column masking, entity joins, the guard
  and the write-approval policy all apply exactly as they do in chat.
- `tools/call` returns a standard CallToolResult (`content`, `isError`, `structuredContent`). The
  legacy `ok` and `error_class` fields are kept alongside for existing clients.
- Notifications (no `id`) get no response. Unknown tools are JSON-RPC errors (-32602).
"""

from __future__ import annotations

import json
import sys
from typing import Any, Optional

from harness.context import get_runtime, new_context
from harness.errors import HarnessError, UnknownToolError
from harness.permissions import assert_tool_allowed, strip_salary_if_limited
from harness.types import SideEffect
from retrieval.tables import TABLE_ENTITY, list_tables, load_table

LATEST_PROTOCOL = "2025-06-18"
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_VERSION = "5.0.0"


def negotiate(requested: Optional[str]) -> str:
    """Use the client's revision if we support it, else our latest."""
    return requested if requested in SUPPORTED_PROTOCOLS else LATEST_PROTOCOL


def _visible_tools(runtime, role: Optional[str]) -> list:
    from harness import web_search

    out = []
    for verb in runtime.tools.verbs():
        manifest = runtime.tools.manifest(verb)
        if manifest is None:
            continue
        try:
            assert_tool_allowed(role, verb)
        except HarnessError:
            continue
        if verb == "web_search":
            probe = new_context(role=role)
            probe.extra["mode"] = "product"
            if not web_search.is_enabled(probe):
                continue
        out.append(manifest)
    return out


def handle_mcp(
    body: dict[str, Any], *, user: str = "mcp", role: str | None = None, tenant: str = "default"
) -> Optional[dict[str, Any]]:
    """Handle one JSON-RPC message. Returns None for notifications (callers answer 202 / print nothing)."""
    req_id = body.get("id")
    method = str(body.get("method") or "")
    params = body.get("params") or {}
    is_notification = "id" not in body
    if body.get("jsonrpc") != "2.0" or not method:
        return None if is_notification else _error(req_id, -32600, "Invalid Request")
    if is_notification:
        return None  # initialized, cancelled, progress ... nothing to answer

    runtime = get_runtime()

    if method == "ping":
        return _ok(req_id, {})

    if method == "initialize":
        return _ok(
            req_id,
            {
                "protocolVersion": negotiate(params.get("protocolVersion")),
                "serverInfo": {"name": "orion-harness", "title": "OrionHarness", "version": SERVER_VERSION},
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"subscribe": False, "listChanged": False},
                    "prompts": {"listChanged": False},
                },
                "instructions": "Enterprise data tools with entity-aware join policy. Writes need approval.",
            },
        )

    if method in {"tools/list", "list_tools"}:
        tools = []
        for manifest in _visible_tools(runtime, role):
            tools.append(
                {
                    "name": manifest.verb,
                    "title": manifest.verb.replace("_", " "),
                    "description": manifest.description,
                    "inputSchema": manifest.schema,
                    "annotations": {"readOnlyHint": manifest.side_effect == SideEffect.READ},
                }
            )
        return _ok(req_id, {"tools": tools})

    if method in {"tools/call", "call_tool"}:
        return _call_tool(runtime, req_id, params, user, role, tenant)

    if method in {"resources/list", "list_resources"}:
        return _ok(req_id, {"resources": _resources(role)})

    if method in {"resources/read", "read_resource"}:
        return _read_resource(req_id, str(params.get("uri") or ""), role)

    if method in {"prompts/list", "list_prompts"}:
        prompts = [
            {
                "name": card.id,
                "description": card.description,
                "arguments": [{"name": "task", "description": "Specific query or instruction", "required": True}],
            }
            for card in runtime.agents.all()
            if card.status.value in {"active", "deployed"}
        ]
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
                    {"role": "user", "content": {"type": "text", "text": card.system_prompt}},
                    {"role": "user", "content": {"type": "text", "text": task_arg or "Help me with enterprise data."}},
                ],
            },
        )

    return _error(req_id, -32601, f"Method not found: {method}")


def _call_tool(runtime, req_id: Any, params: dict[str, Any], user: str, role: Optional[str], tenant: str) -> dict[str, Any]:
    name = str(params.get("name") or params.get("verb") or "")
    arguments = params.get("arguments") or params.get("input") or {}
    if not isinstance(arguments, dict):
        return _error(req_id, -32602, "arguments must be an object")
    ctx = new_context(user=user, role=role)
    ctx.extra.update({"kb_enabled": True, "mode": "product", "tenant": tenant})
    try:
        result = runtime.tools.call(name, arguments, ctx)
    except UnknownToolError:
        return _error(req_id, -32602, f"Unknown tool: {name}")
    except HarnessError as exc:
        return _ok(req_id, _tool_result(text=exc.safe_message, is_error=True, error_class=exc.error_class.value))
    text = result.preview or result.error or ""
    structured: dict[str, Any] = {"ok": result.ok, "verb": result.verb, "result_hash": result.result_hash}
    if result.error_class:
        structured["error_class"] = result.error_class
    if result.ok and isinstance(result.data, dict):
        for key in ("rows", "columns", "records", "entity", "table"):
            if key in result.data:
                structured[key] = result.data[key]
    return _ok(req_id, _tool_result(text=text, is_error=not result.ok, error_class=result.error_class, structured=structured))


def _tool_result(*, text: str, is_error: bool, error_class: Optional[str], structured: Optional[dict] = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "content": [{"type": "text", "text": text}],
        "isError": is_error,
        "structuredContent": structured if structured is not None else {"ok": not is_error, "error_class": error_class},
        # Legacy fields from the first MCP surface; clients that ignore unknown keys are unaffected.
        "ok": not is_error,
    }
    if error_class:
        out["error_class"] = error_class
    return out


def _resources(role: Optional[str]) -> list[dict[str, Any]]:
    resources = []
    for table in list_tables(include_kb=True):
        entity = TABLE_ENTITY.get(table, "KB" if table.startswith("kb_") else "General")
        resources.append(
            {
                "uri": f"table://{table}",
                "name": f"Table: {table} ({entity})",
                "description": f"Table '{table}' for entity '{entity}'",
                "mimeType": "application/json",
            }
        )
    try:
        from harness.connectors import registry as connectors

        for ref in connectors().table_refs():
            connector, _, _table = ref.partition(".")
            resources.append(
                {
                    "uri": f"table://{ref}",
                    "name": f"Connector table: {ref}",
                    "description": f"Read-only table from connector '{connector}'",
                    "mimeType": "application/json",
                }
            )
    except Exception:  # a broken connector must not hide the built-in tables
        pass
    return resources


def _read_resource(req_id: Any, uri: str, role: Optional[str]) -> dict[str, Any]:
    if not uri.startswith("table://"):
        return _error(req_id, -32602, f"Unknown resource uri: {uri}")
    name = uri.removeprefix("table://")
    try:
        if "." in name:
            from harness.connectors import registry as connectors

            connector, table = connectors().resolve(name)
            payload = connector.query({"table": table, "limit": 10}, role=role)
            content = {"table": name, "rows": payload["rows"], "columns": payload["columns"], "sample": payload["records"]}
        else:
            df = load_table(name)
            columns = strip_salary_if_limited(list(df.columns), role)
            df = df[columns]
            content = {"table": name, "rows": len(df), "columns": columns, "sample": df.head(10).to_dict(orient="records")}
    except HarnessError as exc:
        return _error(req_id, -32602, exc.safe_message)
    except Exception as exc:
        return _error(req_id, -32000, f"Cannot load table resource '{name}' ({type(exc).__name__})")
    return _ok(
        req_id,
        {"contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(content, default=str, indent=2)}]},
    )


def _ok(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def run_stdio() -> None:
    """Stdio transport for MCP hosts (Claude Desktop, Cursor). Runs as the local administrator."""
    sys.stderr.write("OrionHarness MCP server listening on stdio...\n")
    sys.stderr.flush()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            resp = handle_mcp(json.loads(line))
        except json.JSONDecodeError:
            resp = _error(None, -32700, "Parse error")
        except Exception as exc:  # keep the transport alive; report the failure to the client
            resp = _error(None, -32603, f"Internal error ({type(exc).__name__})")
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    run_stdio()
