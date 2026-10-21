"""Online quality loop: judge a sample of live answers, queue the failures for a human.

A sampled fraction (`quality.sample_rate`, default 0) of live-chat answers is scored in the
background by the isolated judge (`ORION_JUDGE_*`, a different model family when configured). A weak
verdict goes into a repair queue. Nothing is patched automatically: an administrator reviews an item
and may ask the Repair Agent to patch the (Lab) agent, which still goes through `guarded_repair`
(contracts must not get worse). Built-in agents are never auto-patched.
"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
import uuid
from typing import Any, Callable, Optional

from harness import statedb, tenancy
from harness.settings_store import effective

log = logging.getLogger("orion.quality")

statedb.register_schema(
    """
    CREATE TABLE IF NOT EXISTS repair_queue (
        id TEXT PRIMARY KEY,
        tenant TEXT NOT NULL DEFAULT 'default',
        request_id TEXT NOT NULL,
        agent_id TEXT,
        query TEXT,
        answer TEXT,
        judge_json TEXT,
        reason TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at REAL NOT NULL,
        decided_by TEXT,
        decided_at REAL
    );
    CREATE INDEX IF NOT EXISTS idx_repair_queue_status ON repair_queue (tenant, status, created_at);
    """
)


def trace_digest(request_id: str, limit: int = 3000) -> str:
    """Compact, secret-free summary of a request's spans for the judge."""
    from harness.trace import get_tracer

    lines = []
    for span in get_tracer().spans_for(request_id):
        extra = span.extra or {}
        note = str(extra.get("preview") or extra.get("class") or "")[:160]
        status = f" ERR={span.error_class}" if span.error_class else ""
        lines.append(f"{span.kind.value} {span.name}{status} {note}".strip())
    return "\n".join(lines)[:limit]


def should_sample(rate: float, rng: Callable[[], float] = random.random) -> bool:
    return rate > 0 and rng() < min(rate, 1.0)


def judge_and_queue(
    *, tenant: str, request_id: str, query: str, answer: str, agents: list[str], judge: Optional[Callable[..., Any]] = None
) -> Optional[str]:
    """Judge one answer. Returns the queue id if it was flagged, else None. Never raises."""
    try:
        from harness.context import new_context
        from sandbox.eval_sandbox import judge_trace

        ctx = new_context(user="quality")
        ctx.extra.update({"mode": "product", "tenant": tenant})
        digest = trace_digest(request_id)
        card = (judge or judge_trace)(ctx, query=query, answer=answer, evidence=digest, spans_summary=digest)
        weak = card.overall < 2 or not card.grounded or not card.safe
        if not weak:
            return None
        return enqueue(
            tenant=tenant,
            request_id=request_id,
            agent_id=(agents or [""])[0],
            query=query,
            answer=answer,
            judge=card.to_dict(),
            reason=card.repair_hint or f"judge overall={card.overall}",
        )
    except Exception:  # quality sampling must never affect the user's request
        log.exception("quality judge failed for %s", request_id)
        return None


def maybe_sample(*, tenant: str, request_id: str, query: str, answer: str, agents: list[str]) -> bool:
    """Called after a live answer. Starts a background judge for a sampled fraction. Returns True if sampled."""
    if not should_sample(float(effective("quality.sample_rate", tenant))):
        return False
    threading.Thread(
        target=tenancy.bound(judge_and_queue, tenant),
        kwargs={"tenant": tenant, "request_id": request_id, "query": query, "answer": answer, "agents": agents},
        name="orion-quality-judge",
        daemon=True,
    ).start()
    return True


def enqueue(*, tenant: str, request_id: str, agent_id: str, query: str, answer: str, judge: dict[str, Any], reason: str) -> str:
    item_id = "rq_" + uuid.uuid4().hex[:12]
    conn = statedb.connect()
    try:
        conn.execute(
            "INSERT INTO repair_queue (id, tenant, request_id, agent_id, query, answer, judge_json, reason, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (item_id, tenant, request_id, agent_id, query[:1000], answer[:2000], json.dumps(judge), reason[:500], time.time()),
        )
        conn.commit()
    finally:
        conn.close()
    return item_id


def list_queue(*, tenant: str, status: Optional[str] = None, limit: int = 100) -> list[dict[str, Any]]:
    query = "SELECT * FROM repair_queue WHERE tenant=?"
    params: list[Any] = [tenant]
    if status:
        query += " AND status=?"
        params.append(status)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(max(1, min(limit, 500)))
    conn = statedb.connect()
    try:
        rows = conn.execute(query, params).fetchall()
    finally:
        conn.close()
    out = []
    for row in rows:
        item = dict(row)
        item["judge"] = json.loads(item.pop("judge_json") or "{}")
        out.append(item)
    return out


def get_item(item_id: str, tenant: str) -> Optional[dict[str, Any]]:
    return next((i for i in list_queue(tenant=tenant, limit=500) if i["id"] == item_id), None)


def decide(item_id: str, *, tenant: str, status: str, actor: str) -> bool:
    if status not in {"dismissed", "resolved"}:
        raise ValueError("status must be dismissed or resolved")
    conn = statedb.connect()
    try:
        cur = conn.execute(
            "UPDATE repair_queue SET status=?, decided_by=?, decided_at=? WHERE id=? AND tenant=? AND status='pending'",
            (status, actor, time.time(), item_id, tenant),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def apply_repair(item_id: str, *, tenant: str, actor: str, runner: Any = None, tools: Any = None, repair_fn: Any = None) -> dict[str, Any]:
    """Administrator-approved repair of a queued Lab agent, with the contract guard. Built-in agents are refused."""
    from harness.agent_registry import get_registry
    from harness.context import new_context
    from harness.types import JudgeCard
    from lab.evaluate import guarded_repair
    from repair.repair_agent import repair as run_repair

    item = get_item(item_id, tenant)
    if item is None or item["status"] != "pending":
        raise LookupError("unknown or already decided queue item")
    card = get_registry().get(item["agent_id"] or "")
    if card is not None and not tenancy.card_visible(card, tenant):
        raise LookupError("unknown or already decided queue item")  # another tenant's agent is never patched from here
    if card is None or not card.is_custom:
        raise PermissionError("only Lab agents are repaired; built-in agents are never patched automatically")
    judge = item["judge"]
    jc = JudgeCard(
        overall=int(judge.get("overall", 0)),
        grounded=bool(judge.get("grounded")),
        complete=bool(judge.get("complete")),
        safe=bool(judge.get("safe", True)),
        failure_class=str(judge.get("failure_class", "none")),
        repair_hint=str(judge.get("repair_hint", "")),
    )
    ctx = new_context(user=actor)
    ctx.extra.update({"mode": "product", "tenant": tenant})
    patch = repair_fn or (
        lambda: run_repair(ctx, card, failing_trace=trace_digest(item["request_id"]), judge=jc, oracle_diff=item["reason"], actor=actor)
    )
    outcome = guarded_repair(card.id, patch, runner=runner, tools=tools, actor=actor)
    decide(item_id, tenant=tenant, status="resolved" if outcome["accepted"] else "dismissed", actor=actor)
    return outcome
