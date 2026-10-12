"""One-command benchmark: python -m bench.run --split dev --heal [--judge] [--inject F4]"""

from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict

from bench.compile_oracles import load_split
from bench.injectors import INJECT_CODES
from bench.oracles import grade, looks_like_abstain
from harness.config import OUTPUTS_DIR, ensure_dirs
from harness.context import new_context
from harness.planner import coordinate
from harness.runtime import build_runtime
from harness.trace import get_tracer

NUMERIC_TOOLS = frozenset(
    {
        "sql_spec",
        "date_at",
        "workforce_slice",
        "product_search",
        "calendar_query",
        "ratio_agg",
        "macro_latest",
        "series_window",
        "distinct_values",
        "row_lookup",
        "top_n",
        "compare_series",
    }
)


def _span_verbs(spans: list) -> set[str]:
    out: set[str] = set()
    for span in spans:
        name = getattr(span, "name", "") or ""
        if name.startswith("tool."):
            out.add(name.split(".", 1)[-1])
        extra = getattr(span, "extra", None) or {}
        if extra.get("verb"):
            out.add(str(extra["verb"]))
    return out


def tool_path_ok(task: dict, spans: list) -> bool | None:
    grader = task.get("grader_type") or task.get("grader")
    if grader == "abstain" or task.get("family") in {"cross-source-illegal", "unanswerable"}:
        return None
    verbs = _span_verbs(spans)
    if not verbs:
        return False
    return bool(verbs & NUMERIC_TOOLS)


def evidence_grounded(predicted, spans: list) -> bool | None:
    if predicted in (None, "", "ABSTAIN"):
        return None
    needle = str(predicted).replace(",", "")
    if needle.endswith(".0"):
        needle = needle[:-2]
    parts = []
    for span in spans:
        extra = getattr(span, "extra", None) or {}
        parts.append(str(extra.get("preview") or "").replace(",", ""))
        parts.append(getattr(span, "name", "") or "")
    blob = "\n".join(parts)
    return needle in blob if needle else None


def abstention_f1(rows: list[dict]) -> dict:
    gold = [
        (r.get("grader") == "abstain") or (r.get("family") in {"cross-source-illegal", "unanswerable"})
        for r in rows
    ]
    pred = [looks_like_abstain(str(r.get("answer") or "")) for r in rows]
    tp = sum(g and p for g, p in zip(gold, pred))
    fp = sum((not g) and p for g, p in zip(gold, pred))
    fn = sum(g and (not p) for g, p in zip(gold, pred))
    prec = tp / (tp + fp) if (tp + fp) else None
    rec = tp / (tp + fn) if (tp + fn) else None
    if prec is None or rec is None:
        f1 = 0.0 if (tp + fp + fn) else None
    elif prec + rec == 0:
        f1 = 0.0
    else:
        f1 = 2 * prec * rec / (prec + rec)
    return {
        "precision": prec,
        "recall": rec,
        "f1": f1,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "n_gold_abstain": sum(gold),
    }


def split_summary_from_rows(
    rows: list[dict],
    *,
    split: str,
    heal: bool,
    inject: str | None,
    judge: bool,
    seconds: float,
    retry_only: bool = False,
) -> dict:
    n_ok = sum(1 for r in rows if r.get("oracle_ok"))
    n = len(rows)
    tsr_ci = wilson_ci(n_ok, n)
    route = [r for r in rows if r.get("route_ok") is not None]
    route_ok_n = sum(1 for r in route if r["route_ok"])
    route_acc = (route_ok_n / len(route)) if route else None
    by_family: dict[str, list[bool]] = defaultdict(list)
    by_domain: dict[str, list[bool]] = defaultdict(list)
    for r in rows:
        by_family[str(r.get("family"))].append(bool(r.get("oracle_ok")))
        by_domain[str(r.get("domain"))].append(bool(r.get("oracle_ok")))

    def _group(src: dict[str, list[bool]]) -> dict:
        out = {}
        for key, vals in src.items():
            ok = sum(vals)
            ci = wilson_ci(ok, len(vals))
            out[key] = {"n": len(vals), "tsr": ok / max(len(vals), 1), "tsr_ci95": list(ci) if ci else None}
        return out

    injected = [r for r in rows if "recovered" in r]
    recovered = sum(1 for r in injected if r.get("recovered"))
    detected_n = sum(1 for r in injected if r.get("detected"))
    injected_n = len(injected)
    rsr = (recovered / injected_n) if injected_n else None
    fda = (detected_n / injected_n) if injected_n else None
    tokens_in = sum(int(r.get("tokens_in") or 0) for r in rows)
    tokens_out = sum(int(r.get("tokens_out") or 0) for r in rows)
    path_rows = [r for r in rows if r.get("tool_path_ok") is not None]
    path_ok_n = sum(1 for r in path_rows if r.get("tool_path_ok"))
    ev_rows = [r for r in rows if r.get("evidence_ok") is not None]
    ev_ok_n = sum(1 for r in ev_rows if r.get("evidence_ok"))
    abst = abstention_f1(rows)
    return {
        "split": split,
        "n": n,
        "tsr": n_ok / max(n, 1),
        "tsr_ci95": list(tsr_ci) if tsr_ci else None,
        "route_acc": route_acc,
        "route_ci95": list(wilson_ci(route_ok_n, len(route))) if route else None,
        "tool_path_acc": (path_ok_n / len(path_rows)) if path_rows else None,
        "tool_path_ci95": list(wilson_ci(path_ok_n, len(path_rows))) if path_rows else None,
        "evidence_acc": (ev_ok_n / len(ev_rows)) if ev_rows else None,
        "evidence_ci95": list(wilson_ci(ev_ok_n, len(ev_rows))) if ev_rows else None,
        "abstention_f1": abst,
        "seconds": seconds,
        "by_family": _group(by_family),
        "by_domain": _group(by_domain),
        "heal": heal,
        "retry_only": retry_only,
        "inject": inject,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "tokens_per_ok": ((tokens_in + tokens_out) / n_ok) if n_ok else None,
        "backtracks": sum(int(r.get("backtracks") or 0) for r in rows),
        "cascade_radius_max": max((int(r.get("cascade_radius") or 0) for r in rows), default=0),
        "rsr": rsr,
        "rsr_ci95": list(wilson_ci(recovered, injected_n)) if injected_n else None,
        "fda": fda,
        "fda_ci95": list(wilson_ci(detected_n, injected_n)) if injected_n else None,
        "judge": judge,
        "cost_tokens": tokens_in + tokens_out,
    }


def wilson_ci(successes: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """95% Wilson interval for a binomial rate. None when n=0."""
    if n <= 0:
        return None
    p = successes / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2 * n)) / denom
    margin = z * ((p * (1 - p) / n + z2 / (4 * n * n)) ** 0.5) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


def run_split(
    split: str,
    *,
    heal: bool,
    limit: int | None = None,
    judge: bool = False,
    inject: str | None = None,
    retry_only: bool = False,
    tag_extra: str = "",
) -> dict:
    build_runtime()
    tasks = load_split(split)
    if limit:
        tasks = tasks[:limit]
    rows = []
    t0 = time.time()
    tracer = get_tracer()
    for task in tasks:
        query = task["query"]
        result = coordinate(
            query, heal=heal, inject=inject, kb_enabled=False, retry_only=retry_only
        )
        answer = result.final_response
        oracle = grade(task, answer)
        spans = tracer.spans_for(result.request_id)
        tokens_in = sum(s.tokens_in for s in spans)
        tokens_out = sum(s.tokens_out for s in spans)
        row = {
            "id": task.get("id"),
            "split": split,
            "domain": task.get("domain"),
            "family": task.get("family"),
            "query": query,
            "answer": answer,
            "oracle_ok": oracle.get("ok"),
            "grader": oracle.get("grader"),
            "gold": oracle.get("gold"),
            "predicted": oracle.get("predicted"),
            "agents": result.agents_used,
            "expected_agent": task.get("expected_agent"),
            "expected_agents": task.get("expected_agents") or ([task.get("expected_agent")] if task.get("expected_agent") else []),
            "route_ok": (
                any(a in (result.agents_used or []) for a in (task.get("expected_agents") or [task.get("expected_agent")]))
                if (task.get("expected_agents") or task.get("expected_agent"))
                else None
            ),
            "tool_path_ok": tool_path_ok(task, spans),
            "evidence_ok": evidence_grounded(oracle.get("predicted"), spans),
            "success": result.success,
            "n_spans": len(spans),
            "request_id": result.request_id,
            "backtracks": result.backtracks,
            "graph": result.graph,
            "cascade_radius": (result.graph or {}).get("cascade_radius") or 0,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "tokens_used": result.tokens_used,
            "heal": heal,
            "retry_only": retry_only,
            "inject": inject,
        }
        if inject:
            row["detected"] = _detected(inject, spans, result.tasks)
            row["recovered"] = bool(oracle.get("ok"))
        if judge:
            row["judge"] = _maybe_judge(result.request_id, query, answer, spans)
        rows.append(row)
        print(
            f"bench {len(rows)}/{len(tasks)} oracle_ok={row['oracle_ok']} family={row.get('family')}",
            flush=True,
        )
    elapsed = time.time() - t0
    summary = split_summary_from_rows(
        rows,
        split=split,
        heal=heal,
        inject=inject,
        judge=judge,
        seconds=elapsed,
        retry_only=retry_only,
    )
    ensure_dirs()
    tag = f"{split}_{'heal' if heal else 'noheal'}"
    if retry_only:
        tag += "_retryonly"
    if inject:
        tag += f"_inject{inject}"
    if judge:
        tag += "_judge"
    if tag_extra:
        tag += f"_{tag_extra}"
    out = OUTPUTS_DIR / f"bench_{tag}.json"
    summary["out"] = str(out)
    out.write_text(json.dumps({"summary": summary, "rows": rows}, indent=2, default=str), encoding="utf-8")
    return summary


def unique_by_query(tasks: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for task in tasks:
        q = str(task.get("query") or "")
        if q in seen:
            continue
        seen.add(q)
        out.append(task)
    return out


def run_passk(
    split: str,
    *,
    k: int,
    heal: bool,
    limit: int | None = None,
    judge: bool = False,
    tag_extra: str = "",
) -> dict:
    """τ-bench-style pass^k: a task counts only if all k independent trials succeed."""
    k = max(2, int(k))
    build_runtime()
    tasks = unique_by_query(load_split(split))
    if limit:
        tasks = tasks[:limit]
    tracer = get_tracer()
    t0 = time.time()
    rows = []
    hat_ok = 0
    at_ok = 0
    for task in tasks:
        trials = []
        for i in range(k):
            result = coordinate(task["query"], heal=heal, kb_enabled=False)
            oracle = grade(task, result.final_response)
            spans = tracer.spans_for(result.request_id)
            trials.append(
                {
                    "trial": i,
                    "oracle_ok": bool(oracle.get("ok")),
                    "answer": result.final_response,
                    "request_id": result.request_id,
                    "tokens_in": sum(s.tokens_in for s in spans),
                    "tokens_out": sum(s.tokens_out for s in spans),
                }
            )
        oks = [t["oracle_ok"] for t in trials]
        hat = all(oks)
        at = any(oks)
        if hat:
            hat_ok += 1
        if at:
            at_ok += 1
        print(
            f"passk {len(rows) + 1}/{len(tasks)} hat={hat} at={at} k_ok={sum(oks)}/{k} family={task.get('family')}",
            flush=True,
        )
        rows.append(
            {
                "id": task.get("id"),
                "query": task.get("query"),
                "family": task.get("family"),
                "domain": task.get("domain"),
                "pass_hat_k": hat,
                "pass_at_k": at,
                "k_successes": sum(oks),
                "k": k,
                "trials": trials,
            }
        )
    n = max(len(rows), 1)
    elapsed = time.time() - t0
    hat_ci = wilson_ci(hat_ok, len(rows))
    at_ci = wilson_ci(at_ok, len(rows))
    summary = {
        "split": split,
        "n": len(rows),
        "k": k,
        "heal": heal,
        "pass_hat_k": hat_ok / n,
        "pass_hat_k_ci95": list(hat_ci) if hat_ci else None,
        "pass_at_k": at_ok / n,
        "pass_at_k_ci95": list(at_ci) if at_ci else None,
        "seconds": elapsed,
        "tokens_in": sum(t["tokens_in"] for r in rows for t in r["trials"]),
        "tokens_out": sum(t["tokens_out"] for r in rows for t in r["trials"]),
        "judge": judge,
    }
    ensure_dirs()
    tag = f"{split}_{'heal' if heal else 'noheal'}_passk{k}_n{len(rows)}"
    if tag_extra:
        tag += f"_{tag_extra}"
    out = OUTPUTS_DIR / f"bench_{tag}.json"
    summary["out"] = str(out)
    out.write_text(json.dumps({"summary": summary, "rows": rows}, indent=2, default=str), encoding="utf-8")
    return summary


def _detected(inject: str, spans: list, tasks: list) -> bool:
    needles = {
        "F1": {"F1", "SchemaInvalid", "ToolRuntime"},
        "F2": {"F2", "RetrievalMiss"},
        "F3": {"F3", "RoutingError", "UnknownAgent"},
        "F4": {"F4", "SchemaInvalid"},
        "F5": {"F5"},
        "F6": {"F6"},
        "F7": {"F7", "PolicyDenied"},
        "F8": {"F8"},
    }.get(inject, {inject})
    for span in spans:
        extra = getattr(span, "extra", None) or {}
        if extra.get("class") in needles:
            return True
        if getattr(span, "error_class", None) in needles:
            return True
        kind = getattr(span.kind, "value", span.kind)
        if kind == "heal" and inject in (span.name or ""):
            return True
    for task in tasks or []:
        if isinstance(task, dict) and task.get("failure_class") in needles:
            return True
    return False


def judge_payload(query: str, answer: str, spans: list) -> tuple[str, str]:
    """Trace summary + evidence for the isolated judge. No gold, no API keys."""
    summary = "\n".join(
        f"{getattr(s.kind, 'value', s.kind)} {s.name} err={s.error_class} in={s.tokens_in} out={s.tokens_out}"
        for s in spans[:40]
    )
    parts = [str(answer or "")[:1500]]
    for span in spans[:40]:
        extra = getattr(span, "extra", None) or {}
        preview = extra.get("preview")
        if preview:
            parts.append(str(preview)[:800])
    evidence = "\n".join(parts)[:6000]
    return summary, evidence


def _maybe_judge(request_id: str, query: str, answer: str, spans: list) -> dict:
    try:
        from bench.eval_agent import score_run

        ctx = new_context(user="eval", request_id=request_id)
        summary, evidence = judge_payload(query, answer, spans)
        card = score_run(ctx, query, answer, evidence=evidence, spans_summary=summary)
        return card.to_dict()
    except Exception as exc:
        return {"error": str(exc)[:300], "overall": 0}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", default="dev", choices=["dev", "val", "test"])
    parser.add_argument("--heal", action="store_true")
    parser.add_argument("--no-heal", action="store_true")
    parser.add_argument("--judge", action="store_true", help="secondary LLM-as-Judge on traces")
    parser.add_argument("--retry-only", action="store_true", help="ablation: typed classes collapse to RETRY except F6/F7")
    parser.add_argument("--inject", default=None, choices=list(INJECT_CODES), help="one-shot fault for FDA/RSR")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--passk", type=int, default=0, help="τ-bench pass^k trials per unique query (0 = off)")
    parser.add_argument("--tag", default="", help="optional filename suffix (e.g. qwen)")
    args = parser.parse_args()
    if args.split == "test":
        print("WARNING: test split should be scored once at the end of experiments.")
    heal = not args.no_heal
    if args.passk:
        summary = run_passk(
            args.split,
            k=args.passk,
            heal=heal,
            limit=args.limit,
            judge=args.judge,
            tag_extra=args.tag,
        )
    else:
        summary = run_split(
            args.split,
            heal=heal,
            limit=args.limit,
            judge=args.judge,
            inject=args.inject,
            retry_only=args.retry_only,
            tag_extra=args.tag,
        )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
