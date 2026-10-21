"""Chat orchestration shared by /chat, /chat/stream and /v1/chat/completions.

Contract: the authenticated identity is the only source of user, role and tenant. Request bodies
can never widen access. Everything here runs in product mode (`kb_enabled=True`).
"""

from __future__ import annotations

from typing import Iterator, Optional

from backend.deps import Identity
from backend.errors import ApiError
from harness import guard
from harness.agent_registry import get_registry
from harness.permissions import preprocess
from harness.planner import OrchestrationResult, coordinate_stream

ROUTABLE = {"active", "deployed"}


def parse_pin(message: str, requested: Optional[str], role: Optional[str]) -> tuple[str, Optional[str]]:
    """Resolve an `@agent_id` prefix (or an explicit pin) to a routable agent.

    Unknown or paused agents are rejected with 400 rather than silently ignored, so a user never
    believes they are talking to an agent that is not running.
    """
    text = (message or "").strip()
    pin = requested
    if text.startswith("@"):
        head, _, rest = text.partition(" ")
        pin = head[1:].strip()
        text = rest.strip() or text
    if not pin:
        return text, None
    card = get_registry().get(pin)
    if card is None:
        raise ApiError(400, "unknown_agent", f"Unknown agent '{pin}'")
    if card.status.value not in ROUTABLE:
        raise ApiError(400, "agent_not_active", f"Agent '{pin}' is {card.status.value}, not active")
    return text, pin


def check_input(message: str) -> str:
    """Regex gate, then the optional model guardrail. Returns the cleaned text or raises 400."""
    pre = preprocess(message)
    if not pre.approved:
        raise ApiError(400, "blocked", f"Request blocked: {pre.reason}")
    verdict = guard.check_input(pre.clean_text)
    if verdict.blocked:
        raise ApiError(400, "blocked_by_guard", f"Request blocked: {verdict.reason}")
    return pre.clean_text


def stream(
    identity: Identity,
    message: str,
    *,
    allowed_agents: Optional[list[str]] = None,
    pin_agent: Optional[str] = None,
) -> Iterator[dict]:
    """Yield planner events for one live chat turn."""
    yield from coordinate_stream(
        message,
        allowed_agents or None,
        user=identity.username,
        user_role=identity.harness_role,
        pin_agent=pin_agent,
        kb_enabled=True,
        tenant=identity.tenant,
    )


def run(
    identity: Identity,
    message: str,
    *,
    allowed_agents: Optional[list[str]] = None,
    pin_agent: Optional[str] = None,
) -> OrchestrationResult:
    result = OrchestrationResult()
    for update in stream(identity, message, allowed_agents=allowed_agents, pin_agent=pin_agent):
        kind = update.get("type")
        if kind == "final":
            result.final_response = update["response"]
            result.tasks = update["tasks"]
            result.agents_used = update["agents_used"]
            result.success = update["success"]
            result.request_id = update["request_id"]
            result.graph = update.get("graph") or {}
            result.backtracks = int(update.get("backtracks") or 0)
            result.tokens_used = int(update.get("tokens_used") or 0)
        elif kind == "error":
            result.final_response = update.get("message", "error")
            result.success = False
            result.request_id = update.get("request_id", "")
    return result
