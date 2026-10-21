"""MCP server over HTTP (streamable HTTP, JSON responses). Tool calls run as the authenticated caller."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, Response

from backend.deps import Identity, current_identity
from harness.mcp_server import handle_mcp

router = APIRouter(tags=["MCP"])


@router.post("/mcp")
def mcp_rpc(body: dict[str, Any], identity: Identity = Depends(current_identity)):
    """One JSON-RPC message. Notifications get `202 Accepted` with no body, as the transport requires."""
    reply = handle_mcp(
        body if isinstance(body, dict) else {},
        user=identity.username,
        role=identity.harness_role,
        tenant=identity.tenant,
    )
    if reply is None:
        return Response(status_code=202)
    return JSONResponse(reply)
