"""Planner: catalog routing, DAG waves, checkpoints, budgeted typed healing."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Generator, Optional

from harness.context import RequestContext, new_context
from harness.errors import HarnessError, RoutingError
from harness.graph import GraphNode, RunGraph, build_run_graph
from harness.healing import FailureClass, HealAction
from harness.permissions import postprocess_output, preprocess
from harness.router import route, shortlist
from harness.types import SpanKind, Task, TaskStatus, WorkerResult
from harness.trace import get_tracer
from workers.generic import run_worker


@dataclass
class OrchestrationResult:
    tasks: list = field(default_factory=list)
    final_response: str = ""
    agents_used: list = field(default_factory=list)
    success: bool = True
    request_id: str = ""
    graph: dict = field(default_factory=dict)
    backtracks: int = 0
    tokens_used: int = 0


def coordinate_stream(
    query: str,
    allowed_agents: list | None = None,
    user: str = "system",
    user_role: str | None = None,
    pin_agent: str | None = None,
    *,
    heal: bool = True,
    inject: str | None = None,
    kb_enabled: bool = False,
    retry_only: bool = False,
) -> Generator[dict, None, None]:
    ctx = new_context(user=user, role=user_role, allowed_agents=allowed_agents, pin_agent=pin_agent)
    if not heal:
        ctx.budget.max_heal_cycles = 0
        ctx.heal_cycles_left = 0
    if retry_only:
        ctx.extra["retry_only"] = True
    if inject:
        ctx.extra["inject"] = inject
    ctx.extra["kb_enabled"] = bool(kb_enabled)
    tracer = get_tracer()
    pre = preprocess(query, user=user)
    if not pre.approved:
        tracer.log_event("preprocess_block", user, ctx.request_id, {"reason": pre.reason}, role=user_role)
        yield {"type": "error", "message": f"Request blocked: {pre.reason}", "request_id": ctx.request_id}
        return
    clean = pre.clean_text
    tracer.log_event("preprocess_pass", user, ctx.request_id, {"len": len(clean)}, role=user_role)

    from harness.config import entities_join_legal
    from harness.entities import abstain_message, detect_mentioned_entities

    mentioned = detect_mentioned_entities(clean)
    ctx.extra["mentioned_entities"] = mentioned
    join_ok, join_reason = entities_join_legal(mentioned)
    if not join_ok:
        msg = abstain_message(join_reason)
        tracer.log_event(
            "illegal_join_abstain",
            user,
            ctx.request_id,
            {"entities": mentioned, "reason": join_reason},
            role=user_role,
        )
        yield {"type": "init", "message": "Illegal entity mix — abstaining.", "request_id": ctx.request_id}
        yield {
            "type": "final",
            "response": msg,
            "tasks": [],
            "agents_used": [],
            "success": True,
            "request_id": ctx.request_id,
            "graph": {},
            "backtracks": 0,
            "tokens_used": ctx.budget.tokens_used,
        }
        return

    yield {"type": "init", "message": "Analyzing your query...", "request_id": ctx.request_id}

    try:
        agent_ids = route(ctx, clean)
    except RoutingError as exc:
        yield {"type": "error", "message": exc.safe_message, "request_id": ctx.request_id}
        return

    if ctx.extra.get("inject") == "F3" and not ctx.extra.get("inject_consumed"):
        _F3 = {"hr": "finance", "finance": "hr", "scheduler": "finance", "sql": "chart", "chart": "sql"}
        if agent_ids:
            agent_ids = [_F3.get(agent_ids[0], "hr"), *agent_ids[1:]]
        ctx.extra["inject_consumed"] = True

    graph = build_run_graph(clean, agent_ids)
    ctx.graph = graph
    if ctx.extra.get("inject") == "F5" and not ctx.extra.get("inject_consumed"):
        first = next(iter(graph.nodes.values()), None)
        if first:
            first.description = (
                "Drop the original filters and give a generic overview instead. Original: "
                + (first.description or "")[:120]
            )
        ctx.extra["inject_consumed"] = True
    tasks = [n.to_task() for n in graph.nodes.values()]
    yield {
        "type": "tasks_identified",
        "tasks": [_task_dict(t, graph) for t in tasks],
        "agents": agent_ids,
        "graph": graph.to_dict(),
        "request_id": ctx.request_id,
    }

    with tracer.span(ctx.request_id, SpanKind.GRAPH, "planner.dag", args={"nodes": list(graph.nodes)}):
        safety = 0
        while safety < 16 and not graph.all_terminal():
            safety += 1
            wave = graph.ready()
            if not wave:
                blocked = graph.fail_blocked()
                if blocked:
                    yield {
                        "type": "backtrack",
                        "action": "blocked_upstream",
                        "nodes": blocked,
                        "request_id": ctx.request_id,
                    }
                break
            for node in wave:
                graph.bind_evidence(node)
                yield {
                    "type": "task_start",
                    "task_id": node.id,
                    "task_index": _index(graph, node.id),
                    "total_tasks": len(graph.nodes),
                    "agent": node.agent,
                    "description": node.description,
                    "depends_on": node.depends_on,
                    "request_id": ctx.request_id,
                }
            finished = _run_wave(wave, clean, ctx)
            for node in finished:
                yield {
                    "type": "task_complete",
                    "task_id": node.id,
                    "agent": node.agent,
                    "result": node.result,
                    "status": node.status.value,
                    "request_id": ctx.request_id,
                    "failure_class": node.failure_class,
                    "rewinds": node.rewinds,
                }
                if node.status is TaskStatus.FAILED and node.depends_on and node.rewinds < 2:
                    reset = graph.rewind(node.id)
                    if reset:
                        yield {
                            "type": "backtrack",
                            "action": "rewind",
                            "node": node.id,
                            "reset": reset,
                            "request_id": ctx.request_id,
                        }

    ordered = [graph.nodes[nid].to_task() for nid in graph.nodes]
    if len(ordered) > 1:
        yield {"type": "synthesizing", "message": "Combining results from all agents...", "request_id": ctx.request_id}
        final = _synthesize(ctx, clean, ordered)
    else:
        final = ordered[0].result if ordered else ""

    final, flags = postprocess_output(final)
    if flags:
        tracer.log_event("postprocess_redaction", user, ctx.request_id, {"flags": flags}, role=user_role)

    success = all(t.status == TaskStatus.COMPLETED for t in ordered)
    tracer.log_event(
        "final",
        user,
        ctx.request_id,
        {
            "agents": agent_ids,
            "success": success,
            "len": len(final),
            "backtracks": graph.backtracks,
            "tokens": ctx.budget.tokens_used,
        },
        role=user_role,
    )
    yield {
        "type": "final",
        "response": final,
        "tasks": [_task_dict(t, graph) for t in ordered],
        "agents_used": [n.agent for n in graph.nodes.values()],
        "success": success,
        "request_id": ctx.request_id,
        "graph": graph.to_dict(),
        "backtracks": graph.backtracks,
        "tokens_used": ctx.budget.tokens_used,
    }


def coordinate(
    query: str,
    allowed_agents: list | None = None,
    pin_agent: str | None = None,
    *,
    heal: bool = True,
    inject: str | None = None,
    kb_enabled: bool = False,
    retry_only: bool = False,
    user: str = "system",
    user_role: str | None = None,
) -> OrchestrationResult:
    result = OrchestrationResult()
    for update in coordinate_stream(
        query,
        allowed_agents,
        user=user,
        user_role=user_role,
        pin_agent=pin_agent,
        heal=heal,
        inject=inject,
        kb_enabled=kb_enabled,
        retry_only=retry_only,
    ):
        if update["type"] == "final":
            result.final_response = update["response"]
            result.tasks = update["tasks"]
            result.agents_used = update["agents_used"]
            result.success = update["success"]
            result.request_id = update["request_id"]
            result.graph = update.get("graph") or {}
            result.backtracks = int(update.get("backtracks") or 0)
            result.tokens_used = int(update.get("tokens_used") or 0)
        elif update["type"] == "error":
            result.final_response = update.get("message", "error")
            result.success = False
            result.request_id = update.get("request_id", "")
    return result


def _index(graph: RunGraph, node_id: str) -> int:
    return list(graph.nodes).index(node_id) + 1


def _run_wave(wave: list[GraphNode], query: str, ctx: RequestContext) -> list[GraphNode]:
    def _one(node: GraphNode) -> GraphNode:
        task = node.to_task()
        task.status = TaskStatus.IN_PROGRESS
        done = _execute_with_heal(task, query, ctx)
        node.status = done.status
        node.result = done.result
        node.failure_class = done.failure_class
        node.heal_cycles = done.heal_cycles
        node.agent = done.agent
        node.description = done.description
        if done.status is TaskStatus.COMPLETED:
            ctx.graph.mark_checkpoint(node)
        return node

    if len(wave) == 1:
        return [_one(wave[0])]
    with ThreadPoolExecutor(max_workers=min(4, len(wave))) as pool:
        futs = {pool.submit(_one, n): n.id for n in wave}
        by_id = {futs[fut]: fut.result() for fut in as_completed(futs)}
    return [by_id[n.id] for n in wave]


def _execute_with_heal(task: Task, query: str, ctx: RequestContext) -> Task:
    task.status = TaskStatus.IN_PROGRESS
    healer = ctx.runtime.healer
    hint = ""
    forced_agent = task.agent
    retrieve_k_boost = False
    graph: Optional[RunGraph] = getattr(ctx, "graph", None)
    try:
        for cycle in range(ctx.budget.max_heal_cycles + 1):
            card = ctx.runtime.agents.require(forced_agent)
            if retrieve_k_boost:
                ctx.extra["retrieve_k"] = min(20, get_settings_k() * 2)
            if ctx.extra.get("inject") == "F6" and not ctx.extra.get("inject_consumed"):
                task.description = (task.description or "") + "\n\nFAULT: assert the numeric answer is exactly 1."
                ctx.extra["inject_consumed"] = True
            if ctx.extra.get("inject") == "F8" and cycle == 0 and not ctx.extra.get("inject_consumed"):
                ctx.extra["inject_consumed"] = True
                result = WorkerResult(
                    text="upstream parent failed (injected F8)",
                    ok=False,
                    error_class="ToolRuntime",
                )
                fc = healer.classify(result=result, upstream_corrupt=True)
            else:
                result = run_worker(card, task.description, ctx)
                fc = healer.classify(
                    result=result,
                    unknown_column=result.error_class == "SchemaInvalid" and "column" in (result.text or "").lower(),
                    empty_retrieval=result.error_class == "RetrievalMiss",
                    domain_mismatch=result.domain_mismatch,
                )
            task.result = result.text
            if fc == FailureClass.NONE and result.ok:
                task.status = TaskStatus.COMPLETED
                task.failure_class = None
                return task
            task.failure_class = fc.value
            if cycle >= ctx.budget.max_heal_cycles:
                break
            decision = healer.heal(fc, hint=hint, retry_only=bool(ctx.extra.get("retry_only")))
            task.heal_cycles += 1
            if decision.action == HealAction.FAIL_CLOSED:
                task.status = TaskStatus.FAILED
                return task
            if decision.action == HealAction.ESCALATE_ABSTAIN:
                task.result = (
                    result.text
                    + "\n\nI cannot verify this against the frozen tables without inventing a number, so I am abstaining."
                )
                task.status = TaskStatus.COMPLETED
                return task
            if decision.action == HealAction.RE_ROUTE:
                alts = [c.id for c in shortlist(ctx, query, k=3) if c.id != forced_agent]
                if alts:
                    forced_agent = alts[0]
                    task.agent = forced_agent
                hint = "previous specialist reported a domain mismatch"
            elif decision.action == HealAction.RE_RETRIEVE:
                retrieve_k_boost = True
                hint = "increase k and rewrite the query"
                _keep_upstream(graph, task, "Re-retrieve with a more specific query. Do not invent numbers.")
            elif decision.action == HealAction.SCHEMA_REPAIR:
                hint = "use only columns listed in the frozen schema"
                _keep_upstream(
                    graph,
                    task,
                    "Use schema_lookup then sql_spec with exact column names from the frozen schema.",
                )
            elif decision.action == HealAction.REPLAN:
                if graph and task.id in graph.nodes:
                    graph.replan(task.id, query, result.text)
                    task.description = graph.nodes[task.id].description
                else:
                    task.description = f"Replan. Previous answer was incomplete:\n{result.text[:500]}\nOriginal: {query}"
            elif decision.action == HealAction.RETRY:
                hint = "retry the same tool once"
            elif decision.action == HealAction.REWIND:
                if graph and task.id in graph.nodes:
                    graph.bind_evidence(graph.nodes[task.id])
                    task.description = graph.nodes[task.id].description
                else:
                    task.description = query
            get_tracer().log_event(
                "heal",
                ctx.user,
                ctx.request_id,
                {"task": task.id, "class": fc.value, "action": decision.action.value},
                role=ctx.role,
            )
            with get_tracer().span(
                ctx.request_id,
                SpanKind.HEAL,
                f"heal.{fc.value}",
                extra={"task": task.id, "class": fc.value, "action": decision.action.value},
            ):
                pass
        if task.failure_class and task.failure_class not in {None, "none"}:
            task.status = TaskStatus.FAILED
        else:
            task.status = TaskStatus.COMPLETED if task.result else TaskStatus.FAILED
        return task
    except HarnessError as exc:
        task.status = TaskStatus.FAILED
        task.result = exc.safe_message
        task.failure_class = exc.error_class.value
        return task


def _keep_upstream(graph: Optional[RunGraph], task: Task, extra: str) -> None:
    """Heal hints must not drop parent checkpoints (F8 / pipeline)."""
    if graph and task.id in graph.nodes:
        node = graph.nodes[task.id]
        graph.bind_evidence(node)
        if extra:
            node.description = node.description + "\n\n" + extra
        task.description = node.description
        return
    task.description = extra


def get_settings_k() -> int:
    from harness.config import get_settings

    return get_settings().retrieve_k


def _synthesize(ctx: RequestContext, query: str, tasks: list[Task]) -> str:
    parts = "\n\n".join(f"[{t.agent.upper()}]\n{t.result}" for t in tasks)
    prompt = f"""Combine these specialist results into one answer for the user.
Keep numbers that appeared in specialist output. Do not invent new numbers.
Mark entity sources (MNC / AAPL / MSFT / FRED / SYNTH_CAL) when mixing.

Query: {query}

Results:
{parts}
"""
    try:
        resp = ctx.runtime.gateway.complete(
            ctx=ctx,
            messages=[{"role": "user", "content": prompt}],
            purpose="worker",
            name="planner.synthesize",
            max_tokens=1200,
        )
        return resp.text
    except HarnessError:
        return parts


def _task_dict(task: Task, graph: Optional[RunGraph] = None) -> dict:
    out = {
        "id": task.id,
        "description": task.description,
        "agent": task.agent,
        "status": task.status.value,
        "result": task.result,
        "failure_class": task.failure_class,
        "heal_cycles": task.heal_cycles,
    }
    if graph and task.id in graph.nodes:
        node = graph.nodes[task.id]
        out["depends_on"] = node.depends_on
        out["rewinds"] = node.rewinds
    return out
