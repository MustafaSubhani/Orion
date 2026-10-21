"""Flight-recorder access. Users see their own requests; admins see their tenant's."""

from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from backend.deps import Identity, current_identity, in_scope
from backend.errors import ApiError
from backend.schemas import TraceOut, TraceSummary
from harness.config import TRACES_DIR
from harness.trace import get_tracer

router = APIRouter(tags=["Traces"])


def _safe(request_id: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in request_id)


def _authorise(request_id: str, identity: Identity) -> dict[str, Any]:
    """Return trace metadata if the caller may read it. Unknown and foreign traces both give 404."""
    meta = get_tracer().read_meta(request_id)
    if meta is None:
        # A trace with no metadata has no known owner, so only the platform administrator may read it.
        if identity.is_platform_admin and (TRACES_DIR / f"{_safe(request_id)}.jsonl").exists():
            return {}
        raise ApiError(404, "not_found", "Trace not found")
    if not in_scope(identity, str(meta.get("tenant") or "default")):
        raise ApiError(404, "not_found", "Trace not found")
    if meta.get("user") != identity.username and not identity.is_admin:
        raise ApiError(404, "not_found", "Trace not found")
    return meta


def _spans(request_id: str) -> list[dict[str, Any]]:
    path = TRACES_DIR / f"{_safe(request_id)}.jsonl"
    if not path.exists():
        raise ApiError(404, "not_found", "Trace not found")
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


@router.get("/traces", response_model=list[TraceSummary])
def list_traces(
    limit: int = Query(default=50, ge=1, le=200),
    user: Optional[str] = None,
    identity: Identity = Depends(current_identity),
) -> list[TraceSummary]:
    tracer = get_tracer()
    if identity.is_admin:
        rows = tracer.list_requests(user=user, tenant=identity.tenant, limit=limit)
    else:
        rows = tracer.list_requests(user=identity.username, tenant=identity.tenant, limit=limit)
    return [TraceSummary(**{k: r.get(k) for k in TraceSummary.model_fields}) for r in rows]


@router.get("/traces/{request_id}", response_model=TraceOut)
def get_trace(request_id: str, identity: Identity = Depends(current_identity)) -> TraceOut:
    _authorise(request_id, identity)
    spans = _spans(request_id)
    return TraceOut(request_id=request_id, n=len(spans), spans=spans)


@router.get("/traces/{request_id}/export")
def export_trace(request_id: str, identity: Identity = Depends(current_identity)) -> Response:
    _authorise(request_id, identity)
    body = "\n".join(json.dumps(s, ensure_ascii=False) for s in _spans(request_id)) + "\n"
    return Response(
        content=body,
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="{_safe(request_id)}.jsonl"'},
    )


@router.get("/requests/{request_id}/graph")
def request_graph(request_id: str, identity: Identity = Depends(current_identity)) -> dict[str, Any]:
    """The execution DAG with node status plus every heal event, for the workflow visualiser."""
    meta = _authorise(request_id, identity)
    spans = _spans(request_id)
    heals = [
        {"name": s.get("name"), "latency_ms": s.get("latency_ms"), **(s.get("extra") or {})}
        for s in spans
        if s.get("kind") == "heal"
    ]
    return {
        "request_id": request_id,
        "graph": meta.get("graph") or {},
        "tasks": meta.get("tasks") or [],
        "success": meta.get("success"),
        "heal_events": heals,
    }
