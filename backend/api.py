"""
OrionHarness FastAPI control plane.
"""

from __future__ import annotations

import json
from typing import List, Optional

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from harness.agent_registry import get_all_agents
from harness.auth import api_key_ok, identity_from
from harness.config import GRAPHS_DIR, TRACES_DIR, get_settings
from harness.mcp_server import handle_mcp
from harness.permissions import preprocess
from harness.planner import coordinate, coordinate_stream
from harness.runtime import build_runtime

settings = get_settings()
build_runtime()

app = FastAPI(
    title="OrionHarness API",
    description="Typed self-healing multi-agent harness over a heterogeneous enterprise lake",
    version="4.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

GRAPHS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/graphs", StaticFiles(directory=str(GRAPHS_DIR)), name="graphs")


class AuthRequest(BaseModel):
    username: str
    password: str


class LabRequest(BaseModel):
    message: str


class ChatRequest(BaseModel):
    message: str
    stream: bool = False
    allowed_agents: Optional[List[str]] = None
    pin_agent: Optional[str] = None
    user: str = "system"
    role: Optional[str] = None


class TaskInfo(BaseModel):
    id: str
    description: str
    agent: str
    status: str
    result: Optional[str] = None


class ChatResponse(BaseModel):
    response: str
    tasks: List[TaskInfo]
    agents_used: List[str]
    success: bool
    request_id: str = ""


class AgentInfo(BaseModel):
    id: str
    name: str
    description: str


class HealthResponse(BaseModel):
    status: str
    message: str
    worker_provider: str = ""
    worker_model: str = ""
    router_model: str = ""
    judge_model: str = ""
    frozen_ok: bool = False


@app.get("/", tags=["System"])
async def root():
    return {
        "message": "OrionHarness API",
        "version": "4.0.0",
        "docs": "/docs",
        "endpoints": ["/health", "/agents", "/chat", "/chat/stream", "/ingest", "/kb", "/mcp", "/traces/{request_id}"],
    }


def _require_api(authorization: str | None) -> None:
    if not api_key_ok(authorization):
        raise HTTPException(status_code=401, detail="Missing or invalid API key")


def _identity(authorization: str | None) -> dict | None:
    return identity_from(authorization)


def _harness_role(role: str | None) -> str | None:
    if role == "limited":
        return "limited"
    return None


@app.get("/health", response_model=HealthResponse, tags=["System"])
async def health_check():
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
    )


@app.post("/auth/login", tags=["Auth"])
async def auth_login(body: AuthRequest):
    from backend.database import create_session, get_user_role, verify_user

    username = (body.username or "").strip()
    if not username or not verify_user(username, body.password):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    role = get_user_role(username) or "limited"
    token = create_session(username, role)
    return {"token": token, "username": username, "role": role}


@app.post("/auth/signup", tags=["Auth"])
async def auth_signup(body: AuthRequest):
    from backend.database import ROLE_LIMITED, create_session, create_user, user_exists

    username = (body.username or "").strip()
    if len(username) < 2 or len(body.password or "") < 4:
        raise HTTPException(status_code=400, detail="Username or password is too short")
    if user_exists(username):
        raise HTTPException(status_code=409, detail="Username already exists")
    if not create_user(username, body.password, ROLE_LIMITED):
        raise HTTPException(status_code=409, detail="Username already exists")
    token = create_session(username, ROLE_LIMITED)
    return {"token": token, "username": username, "role": ROLE_LIMITED}


@app.get("/auth/me", tags=["Auth"])
async def auth_me(authorization: str | None = Header(default=None)):
    ident = _identity(authorization)
    if not ident:
        raise HTTPException(status_code=401, detail="Not signed in")
    return ident


@app.post("/auth/logout", tags=["Auth"])
async def auth_logout(authorization: str | None = Header(default=None)):
    from backend.database import delete_session
    from harness.auth import bearer_token

    delete_session(bearer_token(authorization))
    return {"ok": True}


@app.post("/lab", tags=["Lab"])
async def lab_chat(body: LabRequest, authorization: str | None = Header(default=None)):
    _require_api(authorization)
    ident = _identity(authorization) or {"username": "lab", "role": None}
    text = (body.message or "").strip()
    if text.lower().startswith("deploy") and ident.get("role") != "super_user":
        raise HTTPException(status_code=403, detail="Deploy requires an admin account")
    from lab.synthesizer import ask_lab

    reply = ask_lab(text, ident.get("username") or "lab")
    return {"response": reply, "username": ident.get("username")}


@app.get("/agents", response_model=list[AgentInfo], tags=["Agents"])
async def get_agents():
    return [
        AgentInfo(id=c.id, name=c.name, description=c.description)
        for c in get_all_agents()
        if c.status.value in {"active", "deployed", "draft"}
    ]


@app.post("/chat", response_model=ChatResponse, tags=["Chat"])
async def chat(request: ChatRequest, authorization: str | None = Header(default=None)):
    _require_api(authorization)
    pre = preprocess(request.message)
    if not pre.approved:
        raise HTTPException(status_code=400, detail=f"Request blocked: {pre.reason}")
    try:
        ident = _identity(authorization)
        user = (ident or {}).get("username") or request.user
        role = _harness_role((ident or {}).get("role") or request.role)
        result = coordinate(
            pre.clean_text,
            allowed_agents=request.allowed_agents,
            pin_agent=request.pin_agent,
            user=user,
            user_role=role,
            kb_enabled=True,
        )
        tasks = [
            TaskInfo(
                id=t.get("id", ""),
                description=t.get("description", ""),
                agent=t.get("agent", ""),
                status=t.get("status", ""),
                result=t.get("result", ""),
            )
            for t in result.tasks
        ]
        return ChatResponse(
            response=result.final_response,
            tasks=tasks,
            agents_used=result.agents_used,
            success=result.success,
            request_id=result.request_id,
        )
    except Exception as exc:
        return ChatResponse(
            response=f"Error processing request: {exc}",
            tasks=[],
            agents_used=[],
            success=False,
        )


@app.post("/chat/stream", tags=["Chat"])
async def chat_stream(request: ChatRequest, authorization: str | None = Header(default=None)):
    _require_api(authorization)
    pre = preprocess(request.message)
    if not pre.approved:
        payload = json.dumps({"type": "error", "message": f"Request blocked: {pre.reason}"})

        async def blocked():
            yield f"data: {payload}\n\n"

        return StreamingResponse(blocked(), media_type="text/event-stream")

    def generate():
        try:
            ident = _identity(authorization)
            user = (ident or {}).get("username") or request.user
            role = _harness_role((ident or {}).get("role") or request.role)
            for update in coordinate_stream(
                pre.clean_text,
                allowed_agents=request.allowed_agents,
                user=user,
                user_role=role,
                pin_agent=request.pin_agent,
                kb_enabled=True,
            ):
                yield f"data: {json.dumps(update, default=str)}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@app.post("/ingest", tags=["KB"])
async def ingest_files(
    files: list[UploadFile] = File(...),
    user: str = Form("chat"),
    authorization: str | None = Header(default=None),
):
    """Store uploads in kb/ only. Never writes data/frozen or bench/."""
    _require_api(authorization)
    from kb.ingest import ingest_bytes

    if not files:
        raise HTTPException(status_code=400, detail="no files")
    ingested = []
    for upload in files:
        data = await upload.read()
        try:
            record = ingest_bytes(upload.filename or "upload.bin", data, uploaded_by=user)
            ingested.append(record)
        except Exception as exc:
            ingested.append({"filename": upload.filename, "error": str(exc)})
    return {"ingested": ingested}


@app.get("/kb", tags=["KB"])
async def kb_catalog(authorization: str | None = Header(default=None)):
    _require_api(authorization)
    from kb.connectors import list_connectors
    from kb.store import kb_table_names, list_resources

    return {"resources": list_resources(), "tables": kb_table_names(), "connectors": list_connectors()}


@app.post("/mcp", tags=["MCP"])
async def mcp_rpc(body: dict, authorization: str | None = Header(default=None)):
    _require_api(authorization)
    return handle_mcp(body if isinstance(body, dict) else {})


@app.get("/traces/{request_id}", tags=["System"])
async def get_trace(request_id: str, authorization: str | None = Header(default=None)):
    _require_api(authorization)
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in request_id)
    path = TRACES_DIR / f"{safe}.jsonl"
    if not path.exists():
        raise HTTPException(status_code=404, detail="trace not found")
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return {"request_id": request_id, "n": len(lines), "spans": lines}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
