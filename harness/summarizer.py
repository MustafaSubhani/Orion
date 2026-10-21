"""Abstractive context summarization with a hard fact check and an extractive fallback.

Two uses:
1. `pack_for_model` replaces middle turns with a summary instead of cutting them to snippets.
2. `fold_thread` keeps a rolling summary of a long conversation thread.

Contract: a summary reaches a worker only if every number, date, entity tag and artifact id in its
input also appears in it. Anything else (timeout, API error, a dropped fact) falls back to the
extractive fold and records why. Product mode only; eval mode never calls a model to summarize.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from harness import threads
from harness.context import RequestContext, is_product, new_context
from harness.errors import HarnessError
from harness.settings_store import effective
from harness.trace import hash_payload

PROMPT = """Summarize the conversation turns below for an agent that will continue the task.
Use exactly these sections: Goal, Facts established, Tools called and outcomes, Open questions.
Copy every number, date, entity tag and artifact:<id> reference verbatim. Do not add facts.

Turns:
{turns}"""

_ARTIFACT = r"artifact:[0-9a-f]{6,}"
_DATE = r"\b\d{4}-\d{2}-\d{2}\b"
_NUMBER = r"-?\d[\d,]*(?:\.\d+)?"
_LIST_MARKER = re.compile(r"(?m)^\s*\d+[.)]\s")


def _entity_pattern() -> str:
    try:
        from harness.packs import registry

        tags = sorted((t for t in registry().allowed_entities() if len(t) > 1), key=len, reverse=True)
    except Exception:  # entity tags are an extra check; never block summarizing because the registry is unavailable
        tags = []
    return r"\b(?:" + "|".join(re.escape(t) for t in tags) + r")\b" if tags else r"(?!x)x"


def facts(text: str) -> set[str]:
    """Verifiable tokens in `text`: artifact ids, ISO dates, numbers and entity tags."""
    cleaned = _LIST_MARKER.sub("", text or "")
    pattern = re.compile("|".join((_ARTIFACT, _DATE, _NUMBER, _entity_pattern())))
    found = {m.group(0).rstrip(".,") for m in pattern.finditer(cleaned)}
    return {f.replace(",", "") if re.fullmatch(_NUMBER, f) else f for f in found}


def _render(turns: list[dict[str, Any]]) -> str:
    return "\n".join(f"{m.get('role', '?')}: {m.get('content') or ''}" for m in turns)


def summarize_turns(ctx: RequestContext, turns: list[dict[str, Any]]) -> tuple[Optional[str], str]:
    """Return `(summary, reason)`. `summary` is None when the caller must use the extractive fold."""
    if not is_product(ctx):
        return None, "eval_mode"
    if effective("context.summarize") != "abstractive":
        return None, "disabled"
    blob = _render(turns)
    if not blob.strip():
        return None, "empty"
    key = "sum:" + hash_payload(blob)
    cached = ctx.session.get_meta(key)
    if cached:
        return cached, "cache"
    try:
        response = ctx.runtime.gateway.complete(
            ctx=ctx,
            messages=[{"role": "user", "content": PROMPT.format(turns=blob)}],
            purpose="summarize",
            name="context.summarize",
            max_tokens=int(effective("memory.summary_max_tokens")),
        )
    except HarnessError as exc:
        return None, f"error:{exc.error_class.value}"
    summary = (response.text or "").strip()
    if not summary:
        return None, "empty_summary"
    missing = facts(blob) - facts(summary)
    if missing:
        return None, f"dropped_facts:{len(missing)}"
    ctx.session.set_meta(key, summary)
    return summary, "ok"


def extractive_summary(previous: str, turns: list[dict[str, Any]], *, snippet: int = 220, cap: int = 3000) -> str:
    """Deterministic fallback: the old summary plus a short snippet of every folded turn."""
    lines = []
    if previous.strip():
        lines.append(previous.strip()[: cap // 2])
    for turn in turns:
        content = " ".join(str(turn.get("content") or "").split())
        lines.append(f"- {turn.get('role', '?')}: {content[:snippet]}")
    return "\n".join(lines)[-cap:]


def fold_thread(thread_id: str, tenant: str, user: str, *, keep_tail: int = 6, force: bool = False) -> dict[str, Any]:
    """Fold the oldest unsummarized turns of a thread into its rolling summary.

    Runs only when the thread has grown past `memory.summary_trigger_tokens` (or `force`). The newest
    `keep_tail` turns stay verbatim. Returns `{"folded": n, "reason": ...}`; never raises.
    """
    try:
        if not effective("memory.enabled"):
            return {"folded": 0, "reason": "memory_disabled"}
        thread = threads.get(thread_id, tenant, user)
        if thread is None:
            return {"folded": 0, "reason": "unknown_thread"}
        if not force and threads.unsummarized_tokens(thread) < int(effective("memory.summary_trigger_tokens")):
            return {"folded": 0, "reason": "below_trigger"}
        pending = threads.messages(thread_id, after_id=int(thread["summary_upto"] or 0))
        foldable = pending[:-keep_tail] if keep_tail else pending
        if not foldable:
            return {"folded": 0, "reason": "nothing_to_fold"}
        ctx = new_context(user=user)
        ctx.extra.update({"mode": "product", "tenant": tenant})
        previous = thread.get("summary") or ""
        inputs = ([{"role": "system", "content": f"Earlier summary:\n{previous}"}] if previous else []) + foldable
        summary, reason = summarize_turns(ctx, inputs)
        if summary is None:
            summary = extractive_summary(previous, foldable)
            reason = f"extractive({reason})"
        threads.set_summary(thread_id, summary, int(foldable[-1]["id"]))
        return {"folded": len(foldable), "reason": reason}
    except Exception as exc:  # background housekeeping must never take chat down
        return {"folded": 0, "reason": f"error:{type(exc).__name__}"}
