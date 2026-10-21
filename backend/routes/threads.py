"""Conversation threads. Strictly private: even administrators do not read other users' chats."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from backend.deps import Identity, current_identity
from backend.errors import ApiError
from harness import threads
from harness.settings_store import effective

router = APIRouter(prefix="/threads", tags=["Threads"])


class ThreadCreate(BaseModel):
    title: str = Field(default="", max_length=200)


class ThreadRename(BaseModel):
    title: str = Field(min_length=1, max_length=200)


def _enabled() -> None:
    if not effective("memory.enabled"):
        raise ApiError(409, "memory_disabled", "Conversation memory is disabled by the administrator")


def _own(thread_id: str, identity: Identity) -> dict[str, Any]:
    thread = threads.get(thread_id, identity.tenant, identity.username)
    if thread is None:
        raise ApiError(404, "not_found", "Unknown thread")
    return thread


@router.get("")
def list_threads(
    limit: int = Query(default=50, ge=1, le=200), identity: Identity = Depends(current_identity)
) -> list[dict[str, Any]]:
    _enabled()
    return threads.list_for(tenant=identity.tenant, user=identity.username, limit=limit)


@router.post("", status_code=201)
def create_thread(body: ThreadCreate, identity: Identity = Depends(current_identity)) -> dict[str, Any]:
    _enabled()
    return threads.create(tenant=identity.tenant, user=identity.username, title=body.title)


@router.get("/{thread_id}/messages")
def thread_messages(
    thread_id: str,
    after_id: int = Query(default=0, ge=0),
    limit: Optional[int] = Query(default=None, ge=1, le=1000),
    identity: Identity = Depends(current_identity),
) -> dict[str, Any]:
    _enabled()
    thread = _own(thread_id, identity)
    return {
        "id": thread["id"],
        "title": thread["title"],
        "summary": thread["summary"],
        "messages": threads.messages(thread_id, after_id=after_id, limit=limit),
    }


@router.patch("/{thread_id}")
def rename_thread(thread_id: str, body: ThreadRename, identity: Identity = Depends(current_identity)) -> dict[str, Any]:
    _enabled()
    _own(thread_id, identity)
    threads.rename(thread_id, identity.tenant, identity.username, body.title)
    return _own(thread_id, identity)


@router.delete("/{thread_id}")
def delete_thread(thread_id: str, identity: Identity = Depends(current_identity)) -> dict[str, Any]:
    _enabled()
    if not threads.delete(thread_id, identity.tenant, identity.username):
        raise ApiError(404, "not_found", "Unknown thread")
    return {"ok": True, "id": thread_id}
