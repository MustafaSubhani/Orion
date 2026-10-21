"""MCP server over HTTP (JSON-RPC 2.0). Tool calls run as the authenticated caller."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from backend.deps import Identity, current_identity
from harness.mcp_server import handle_mcp

router = APIRouter(tags=["MCP"])


@router.post("/mcp")
def mcp_rpc(body: dict[str, Any], identity: Identity = Depends(current_identity)) -> dict[str, Any]:
    return handle_mcp(body if isinstance(body, dict) else {}, user=identity.username, role=identity.harness_role)
