"""Cross-module dataclasses. No bare dicts across module boundaries."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


class SideEffect(str, Enum):
    READ = "read"
    WRITE = "write"
    EGRESS = "egress"


class AgentStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    DEPLOYED = "deployed"
    PAUSED = "paused"
    RETIRED = "retired"


class LatencyClass(str, Enum):
    FAST = "fast"
    SLOW = "slow"


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    HEALING = "healing"


class SpanKind(str, Enum):
    LLM = "llm"
    TOOL = "tool"
    AGENT = "agent"
    ROUTE = "route"
    HEAL = "heal"
    SANDBOX = "sandbox"
    EVAL = "eval"
    GRAPH = "graph"


@dataclass
class ToolManifest:
    """Registry row. Adding a tool is writing one of these, not a new LLM function."""

    id: str
    verb: str
    resource_type: str
    description: str
    schema: dict[str, Any]
    side_effect: SideEffect = SideEffect.READ
    timeout_ms: int = 30_000
    max_bytes: int = 512_000
    allowed_hosts: tuple[str, ...] = ()
    circuit_breaker: bool = True


@dataclass
class ToolCall:
    id: str
    verb: str
    arguments: dict[str, Any]


@dataclass
class ToolResult:
    ok: bool
    verb: str
    data: Any = None
    error_class: Optional[str] = None
    error: Optional[str] = None
    preview: str = ""
    result_hash: str = ""
    truncated: bool = False
    latency_ms: float = 0.0


@dataclass
class LLMResponse:
    text: str
    model: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: float = 0.0
    raw_finish_reason: str = ""
    packed_messages: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class AgentCard:
    """Plug-in contract. Router may only emit an id that exists here."""

    id: str
    name: str
    description: str
    intent: str
    tools: list[str]
    entities: list[str] = field(default_factory=list)
    collections: list[str] = field(default_factory=list)
    system_prompt: str = ""
    status: AgentStatus = AgentStatus.ACTIVE
    is_custom: bool = False
    is_builtin: bool = True
    version: int = 1
    timeout_ms: int = 60_000
    memory_mb: int = 512
    latency_class: LatencyClass = LatencyClass.FAST
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    created_by: Optional[str] = None
    created_at: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    module_path: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "intent": self.intent,
            "tools": self.tools,
            "entities": self.entities,
            "collections": self.collections,
            "system_prompt": self.system_prompt,
            "status": self.status.value,
            "is_custom": self.is_custom,
            "is_builtin": self.is_builtin,
            "version": self.version,
            "timeout_ms": self.timeout_ms,
            "memory_mb": self.memory_mb,
            "latency_class": self.latency_class.value,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "metadata": self.metadata,
            "module_path": self.module_path,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentCard":
        status_raw = data.get("status", "active")
        try:
            status = AgentStatus(status_raw)
        except ValueError:
            status = AgentStatus.DRAFT
        latency_raw = data.get("latency_class", "fast")
        try:
            latency = LatencyClass(latency_raw)
        except ValueError:
            latency = LatencyClass.FAST
        return cls(
            id=data["id"],
            name=data.get("name", data["id"]),
            description=data.get("description", ""),
            intent=data.get("intent") or data.get("description", ""),
            tools=list(data.get("tools") or []),
            entities=list(data.get("entities") or []),
            collections=list(data.get("collections") or []),
            system_prompt=data.get("system_prompt") or data.get("prompt_template") or "",
            status=status,
            is_custom=bool(data.get("is_custom", False)),
            is_builtin=bool(data.get("is_builtin", not data.get("is_custom", False))),
            version=int(data.get("version", 1)),
            timeout_ms=int(data.get("timeout_ms", 60_000)),
            memory_mb=int(data.get("memory_mb", 512)),
            latency_class=latency,
            input_schema=data.get("input_schema") or {},
            output_schema=data.get("output_schema") or {},
            created_by=data.get("created_by"),
            created_at=data.get("created_at"),
            metadata=data.get("metadata") or {},
            module_path=data.get("module_path"),
        )


@dataclass
class Task:
    id: str
    description: str
    agent: str
    status: TaskStatus = TaskStatus.PENDING
    result: str = ""
    heal_cycles: int = 0
    failure_class: Optional[str] = None


@dataclass
class Span:
    request_id: str
    span_id: str
    parent_id: Optional[str]
    kind: SpanKind
    name: str
    started_at: str
    ended_at: Optional[str] = None
    latency_ms: float = 0.0
    args_hash: str = ""
    result_hash: str = ""
    error_class: Optional[str] = None
    tokens_in: int = 0
    tokens_out: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "span_id": self.span_id,
            "parent_id": self.parent_id,
            "kind": self.kind.value,
            "name": self.name,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "latency_ms": self.latency_ms,
            "args_hash": self.args_hash,
            "result_hash": self.result_hash,
            "error_class": self.error_class,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "extra": self.extra,
        }


@dataclass
class Budget:
    max_tokens: int
    max_seconds: float
    max_heal_cycles: int
    tokens_used: int = 0
    started_at: float = 0.0

    def remaining_tokens(self) -> int:
        return max(0, self.max_tokens - self.tokens_used)


@dataclass
class WorkerResult:
    text: str
    ok: bool = True
    citations: list[str] = field(default_factory=list)
    tool_verbs: list[str] = field(default_factory=list)
    error_class: Optional[str] = None
    domain_mismatch: bool = False
    abstained: bool = False


@dataclass
class JudgeCard:
    overall: int = 0
    grounded: bool = False
    complete: bool = False
    safe: bool = True
    failure_class: str = "none"
    repair_hint: str = ""
    hallucinated_numbers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall": self.overall,
            "grounded": self.grounded,
            "complete": self.complete,
            "safe": self.safe,
            "failure_class": self.failure_class,
            "repair_hint": self.repair_hint,
            "hallucinated_numbers": self.hallucinated_numbers,
        }


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
