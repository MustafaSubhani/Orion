"""Chat: one JSON response, or an SSE stream of planner events."""

from __future__ import annotations

import json
import logging
from typing import Iterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from backend import service
from backend.deps import Identity, current_identity
from backend.schemas import ChatRequest, ChatResponse, TaskInfo

router = APIRouter(tags=["Chat"])
log = logging.getLogger("orion.chat")


def sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, default=str)}\n\n"


def _prepare(req: ChatRequest, identity: Identity) -> tuple[str, str | None]:
    text = service.check_input(req.message, identity.tenant)
    return service.parse_pin(text, req.pin_agent, None, identity.tenant)


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, identity: Identity = Depends(current_identity)) -> ChatResponse:
    text, pin = _prepare(req, identity)
    result = service.run(identity, text, allowed_agents=req.allowed_agents, pin_agent=pin, thread_id=req.thread_id)
    tasks = [
        TaskInfo(
            id=str(t.get("id", "")),
            description=str(t.get("description", "")),
            agent=str(t.get("agent", "")),
            status=str(t.get("status", "")),
            result=t.get("result"),
            failure_class=t.get("failure_class"),
        )
        for t in result.tasks
    ]
    return ChatResponse(
        response=result.final_response,
        tasks=tasks,
        agents_used=result.agents_used,
        success=result.success,
        request_id=result.request_id,
        thread_id=result.thread_id,
    )


@router.post("/chat/stream")
def chat_stream(req: ChatRequest, identity: Identity = Depends(current_identity)) -> StreamingResponse:
    text, pin = _prepare(req, identity)

    def generate() -> Iterator[str]:
        try:
            for update in service.stream(
                identity, text, allowed_agents=req.allowed_agents, pin_agent=pin, thread_id=req.thread_id
            ):
                yield sse(update)
        except Exception:  # stream boundary: report a safe error event instead of a broken connection
            log.exception("chat stream failed")
            yield sse({"type": "error", "message": "The request failed. Please try again."})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
