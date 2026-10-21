"""OpenAI-compatible chat completions, so generic chat UIs (OpenUI `AgentInterface`) can talk to Orion.

Contract: `choices[0].delta.content` carries only answer text. Progress events travel in a sibling
`orion` field with an empty delta, so a client that ignores unknown fields still renders cleanly.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Iterator, Optional, Union

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict

from backend import service
from backend.deps import Identity, current_identity
from backend.errors import ApiError

router = APIRouter(prefix="/v1", tags=["OpenAI compatible"])
log = logging.getLogger("orion.openai")
MODEL_ID = "orion"


class OAIMessage(BaseModel):
    model_config = ConfigDict(extra="allow")
    role: str
    content: Union[str, list[Any], None] = None


class OAIChatRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: str = MODEL_ID
    messages: list[OAIMessage]
    stream: bool = False


def message_text(content: Union[str, list[Any], None]) -> str:
    """Flatten OpenAI content (a string or a list of typed parts) to plain text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for part in content:
        if isinstance(part, dict) and part.get("type") in {"text", "input_text"}:
            parts.append(str(part.get("text", "")))
        elif isinstance(part, str):
            parts.append(part)
    return "\n".join(p for p in parts if p)


def last_user_text(messages: list[OAIMessage]) -> str:
    for msg in reversed(messages):
        if msg.role == "user":
            text = message_text(msg.content).strip()
            if text:
                return text
    raise ApiError(400, "no_user_message", "messages must include a non-empty user message")


def _chunk(cid: str, created: int, delta: dict, finish: Optional[str] = None, orion: Optional[dict] = None) -> dict:
    body: dict[str, Any] = {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": created,
        "model": MODEL_ID,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    if orion is not None:
        body["orion"] = orion
    return body


def pieces(text: str, size: int = 48) -> Iterator[str]:
    """Split on whitespace boundaries so streamed text never breaks a word."""
    buf = ""
    for word in text.split(" "):
        candidate = (buf + " " + word) if buf else word
        if len(candidate) > size and buf:
            yield buf + " "
            buf = word
        else:
            buf = candidate
    if buf:
        yield buf


def _line(payload: dict) -> str:
    return f"data: {json.dumps(payload, default=str)}\n\n"


@router.get("/models")
def models(_: Identity = Depends(current_identity)) -> dict:
    return {"object": "list", "data": [{"id": MODEL_ID, "object": "model", "owned_by": "orion"}]}


@router.post("/chat/completions")
def chat_completions(req: OAIChatRequest, identity: Identity = Depends(current_identity)):
    query = service.check_input(last_user_text(req.messages), identity.tenant)
    query, pin = service.parse_pin(query, None, None, identity.tenant)
    thread_id = (req.model_extra or {}).get("thread_id")
    cid = "chatcmpl-" + uuid.uuid4().hex[:24]
    created = int(time.time())

    if not req.stream:
        result = service.run(identity, query, pin_agent=pin, thread_id=thread_id)
        return JSONResponse(
            {
                "id": cid,
                "object": "chat.completion",
                "created": created,
                "model": MODEL_ID,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": result.final_response},
                        "finish_reason": "stop" if result.success else "error",
                    }
                ],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": result.tokens_used},
                "orion": {
                    "request_id": result.request_id,
                    "thread_id": result.thread_id,
                    "agents": result.agents_used,
                    "success": result.success,
                    "tasks": result.tasks,
                },
            }
        )

    def generate() -> Iterator[str]:
        yield _line(_chunk(cid, created, {"role": "assistant"}))
        try:
            for update in service.stream(identity, query, pin_agent=pin, thread_id=thread_id):
                kind = update.get("type")
                if kind == "error":
                    yield _line(_chunk(cid, created, {"content": str(update.get("message", "Request failed"))}))
                    yield _line(_chunk(cid, created, {}, "error", {"request_id": update.get("request_id", "")}))
                    break
                if kind == "final":
                    for piece in pieces(str(update.get("response", ""))):
                        yield _line(_chunk(cid, created, {"content": piece}))
                    meta = {
                        "request_id": update.get("request_id", ""),
                        "thread_id": update.get("thread_id"),
                        "agents": update.get("agents_used", []),
                        "success": update.get("success", True),
                        "tasks": update.get("tasks", []),
                        "tokens_used": update.get("tokens_used", 0),
                    }
                    yield _line(_chunk(cid, created, {}, "stop", meta))
                    break
                event = {k: v for k, v in update.items() if k not in {"graph", "tasks", "response"}}
                yield _line(_chunk(cid, created, {}, None, event))
        except Exception:  # stream boundary: finish the stream cleanly instead of dropping the connection
            log.exception("openai-compatible stream failed")
            yield _line(_chunk(cid, created, {"content": "The request failed. Please try again."}))
            yield _line(_chunk(cid, created, {}, "error"))
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
