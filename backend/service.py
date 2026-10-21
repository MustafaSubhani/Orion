"""Chat orchestration shared by /chat, /chat/stream and /v1/chat/completions.

Contract: the authenticated identity is the only source of user, role and tenant. Request bodies
can never widen access. Everything here runs in product mode (`kb_enabled=True`).
"""

from __future__ import annotations

import threading
from typing import Iterator, Optional

from backend.deps import Identity
from backend.errors import ApiError
from harness import guard, quality, summarizer, threads
from harness.agent_registry import get_registry
from harness.permissions import preprocess
from harness.planner import OrchestrationResult, coordinate_stream
from harness.settings_store import effective

ROUTABLE = {"active", "deployed"}
NEW_THREAD = "new"


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


def prepare_thread(identity: Identity, thread_id: Optional[str], message: str) -> tuple[Optional[str], str]:
    """Resolve the thread for this turn. Returns `(thread_id, history_text)`.

    `thread_id="new"` starts a thread titled after the message. Unknown or foreign threads are 404.
    With `memory.enabled` off the id is ignored and the turn is stateless.
    """
    if not thread_id or not effective("memory.enabled"):
        return None, ""
    if thread_id == NEW_THREAD:
        created = threads.create(tenant=identity.tenant, user=identity.username, title=message)
        return created["id"], ""
    thread = threads.get(thread_id, identity.tenant, identity.username)
    if thread is None:
        raise ApiError(404, "not_found", "Unknown thread")
    return thread["id"], threads.history_text(thread)


def record_turn(identity: Identity, thread_id: str, user_text: str, answer: str, request_id: str) -> None:
    """Persist one exchange, then fold old turns into the rolling summary in the background."""
    threads.append(thread_id, "user", user_text, request_id)
    threads.append(thread_id, "assistant", answer, request_id)
    threading.Thread(
        target=summarizer.fold_thread,
        args=(thread_id, identity.tenant, identity.username),
        name="orion-thread-fold",
        daemon=True,
    ).start()


def stream(
    identity: Identity,
    message: str,
    *,
    allowed_agents: Optional[list[str]] = None,
    pin_agent: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> Iterator[dict]:
    """Yield planner events for one live chat turn. The final event carries `thread_id` when one is used."""
    active_thread, history = prepare_thread(identity, thread_id, message)
    for update in coordinate_stream(
        message,
        allowed_agents or None,
        user=identity.username,
        user_role=identity.harness_role,
        pin_agent=pin_agent,
        kb_enabled=True,
        tenant=identity.tenant,
        history=history,
    ):
        if update.get("type") == "final" and active_thread:
            record_turn(identity, active_thread, message, str(update.get("response", "")), str(update.get("request_id", "")))
            update = {**update, "thread_id": active_thread}
            quality.maybe_sample(
                tenant=identity.tenant, request_id=str(update.get("request_id", "")), query=message,
                answer=str(update.get("response", "")), agents=list(update.get("agents_used") or []),
            )
        elif update.get("type") == "init" and active_thread:
            update = {**update, "thread_id": active_thread}
        yield update


def run(
    identity: Identity,
    message: str,
    *,
    allowed_agents: Optional[list[str]] = None,
    pin_agent: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> OrchestrationResult:
    result = OrchestrationResult()
    for update in stream(identity, message, allowed_agents=allowed_agents, pin_agent=pin_agent, thread_id=thread_id):
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
            result.thread_id = update.get("thread_id")
        elif kind == "error":
            result.final_response = update.get("message", "error")
            result.success = False
            result.request_id = update.get("request_id", "")
    return result
