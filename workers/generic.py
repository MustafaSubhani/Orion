"""ReAct worker. Domain agents are cards + prompts; they share this loop."""

from __future__ import annotations

import json
from pathlib import Path

from harness.context import RequestContext, is_product
from harness.errors import HarnessError, SandboxError
from harness.packs import registry as pack_registry
from harness.tool_registry import rebind_verb
from harness.types import AgentCard, SpanKind, WorkerResult
from harness.trace import get_tracer


def run_worker(card: AgentCard, task: str, ctx: RequestContext) -> WorkerResult:
    if card.module_path and Path(card.module_path).exists() and card.is_custom:
        return _run_generated(card, task, ctx)
    return _react(card, task, ctx)


def _run_generated(card: AgentCard, task: str, ctx: RequestContext) -> WorkerResult:
    from sandbox.exec_sandbox import run_generated_agent

    tracer = get_tracer()
    with tracer.span(ctx.request_id, SpanKind.AGENT, f"agent.{card.id}", parent_id=ctx.parent_span_id):
        try:
            text = run_generated_agent(Path(card.module_path), task, timeout_s=card.timeout_ms / 1000)
            return WorkerResult(text=text or "(empty)", ok=True)
        except SandboxError as exc:
            return WorkerResult(text=exc.safe_message, ok=False, error_class=exc.error_class.value)


def _react(card: AgentCard, task: str, ctx: RequestContext) -> WorkerResult:
    from harness.context_engine import estimate_tokens, stub_text
    from harness.skills import default_store, render_for_prompt, self_eval_note

    gw = ctx.runtime.gateway
    tools = ctx.runtime.tools
    settings = gw.settings
    ctx.extra["worker_agent"] = card.id
    tool_names = list(card.tools)
    for extra in ("session_get", "search_tools", "skill_manage", "schema_lookup", "join_check", "evidence_check"):
        if extra not in tool_names:
            tool_names.append(extra)
    if ctx.extra.get("kb_enabled"):
        for extra in ("kb_list", "kb_search", "kb_query"):
            if extra not in tool_names:
                tool_names.append(extra)
    schemas = tools.openai_tools(tool_names)
    schema = pack_registry().schema_text(include_kb=bool(ctx.extra.get("kb_enabled")))
    if estimate_tokens(schema) > 900:
        schema = schema[:2400] + "\n…schema truncated; unknown columns come back as SchemaInvalid with the real list."
    if is_product(ctx):
        from harness.connectors import registry as connector_registry

        connected = connector_registry().prompt_text(ctx.role)
        if connected:
            schema = schema + "\n--- connected data sources (read-only; address as connector.table) ---\n" + connected
    lake_label = "Frozen tables + live KB (uploads are not eval gold):\n" if ctx.extra.get("kb_enabled") else "Frozen tables:\n"
    system = card.system_prompt + "\n\n" + lake_label + schema
    if ctx.extra.get("kb_enabled"):
        system += (
            "\nLive KB tools: kb_list, kb_search, kb_query on kb_* only. "
            "sql_spec cannot query kb_* (use kb_query). Never mix uploads into paper-lake joins."
        )
    if gw.settings.worker_provider == "openai_compat":
        system += (
            "\nIf the server does not emit OpenAI tool_calls, reply with ONLY "
            '{"name":"<verb>","arguments":{...}} and nothing else.'
        )
    if card.entities:
        system += f"\nYour entities: {', '.join(card.entities)}. Do not silently join others."
    system += (
        "\nLarge tool results are compacted to artifact ids. "
        "Call session_get to expand one artifact. Do not ask to dump the whole vault."
    )
    store = default_store()
    loaded = store.select(task, agent_id=card.id, entities=card.entities, k=settings.skill_topk)
    if loaded:
        system += (
            "\n\nSaved procedures from earlier recoveries. Follow them when they match; they are not gold labels.\n"
            + render_for_prompt(loaded)
        )
    system += (
        "\nIf a tool fails then a corrected call works, skill_manage op=create so later sessions skip that mistake. "
        "Never save policy denials or guessed numbers."
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": task},
    ]
    citations: list[str] = []
    verbs: list[str] = []
    last_empty_retrieval = False
    unknown_column = False
    tracer = get_tracer()
    preview_chars = settings.tool_preview_chars
    eval_every = max(2, settings.eval_every)
    last_fail: dict[str, str] = {}
    fail_call_ids: dict[str, str] = {}
    rebinds: list[str] = []
    product_mode = is_product(ctx)
    ctx.extra["allowed_verbs"] = list(tool_names)
    recoveries = 0
    last_error = ""
    n_calls = 0
    with tracer.span(ctx.request_id, SpanKind.AGENT, f"agent.{card.id}", parent_id=ctx.parent_span_id) as span:
        ctx.parent_span_id = span.span_id
        try:
            for _ in range(settings.max_tool_rounds):
                ctx.check_time()
                resp = gw.complete(
                    ctx=ctx,
                    messages=messages,
                    tools=schemas or None,
                    purpose="worker",
                    name=f"worker.{card.id}",
                )
                if resp.packed_messages:
                    messages = [dict(m) for m in resp.packed_messages]
                if not resp.tool_calls:
                    text = resp.text.strip() or "I could not produce an answer."
                    mismatch = _domain_mismatch(card, task, text)
                    return WorkerResult(
                        text=text,
                        ok=True,
                        citations=citations,
                        tool_verbs=verbs,
                        domain_mismatch=mismatch,
                        abstained=_looks_like_abstain(text),
                        error_class="RetrievalMiss" if last_empty_retrieval and not citations else None,
                    )
                messages.append(
                    {
                        "role": "assistant",
                        "content": resp.text or "",
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.verb,
                                    "arguments": json.dumps(tc.arguments),
                                },
                            }
                            for tc in resp.tool_calls
                        ],
                    }
                )
                for tc in resp.tool_calls:
                    if tc.verb not in tool_names and product_mode:
                        matched = rebind_verb(tc.verb, tool_names)
                        if matched:
                            rebinds.append(f"{tc.verb}->{matched}")
                            tc.verb = matched
                    if tc.verb not in tool_names:
                        result_text = f"PolicyDenied: agent '{card.id}' cannot call '{tc.verb}'"
                        messages.append({"role": "tool", "tool_call_id": tc.id, "content": result_text})
                        continue
                    result = tools.call(tc.verb, tc.arguments, ctx)
                    verbs.append(tc.verb)
                    n_calls += 1
                    if not result.ok and result.error_class == "SchemaInvalid":
                        unknown_column = True
                    if not result.ok and result.error_class == "RetrievalMiss":
                        last_empty_retrieval = True
                    if tc.verb == "retrieve":
                        data = result.data or {}
                        if data.get("empty"):
                            last_empty_retrieval = True
                        for chunk in data.get("chunks") or []:
                            citations.append(str(chunk.get("chunk_id")))
                    if result.ok:
                        raw = result.preview
                    elif product_mode:
                        raw = _typed_error_line(result.error_class, result.error)
                    else:
                        raw = f"{result.error_class}: {result.error}"
                    if not result.ok:
                        last_fail[tc.verb] = raw
                        last_error = raw
                        fail_call_ids[tc.verb] = tc.id
                    elif tc.verb in last_fail:
                        if product_mode:
                            _collapse_failure(messages, fail_call_ids.pop(tc.verb, ""), tc.verb)
                        store.remember_recovery(
                            verb=tc.verb,
                            error=last_fail.pop(tc.verb),
                            fix_preview=raw,
                            agent_id=card.id,
                            entities=list(card.entities),
                            failure_class="F4" if unknown_column else "F1",
                        )
                        recoveries += 1
                    if tc.verb != "session_get" and len(raw) > preview_chars:
                        art = ctx.session.put(raw, kind=tc.verb)
                        content = stub_text(raw, art.id, preview_chars)
                    else:
                        content = raw
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": content})
                    if n_calls > 0 and n_calls % eval_every == 0:
                        messages.append(
                            {
                                "role": "user",
                                "content": self_eval_note(
                                    n_calls=n_calls,
                                    verbs=verbs,
                                    last_error=last_error,
                                    recoveries=recoveries,
                                ),
                            }
                        )
            resp = gw.complete(
                ctx=ctx,
                messages=messages + [{"role": "user", "content": "Stop calling tools. Give the final answer now."}],
                purpose="worker",
                name=f"worker.{card.id}.final",
            )
            return WorkerResult(
                text=resp.text.strip() or "Tool budget exhausted.",
                ok=True,
                citations=citations,
                tool_verbs=verbs,
                domain_mismatch=_domain_mismatch(card, task, resp.text),
            )
        except HarnessError as exc:
            return WorkerResult(text=exc.safe_message, ok=False, error_class=exc.error_class.value)
        finally:
            span.extra["unknown_column"] = unknown_column
            span.extra["empty_retrieval"] = last_empty_retrieval
            span.extra["artifacts"] = len(ctx.session)
            span.extra["skills_loaded"] = [s.slug for s in loaded]
            span.extra["recoveries"] = recoveries
            if rebinds:
                span.extra["rebound_tools"] = rebinds
            span.extra["self_evals"] = n_calls // eval_every if eval_every else 0


_ERROR_HINTS = {
    "SchemaInvalid": "call schema_lookup and use only the listed columns",
    "RetrievalMiss": "broaden the query or lower filters",
    "UnknownTool": "call search_tools to see allowed verbs",
}


def _typed_error_line(error_class: str | None, message: str | None) -> str:
    """One-line typed error for the model. Raw tracebacks never enter the transcript."""
    cls = error_class or "ToolRuntime"
    text = " ".join(str(message or "").split())[:240]
    hint = _ERROR_HINTS.get(cls)
    return f"[{cls}] {text}" + (f" Hint: {hint}." if hint else "")


def _collapse_failure(messages: list[dict], call_id: str, verb: str) -> None:
    """After a later success, replace the earlier failure turn with a marker (scratchpad pruning)."""
    if not call_id:
        return
    for msg in messages:
        if msg.get("role") == "tool" and msg.get("tool_call_id") == call_id:
            msg["content"] = f"[recovered] earlier {verb} error resolved"
            return


def _looks_like_abstain(text: str) -> bool:
    t = text.lower()
    return any(s in t for s in ("cannot find", "not found", "insufficient", "don't have", "do not have", "abstain", "cannot answer"))


def _domain_mismatch(card: AgentCard, task: str, text: str) -> bool:
    """F3 signal. The heuristic belongs to the domain pack, because it is about the pack's data."""
    return pack_registry().domain_mismatch(card.id, task, text)
