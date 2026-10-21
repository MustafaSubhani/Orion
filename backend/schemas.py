"""Request and response models. Every route uses these; no route returns a bare dict of unknown shape."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class AuthRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class SessionOut(BaseModel):
    token: str
    username: str
    role: str
    tenant: str = "default"


class IdentityOut(BaseModel):
    username: str
    role: str
    tenant: str = "default"


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20000)
    stream: bool = False
    allowed_agents: Optional[list[str]] = None
    pin_agent: Optional[str] = None
    thread_id: Optional[str] = None
    # Accepted for backward compatibility. The server always trusts the authenticated identity.
    user: Optional[str] = None
    role: Optional[str] = None


class TaskInfo(BaseModel):
    id: str = ""
    description: str = ""
    agent: str = ""
    status: str = ""
    result: Optional[str] = None
    failure_class: Optional[str] = None


class ChatResponse(BaseModel):
    response: str
    tasks: list[TaskInfo]
    agents_used: list[str]
    success: bool
    request_id: str = ""
    thread_id: Optional[str] = None


class AgentInfo(BaseModel):
    id: str
    name: str
    description: str
    status: str = "active"
    builtin: bool = True
    tools: list[str] = []
    entities: list[str] = []


class AgentVersionOut(BaseModel):
    version: int
    created_at: str
    created_by: str
    change_note: Optional[str] = None


class StatusChange(BaseModel):
    status: str = Field(pattern="^(active|paused|retired)$")


class RollbackRequest(BaseModel):
    version: int = Field(ge=1)


class HealthResponse(BaseModel):
    status: str
    message: str
    worker_provider: str = ""
    worker_model: str = ""
    router_model: str = ""
    judge_model: str = ""
    frozen_ok: bool = False
    guard: dict[str, Any] = {}
    mcp: dict[str, Any] = {}


class SettingValue(BaseModel):
    value: Any


class SettingOut(BaseModel):
    key: str
    value: Any
    source: str
    default: Any
    type: str
    description: str
    choices: list[str] = []
    minimum: Optional[float] = None
    maximum: Optional[float] = None


class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9_.@-]+$")
    password: str = Field(min_length=8, max_length=256)
    role: str = Field(default="limited", pattern="^(super_user|limited)$")
    tenant: Optional[str] = Field(default=None, pattern=r"^[a-z0-9][a-z0-9_-]{0,47}$")  # platform admins only


class UserRoleChange(BaseModel):
    role: str = Field(pattern="^(super_user|limited)$")


class UserOut(BaseModel):
    username: str
    role: str
    tenant: str = "default"
    is_active: bool = True
    created_at: Optional[str] = None


class LabMessage(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class LabAnswer(BaseModel):
    response: str
    username: str


class TraceSummary(BaseModel):
    request_id: str
    user: Optional[str] = None
    tenant: Optional[str] = None
    query: Optional[str] = None
    started_at: Optional[float] = None


class TraceOut(BaseModel):
    request_id: str
    n: int
    spans: list[dict[str, Any]]


class KBResource(BaseModel):
    id: str
    filename: Optional[str] = None
    kind: Optional[str] = None
    table: Optional[str] = None
    chunks: Optional[int] = None
    uploaded_by: Optional[str] = None
