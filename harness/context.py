"""Per-request context. Shared state is injected; workers never reach for globals."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

from harness.config import get_settings
from harness.types import Budget

if TYPE_CHECKING:
    from harness.agent_registry import AgentRegistry
    from harness.gateway import LLMGateway
    from harness.healing import Healer
    from harness.tool_registry import ToolRegistry
    from harness.trace import Tracer


@dataclass
class Runtime:
    """Process-level wiring. Built once at API startup."""

    gateway: "LLMGateway"
    tools: "ToolRegistry"
    agents: "AgentRegistry"
    tracer: "Tracer"
    healer: "Healer"


_RUNTIME: Optional[Runtime] = None


def set_runtime(runtime: Runtime) -> None:
    global _RUNTIME
    _RUNTIME = runtime


def get_runtime() -> Runtime:
    if _RUNTIME is None:
        from harness.runtime import build_runtime

        set_runtime(build_runtime())
    assert _RUNTIME is not None
    return _RUNTIME


@dataclass
class RequestContext:
    """Everything a worker is allowed to see for one request.

    Contract: the eval sandbox must be constructed with a context that has
    no API keys and no test gold. Workers never receive test labels.
    """

    request_id: str
    user: str = "system"
    role: Optional[str] = None
    allowed_agents: Optional[list[str]] = None
    pin_agent: Optional[str] = None
    parent_span_id: Optional[str] = None
    heal_cycles_left: int = 2
    extra: dict[str, Any] = field(default_factory=dict)
    budget: Budget = field(default_factory=lambda: Budget(80_000, 180.0, 2))
    session: Any = None
    graph: Any = None

    def __post_init__(self) -> None:
        if self.budget.started_at == 0.0:
            self.budget.started_at = time.time()
        if self.session is None:
            from harness.session import SessionStore

            self.session = SessionStore()

    @property
    def runtime(self) -> Runtime:
        return get_runtime()

    def charge_tokens(self, n: int) -> None:
        from harness.errors import BudgetExceededError

        self.budget.tokens_used += n
        if self.budget.tokens_used > self.budget.max_tokens:
            raise BudgetExceededError(
                f"Token budget exceeded ({self.budget.tokens_used}/{self.budget.max_tokens})"
            )

    def check_time(self) -> None:
        from harness.errors import BudgetExceededError

        elapsed = time.time() - self.budget.started_at
        if elapsed > self.budget.max_seconds:
            raise BudgetExceededError(
                f"Time budget exceeded ({elapsed:.1f}s/{self.budget.max_seconds}s)"
            )


def new_context(
    *,
    user: str = "system",
    role: Optional[str] = None,
    allowed_agents: Optional[list[str]] = None,
    pin_agent: Optional[str] = None,
    request_id: Optional[str] = None,
) -> RequestContext:
    from harness.trace import new_request_id

    settings = get_settings()
    rid = request_id or new_request_id()
    return RequestContext(
        request_id=rid,
        user=user,
        role=role,
        allowed_agents=allowed_agents,
        pin_agent=pin_agent,
        heal_cycles_left=settings.max_heal_cycles,
        budget=Budget(
            max_tokens=settings.max_tokens_per_request,
            max_seconds=settings.max_seconds_per_request,
            max_heal_cycles=settings.max_heal_cycles,
            started_at=time.time(),
        ),
    )
