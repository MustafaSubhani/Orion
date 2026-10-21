"""Repair Agent: patch workers from traces. Never opens bench/ or eval gold. Dev-only."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from harness.config import LAB_GENERATED_DIR, PROJECT_ROOT
from harness.context import RequestContext
from harness.types import AgentCard, JudgeCard
from sandbox.gate import static_gate

# Only generated agents are patchable. `workers/` is the shared ReAct kernel, so it is never a repair target.
ALLOW_PREFIXES = (PROJECT_ROOT / "lab" / "generated",)
DENY_PREFIXES = tuple(
    PROJECT_ROOT / name
    for name in ("bench", "harness", "sandbox", "workers", "backend", "packs", "kb", "retrieval", "repair", "tests", "data")
)


@dataclass
class PatchProposal:
    agent_id: str
    path: str
    new_text: str
    note: str
    accepted: bool
    reason: str


def path_allowed(path: Path) -> bool:
    resolved = path.resolve()
    for denied in DENY_PREFIXES:
        try:
            resolved.relative_to(denied.resolve())
            return False
        except ValueError:
            continue
    for allowed in ALLOW_PREFIXES:
        try:
            resolved.relative_to(allowed.resolve())
            return True
        except ValueError:
            continue
    return False


def reward_hacked(dev_oracle_before: float, dev_oracle_after: float, judge_before: float, judge_after: float) -> bool:
    """Judge up + oracle down is reward hacking — do not deploy."""
    return judge_after > judge_before and dev_oracle_after < dev_oracle_before


def repair(
    ctx: RequestContext,
    card: AgentCard,
    *,
    failing_trace: str,
    judge: JudgeCard,
    oracle_diff: str,
    actor: str = "repair",
) -> PatchProposal:
    """Propose a prompt/card patch. Applies only if the path is allow-listed and static gate passes."""
    if not card.is_custom:
        return PatchProposal(card.id, "", "", "builtin workers are not auto-patched", False, "builtin")
    target = Path(card.module_path) if card.module_path else (LAB_GENERATED_DIR / card.id / "card.json")
    if not path_allowed(target):
        return PatchProposal(card.id, str(target), "", "path denied", False, "allow-list")

    prompt = f"""You are the Repair Agent. Propose a replacement for the worker file.
Return JSON {{"note": "...", "new_text": "full file contents"}}.
You may only change prompts, filters, or tool lists. No imports of os/subprocess.
Failing trace:
{failing_trace[:4000]}
Judge card: {json.dumps(judge.to_dict())}
Oracle diff: {oracle_diff[:1500]}
Current file:
{target.read_text(encoding='utf-8') if target.exists() else '(missing)'}
"""
    resp = ctx.runtime.gateway.complete(
        ctx=ctx,
        messages=[{"role": "user", "content": prompt}],
        purpose="repair",
        force_json=True,
        name="repair.patch",
        max_tokens=2500,
    )
    try:
        obj = json.loads(resp.text)
        new_text = obj.get("new_text") or ""
        note = obj.get("note") or ""
    except json.JSONDecodeError:
        return PatchProposal(card.id, str(target), "", "invalid JSON", False, "parse")
    if not new_text.strip():
        return PatchProposal(card.id, str(target), "", note, False, "empty")
    tmp = target.with_suffix(target.suffix + ".proposed")
    tmp.write_text(new_text, encoding="utf-8")
    if target.suffix == ".py":
        gate = static_gate(tmp)
        if not gate.ok:
            tmp.unlink(missing_ok=True)
            return PatchProposal(card.id, str(target), new_text, note, False, gate.reason)
    from harness.agent_registry import get_registry
    from harness.fsutil import atomic_write

    atomic_write(target, new_text)
    tmp.unlink(missing_ok=True)
    card.version += 1
    get_registry().update(card, actor=actor, note=note)  # one version store, shared with the live registry
    return PatchProposal(card.id, str(target), new_text, note, True, "applied")
