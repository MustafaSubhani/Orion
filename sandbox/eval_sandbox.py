"""Isolated evaluation: oracles are pure functions; the judge never sees API keys or test gold during worker runs.

The judge model is configured separately (ORION_JUDGE_*). Pass only traces, artifacts, and evidence.
"""

from __future__ import annotations

import json
import re
from typing import Any

from harness.context import RequestContext
from harness.gateway import LLMGateway
from harness.trace import get_tracer
from harness.types import JudgeCard, SpanKind


def run_oracle(task: dict[str, Any], answer: str) -> dict[str, Any]:
    """Pure grader. `task` must already contain gold — workers must never call this on live chat."""
    from bench.oracles import grade

    return grade(task, answer)


def judge_trace(
    ctx: RequestContext,
    *,
    query: str,
    answer: str,
    evidence: str,
    spans_summary: str,
    gateway: LLMGateway | None = None,
) -> JudgeCard:
    """LLM-as-Judge on traces. Different model family than the worker when configured."""
    gw = gateway or ctx.runtime.gateway
    prompt = f"""You are an isolated evaluation agent. Score the worker run.
Return ONLY JSON with keys: overall (0-2 int), grounded (bool), complete (bool), safe (bool),
failure_class (F1|F2|F3|F4|F5|F6|F7|F8|none), repair_hint (short string), hallucinated_numbers (list of strings).

Rules:
- overall 2 = correct and grounded, 1 = partial, 0 = wrong or unsafe
- grounded=true only if numeric claims appear in EVIDENCE
- If the worker abstains honestly, overall can still be 2
- Do not invent gold answers. You do not have the oracle key.

QUERY:
{query}

ANSWER:
{answer[:4000]}

EVIDENCE:
{evidence[:6000]}

TRACE:
{spans_summary[:3000]}
"""
    tracer = get_tracer()
    with tracer.span(ctx.request_id, SpanKind.EVAL, "judge_trace", parent_id=ctx.parent_span_id):
        try:
            resp = gw.complete(
                ctx=ctx,
                messages=[{"role": "user", "content": prompt}],
                purpose="judge",
                force_json=True,
                name="eval.judge",
                max_tokens=400,
            )
            return _parse_card(resp.text)
        except Exception as exc:
            return JudgeCard(
                overall=0,
                grounded=False,
                complete=False,
                safe=True,
                failure_class="none",
                repair_hint=f"judge unavailable: {exc}",
            )


def _parse_card(text: str) -> JudgeCard:
    blob = text.strip()
    if "```" in blob:
        blob = blob.split("```")[1]
        blob = re.sub(r"^json", "", blob).strip()
    match = re.search(r"\{.*\}", blob, re.S)
    if match:
        blob = match.group(0)
    try:
        obj = json.loads(blob)
    except json.JSONDecodeError:
        return JudgeCard(overall=0, repair_hint="judge JSON parse failed")
    overall = int(obj.get("overall") or 0)
    overall = max(0, min(2, overall))
    fc = str(obj.get("failure_class") or "none")
    nums = obj.get("hallucinated_numbers") or []
    if not isinstance(nums, list):
        nums = []
    return JudgeCard(
        overall=overall,
        grounded=bool(obj.get("grounded")),
        complete=bool(obj.get("complete")),
        safe=bool(obj.get("safe", True)),
        failure_class=fc,
        repair_hint=str(obj.get("repair_hint") or ""),
        hallucinated_numbers=[str(x) for x in nums],
    )
