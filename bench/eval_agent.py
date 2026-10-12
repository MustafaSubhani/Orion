"""Judge-on-trace wrapper used by bench.run."""

from __future__ import annotations

from harness.context import RequestContext
from harness.types import JudgeCard
from sandbox.eval_sandbox import judge_trace


def score_run(ctx: RequestContext, query: str, answer: str, evidence: str, spans_summary: str) -> JudgeCard:
    return judge_trace(ctx, query=query, answer=answer, evidence=evidence, spans_summary=spans_summary)
