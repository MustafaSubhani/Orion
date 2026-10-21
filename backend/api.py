"""OrionHarness FastAPI control plane.

`app` is built by `create_app()`. Nothing touches the database or the runtime at import time:
startup work happens in the lifespan hook, so importing this module is cheap and side-effect free.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend import errors
from backend.routes import admin, agents, approvals, auth, chat, kb, lab, mcp, openai_compat, system, threads, traces
from harness import metrics
from harness.config import GRAPHS_DIR, get_settings

log = logging.getLogger("orion.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    from backend import database
    from harness.runtime import build_runtime

    database.get_connection().close()  # creates the schema and the admin account, fails fast on bad config
    database.purge_expired_sessions()
    runtime = build_runtime()
    from harness import mcp_client

    mcp_client.start_in_background(runtime.tools)
    log.info("OrionHarness API ready")
    yield
    mcp_client.bridge().close_all()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="OrionHarness API",
        description="Typed self-healing multi-agent harness over a heterogeneous enterprise lake",
        version="5.0.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )
    errors.install(app)

    @app.middleware("http")
    async def _metrics(request: Request, call_next):
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            route = request.scope.get("route")
            path = getattr(route, "path", "unmatched")
            metrics.inc("orion_http_requests_total", {"route": path, "method": request.method, "status": status})
            metrics.observe("orion_http_request_seconds", time.perf_counter() - started, {"route": path})

    GRAPHS_DIR.mkdir(parents=True, exist_ok=True)
    # Chart files carry random names, so the URL itself is the capability (an <img> cannot send a bearer).
    app.mount("/graphs", StaticFiles(directory=str(GRAPHS_DIR)), name="graphs")

    for module in (system, auth, chat, openai_compat, agents, approvals, threads, kb, traces, admin, lab, mcp):
        app.include_router(module.router)
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
