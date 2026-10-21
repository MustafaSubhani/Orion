"""Health, root and metrics."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse

from backend.deps import Identity, require_admin
from backend.schemas import HealthResponse
from harness import metrics

router = APIRouter()


@router.get("/", tags=["System"])
def root() -> dict:
    return {
        "message": "OrionHarness API",
        "version": "5.0.0",
        "docs": "/docs",
        "endpoints": [
            "/health", "/auth/login", "/agents", "/chat", "/chat/stream", "/v1/chat/completions",
            "/ingest", "/kb", "/traces", "/lab", "/admin/settings", "/mcp", "/metrics",
        ],
    }


def _guard_status() -> dict:
    try:
        from harness.guard import status

        return status()
    except ImportError:
        return {"available": False}


def _mcp_status() -> dict:
    try:
        from harness.mcp_client import bridge_status

        return bridge_status()
    except ImportError:
        return {"available": False}


@router.get("/health", response_model=HealthResponse, tags=["System"])
def health() -> HealthResponse:
    """Public liveness and configuration summary. Contains no secrets and no user data."""
    from harness.check import snapshot

    snap = snapshot()
    return HealthResponse(
        status="healthy" if snap["frozen_ok"] else "degraded",
        message="OrionHarness is running",
        worker_provider=str(snap["worker_provider"]),
        worker_model=str(snap["models"]["worker"]),
        router_model=str(snap["models"]["router"]),
        judge_model=str(snap["models"]["judge"]),
        frozen_ok=bool(snap["frozen_ok"]),
        guard=_guard_status(),
        mcp=_mcp_status(),
    )


@router.get("/metrics", response_class=PlainTextResponse, tags=["System"])
def prometheus_metrics(_: Identity = Depends(require_admin)) -> str:
    return metrics.render()
