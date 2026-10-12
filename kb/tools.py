"""Live KB tools. Disabled during bench (kb_enabled=False)."""

from __future__ import annotations

from typing import Any

from harness.context import RequestContext
from harness.errors import PolicyDeniedError, RetrievalMissError, SchemaInvalidError
from harness.types import ToolManifest
from kb.ingest import search_kb
from kb.store import kb_table_names, list_resources


def register_kb_tools(registry: Any) -> None:
    from harness.tool_registry import _object, _str

    registry.register(
        ToolManifest(
            id="kb_list",
            verb="kb_list",
            resource_type="kb",
            description="List live KB uploads (chat attachments). Not the paper lake / data/frozen.",
            schema=_object({}),
        ),
        _handle_kb_list,
    )
    registry.register(
        ToolManifest(
            id="kb_search",
            verb="kb_search",
            resource_type="kb",
            description="Search live KB chunks (user uploads). Never searches data/frozen or bench gold.",
            schema=_object({"query": _str("search text"), "k": {"type": "integer"}}, required=["query"]),
        ),
        _handle_kb_search,
    )
    registry.register(
        ToolManifest(
            id="kb_query",
            verb="kb_query",
            resource_type="kb",
            description="sql_spec over kb_* tables from live uploads only. Do not use sql_spec on kb_*.",
            schema=_object(
                {
                    "table": _str("must start with kb_"),
                    "filters": {"type": "array"},
                    "columns": {"type": "array"},
                    "group_by": {"type": "array"},
                    "agg": {"type": "object"},
                    "limit": {"type": "integer"},
                    "column": _str("optional; used by schema inject"),
                },
                required=["table"],
            ),
        ),
        _handle_kb_query,
    )


def _require_kb(ctx: RequestContext) -> None:
    if not (ctx.extra or {}).get("kb_enabled"):
        raise PolicyDeniedError("live KB is disabled for this run (eval lake only)")


def _handle_kb_list(args: dict[str, Any], ctx: RequestContext) -> dict:
    _require_kb(ctx)
    resources = list_resources()
    tables = kb_table_names()
    preview = "\n".join(
        f"- {r.get('filename')} id={r.get('id')} kind={r.get('kind')} table={r.get('table') or '-'} chunks={r.get('chunks')}"
        for r in resources
    ) or "(empty live KB)"
    return {
        "resources": resources,
        "tables": tables,
        "preview": preview,
    }


def _handle_kb_search(args: dict[str, Any], ctx: RequestContext) -> dict:
    _require_kb(ctx)
    query = str(args.get("query") or "")
    k = int(args.get("k") or 8)
    chunks = search_kb(query, k=k)
    if not chunks:
        raise RetrievalMissError("no live KB chunks matched this query")
    preview = "\n\n".join(
        f"[{c.get('chunk_id')} file={((c.get('metadata') or {}).get('filename') or '?')}]\n{str(c.get('text') or '')[:400]}"
        for c in chunks
    )
    return {"k": len(chunks), "chunks": chunks, "empty": False, "preview": preview}


def _handle_kb_query(args: dict[str, Any], ctx: RequestContext) -> dict:
    _require_kb(ctx)
    from sandbox.exec_sandbox import execute_sql_spec

    table = str(args.get("table") or "")
    if not table.startswith("kb_"):
        raise SchemaInvalidError("kb_query only accepts kb_* tables from the live catalog")
    return execute_sql_spec(args, role=ctx.role, allow_kb=True)
