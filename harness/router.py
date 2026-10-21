"""Two-stage router: embed against AgentCards, then a small model picks only among those ids."""

from __future__ import annotations

import json
import re
import threading

from harness.context import RequestContext
from harness.errors import RoutingError
from harness.trace import get_tracer
from harness.types import AgentCard, AgentStatus, SpanKind


def lexical_score(query: str, card: AgentCard) -> float:
    q = set(re.findall(r"[a-z0-9]+", query.lower()))
    blob = set(re.findall(r"[a-z0-9]+", f"{card.intent} {card.description} {card.id}".lower()))
    if not q:
        return 0.0
    return len(q & blob) / max(1, len(q))


_VECTORS: dict[str, tuple[str, list[float]]] = {}
_VECTOR_LOCK = threading.Lock()


def invalidate_vector(agent_id: str, _event: str = "") -> None:
    """Drop a card's cached embedding. Registered as a registry listener, so edits take effect at once."""
    with _VECTOR_LOCK:
        _VECTORS.pop(agent_id, None)


def _card_vector(card: AgentCard, embed) -> list[float]:
    """Embedding of the card's intent, cached until the intent changes or the registry reports a change."""
    with _VECTOR_LOCK:
        hit = _VECTORS.get(card.id)
        if hit and hit[0] == card.intent:
            return hit[1]
    vector = embed(card.intent)
    with _VECTOR_LOCK:
        _VECTORS[card.id] = (card.intent, vector)
    return vector


def shortlist(ctx: RequestContext, query: str, k: int = 3) -> list[AgentCard]:
    agents = ctx.runtime.agents
    if hasattr(agents, "add_listener") and not getattr(agents, "_router_hooked", False):
        agents.add_listener(invalidate_vector)
        agents._router_hooked = True  # an attribute, not id(): ids are reused after garbage collection
    pool = [c for c in agents.active() if c.id != "orchestrator"]
    if ctx.allowed_agents:
        allowed = set(ctx.allowed_agents)
        pool = [c for c in pool if c.id in allowed]
        missing = [aid for aid in ctx.allowed_agents if agents.get(aid) is None]
        if missing:
            raise RoutingError(f"allowed_agents contains unknown ids: {missing}")
    if ctx.pin_agent:
        card = agents.get(ctx.pin_agent)
        if card is None:
            raise RoutingError(f"pinned agent '{ctx.pin_agent}' is not in the registry")
        if card.status not in {AgentStatus.ACTIVE, AgentStatus.DEPLOYED, AgentStatus.DRAFT}:
            raise RoutingError(f"pinned agent '{ctx.pin_agent}' is {card.status.value}")
        return [card]
    if not pool:
        raise RoutingError("no active agents available")
    if len(pool) == 1:
        return pool

    scored: list[tuple[float, AgentCard]] = []
    try:
        from retrieval.embeddings import get_embedding

        q_vec = get_embedding(query)
        for card in pool:
            c_vec = _card_vector(card, get_embedding)
            score = _cosine(q_vec, c_vec)
            scored.append((score, card))
    except Exception:
        scored = [(lexical_score(query, card), card) for card in pool]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [card for _, card in scored[:k]]


def route(ctx: RequestContext, query: str) -> list[str]:
    """Return one or more registered agent ids. Never invents ids. Never defaults to finance."""
    tracer = get_tracer()
    with tracer.span(ctx.request_id, SpanKind.ROUTE, "router.route", parent_id=ctx.parent_span_id, args={"q": query[:200]}):
        cards = shortlist(ctx, query, k=ctx.runtime.gateway.settings.router_topk if hasattr(ctx.runtime, "gateway") else 3)
        ids = [c.id for c in cards]
        if len(cards) == 1:
            return ids[:1]
        catalog = "\n".join(f"- {c.id}: {c.intent}" for c in cards)
        prompt = f"""Pick which agent(s) should handle this query.
You may ONLY use ids from this list:
{catalog}

Return JSON: {{"agent_ids": ["id"], "reasoning": "..."}}
Use multiple ids only if the query truly needs independent specialists.
Query: {query}
"""
        try:
            resp = ctx.runtime.gateway.complete(
                ctx=ctx,
                messages=[{"role": "user", "content": prompt}],
                purpose="router",
                force_json=True,
                name="router.pick",
                max_tokens=300,
            )
            picked = _parse_ids(resp.text, legal=set(ids))
            if picked:
                return picked
        except Exception:
            pass
        # fallback: highest similarity card only
        return [cards[0].id]


def _parse_ids(text: str, legal: set[str]) -> list[str]:
    blob = text.strip()
    match = re.search(r"\{.*\}", blob, re.S)
    if not match:
        return []
    try:
        obj = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    raw = obj.get("agent_ids") or obj.get("agents") or []
    if isinstance(raw, str):
        raw = [raw]
    out = []
    for item in raw:
        if item in legal and item not in out:
            out.append(item)
    return out


def _cosine(a: list[float], b: list[float]) -> float:
    import math

    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
