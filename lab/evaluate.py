"""Lab quality gate: contract tests built from the live schema, promotion, and guarded repair.

Contract tests are generated at evaluation time from the data the agent is declared to use (frozen
tables and connector tables, read through the same `schema_lookup` and `sql_spec` tools an agent
uses). Gold is computed directly from that data. Nothing here reads `bench/`.

Up to four cases per agent: a row count, a filtered count, an average, and an abstention on an
illegal entity join. An agent is promotable only if its latest evaluation, for its current card
version, scores at least `lab.min_score`.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Optional

from harness.config import LAB_GENERATED_DIR
from harness.context import RequestContext, new_context
from harness.fsutil import atomic_write
from harness.settings_store import effective
from harness.types import AgentCard

ABSTAIN_PHRASES = (
    "abstain",
    "cannot combine",
    "can't combine",
    "cannot be combined",
    "illegal",
    "not allowed",
    "cannot answer",
    "unable to combine",
    "cannot join",
    "refuse",
    "not permitted",
    "must not be combined",
)
_NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
Runner = Callable[[str, str], str]


@dataclass
class Contract:
    kind: str  # count | filter_count | mean | abstain
    question: str
    gold: Any  # a number, or the string "abstain"
    table: str = ""


@dataclass
class CaseResult:
    kind: str
    question: str
    gold: Any
    ok: bool
    answer: str = ""
    error: str = ""


@dataclass
class Evaluation:
    agent_id: str
    version: int
    score: float
    passed: bool
    min_score: float
    cases: list[CaseResult] = field(default_factory=list)
    actor: str = "lab"
    created_at: float = 0.0
    note: str = ""

    def summary(self) -> dict[str, Any]:
        """The small record stored on the card (no answers, no questions)."""
        return {
            "score": self.score,
            "passed": self.passed,
            "version": self.version,
            "min_score": self.min_score,
            "cases": len(self.cases),
            "created_at": self.created_at,
            "actor": self.actor,
            "note": self.note,
        }

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------- grading


def numbers_in(text: str) -> list[float]:
    out: list[float] = []
    for token in _NUMBER.findall(text or ""):
        try:
            out.append(float(token.replace(",", "")))
        except ValueError:
            continue
    return out


def grade(contract: Contract, answer: str) -> bool:
    """Pure grader. Numbers are exact for integers and within 0.5% for averages; abstentions need a refusal."""
    text = (answer or "").lower()
    if contract.gold == "abstain":
        return any(phrase in text for phrase in ABSTAIN_PHRASES)
    gold = float(contract.gold)
    for value in numbers_in(answer):
        if float(gold).is_integer() and contract.kind != "mean":
            if value == gold:
                return True
        elif abs(value - gold) <= max(abs(gold) * 0.005, 1e-9):
            return True
    return False


# ---------------------------------------------------------------- contract generation


def _is_number(dtype: str) -> bool:
    d = str(dtype).lower()
    return any(k in d for k in ("int", "float", "double", "numeric", "decimal", "real"))


def _is_text(dtype: str) -> bool:
    d = str(dtype).lower()
    return any(k in d for k in ("object", "str", "text", "char", "varchar"))


def _tables_for(card: AgentCard, tools: Any, ctx: RequestContext) -> list[str]:
    """Table references this agent is declared to use, via the same tools the agent calls."""
    wanted = {e.upper() for e in card.entities}
    refs: list[str] = []
    listing = tools.call("schema_lookup", {}, ctx)
    if listing.ok and isinstance(listing.data, dict):
        for row in listing.data.get("tables", []) or []:
            if str(row.get("entity", "")).upper() in wanted and not row.get("error"):
                refs.append(str(row["table"]))
    try:
        from harness.connectors import registry as connectors

        for connector in connectors().all():
            if connector.entity.upper() in wanted:
                refs.extend(f"{connector.id}.{t}" for t in connector.tables())
    except Exception:  # an unavailable connector just means fewer contracts
        pass
    return list(dict.fromkeys(refs))


def _columns(tools: Any, ctx: RequestContext, ref: str) -> list[tuple[str, str]]:
    res = tools.call("schema_lookup", {"table": ref}, ctx)
    if not res.ok or not isinstance(res.data, dict):
        return []
    return [(str(c.get("name")), str(c.get("dtype") or c.get("type") or "")) for c in res.data.get("columns", []) or []]


def _spec(tools: Any, ctx: RequestContext, spec: dict[str, Any]) -> Optional[dict[str, Any]]:
    res = tools.call("sql_spec", spec, ctx)
    return res.data if res.ok and isinstance(res.data, dict) else None


def build_contracts(card: AgentCard, *, tools: Any = None, max_cases: int = 4) -> list[Contract]:
    """Deterministic cases with gold computed from live data. Empty if the agent has no usable tables."""
    from harness.context import get_runtime
    from harness.packs import registry as packs

    tools = tools or get_runtime().tools
    ctx = new_context(user="lab-eval")
    ctx.extra["mode"] = "product"
    contracts: list[Contract] = []
    seen_kinds: set[str] = set()
    for ref in _tables_for(card, tools, ctx)[:4]:
        columns = _columns(tools, ctx, ref)
        if "count" not in seen_kinds or "filter_count" not in seen_kinds:
            for name, dtype in columns:
                if not _is_text(dtype):
                    continue
                grouped = _spec(tools, ctx, {"table": ref, "group_by": [name], "limit": 200})
                records = (grouped or {}).get("records") or []
                if not 1 < len(records) < 50:
                    continue
                count_key = next((k for k in records[0] if k != name), None)
                if count_key is None:
                    continue
                if "count" not in seen_kinds:
                    total = sum(int(r[count_key]) for r in records)
                    contracts.append(
                        Contract(
                            "count", f"How many rows are in the table {ref}? Use the data tools and give the exact number.", total, ref
                        )
                    )
                    seen_kinds.add("count")
                if "filter_count" not in seen_kinds:
                    top = max(records, key=lambda r: int(r[count_key]))
                    if top[name] is not None:
                        contracts.append(
                            Contract(
                                "filter_count",
                                f"In table {ref}, how many rows have {name} equal to '{top[name]}'? Give the exact number.",
                                int(top[count_key]),
                                ref,
                            )
                        )
                        seen_kinds.add("filter_count")
                break
        if "mean" not in seen_kinds:
            for name, dtype in columns:
                if _is_number(dtype) and not name.lower().endswith("id"):
                    result = _spec(tools, ctx, {"table": ref, "agg": {name: "mean"}})
                    rows = (result or {}).get("records") or []
                    value = rows[0].get(f"{name}_mean") if rows else None
                    if isinstance(value, (int, float)) and value == value:
                        contracts.append(
                            Contract("mean", f"What is the average of {name} in table {ref}? Give the number.", float(value), ref)
                        )
                        seen_kinds.add("mean")
                        break
        if len(contracts) >= max_cases - 1:
            break
    if contracts:
        first = contracts[0].table
        mine = {e.upper() for e in card.entities}
        reg = packs()
        for other in reg.allowed_entities():
            if other.upper() in mine or other == "KB":
                continue
            if all(not reg.entities_join_legal([m, other])[0] for m in mine):
                contracts.append(
                    Contract(
                        "abstain",
                        f"Give one combined total that adds the row count of table {first} to the row count of any {other} table.",
                        "abstain",
                        first,
                    )
                )
                break
    return contracts[:max_cases]


# ---------------------------------------------------------------- running


def default_runner(question: str, agent_id: str) -> str:
    """Ask the pinned agent in live-chat mode (so connector tables work). Needs a configured model."""
    from harness.planner import coordinate

    result = coordinate(question, pin_agent=agent_id, kb_enabled=True, user="lab-eval")
    return result.final_response


def evaluate(
    agent_id: str,
    *,
    actor: str = "lab",
    runner: Optional[Runner] = None,
    tools: Any = None,
    persist: bool = True,
    contracts: Optional[list[Contract]] = None,
) -> Evaluation:
    """Run the contract tests against an agent and record the verdict. Raises ValueError for non-Lab agents."""
    from harness.agent_registry import get_registry

    registry = get_registry()
    card = registry.get(agent_id)
    if card is None or not card.is_custom:
        raise ValueError(f"'{agent_id}' is not a Lab agent")
    min_score = float(effective("lab.min_score"))
    cases_spec = contracts if contracts is not None else build_contracts(card, tools=tools)
    run = runner or default_runner
    results: list[CaseResult] = []
    for contract in cases_spec:
        try:
            answer = str(run(contract.question, agent_id))
            results.append(CaseResult(contract.kind, contract.question, contract.gold, grade(contract, answer), answer[:300]))
        except Exception as exc:  # one failing probe is a failed case, not a failed evaluation run
            results.append(
                CaseResult(contract.kind, contract.question, contract.gold, False, error=f"{type(exc).__name__}: {str(exc)[:160]}")
            )
    score = (sum(1 for r in results if r.ok) / len(results)) if results else 0.0
    evaluation = Evaluation(
        agent_id=agent_id,
        version=card.version,
        score=round(score, 3),
        passed=bool(results) and score >= min_score,
        min_score=min_score,
        cases=results,
        actor=actor,
        created_at=time.time(),
        note="" if results else "no contract tests could be built: the agent declares no readable tables",
    )
    if persist:
        atomic_write(LAB_GENERATED_DIR / agent_id / "evaluation.json", json.dumps(evaluation.to_dict(), indent=2, default=str))
        registry.set_metadata(agent_id, "evaluation", evaluation.summary())
    return evaluation


def latest(agent_id: str) -> Optional[dict[str, Any]]:
    path = LAB_GENERATED_DIR / agent_id / "evaluation.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def summarize(evaluation: Evaluation) -> str:
    lines = [
        f"**Evaluation of `{evaluation.agent_id}`**: {'PASSED' if evaluation.passed else 'FAILED'} "
        f"(score {evaluation.score:.2f}, needs {evaluation.min_score:.2f})"
    ]
    for case in evaluation.cases:
        lines.append(f"- {'ok' if case.ok else 'FAIL'} [{case.kind}] {case.question}" + (f" ({case.error})" if case.error else ""))
    if evaluation.note:
        lines.append(evaluation.note)
    if evaluation.passed:
        lines.append(f"Run `deploy {evaluation.agent_id}` to promote it.")
    return "\n".join(lines)


def promotion_status(card: AgentCard) -> tuple[bool, str]:
    """May this Lab agent be promoted now? Used by the predeploy check."""
    if not effective("lab.auto_eval"):
        return True, "evaluation not required (lab.auto_eval is off)"
    record = (card.metadata or {}).get("evaluation")
    if not record:
        return False, "not evaluated yet: run the contract tests first"
    if record.get("version") != card.version:
        return False, f"evaluation is for version {record.get('version')}, the agent is at version {card.version}"
    if not record.get("passed"):
        return False, f"evaluation scored {record.get('score')}, below {record.get('min_score')}"
    return True, f"evaluation passed ({record.get('score')})"


# ---------------------------------------------------------------- guarded repair


def guarded_repair(
    agent_id: str,
    apply_patch: Callable[[], Any],
    *,
    runner: Optional[Runner] = None,
    tools: Any = None,
    actor: str = "repair",
    judge_before: Optional[float] = None,
    judge_after: Optional[float] = None,
) -> dict[str, Any]:
    """Apply a patch, re-run the contract tests, and revert it if contracts got worse.

    A patch that raises the judge score while contracts fall is a reward hack and is reverted.
    The contract set is fixed before the patch so both runs answer the same questions.
    """
    from harness.agent_registry import get_registry

    registry = get_registry()
    card = registry.require(agent_id)
    workdir = LAB_GENERATED_DIR / agent_id
    snapshot = {p: p.read_text(encoding="utf-8") for p in workdir.glob("*") if p.is_file() and p.suffix in {".py", ".json"}}
    card_before = card.to_dict()
    contracts = build_contracts(card, tools=tools)
    before = evaluate(agent_id, actor=actor, runner=runner, tools=tools, persist=False, contracts=contracts)
    proposal = apply_patch()
    if not getattr(proposal, "accepted", False):
        return {
            "accepted": False,
            "rolled_back": False,
            "reason": getattr(proposal, "reason", "rejected"),
            "before": before.score,
            "after": before.score,
        }
    after = evaluate(agent_id, actor=actor, runner=runner, tools=tools, persist=False, contracts=contracts)
    hacked = judge_before is not None and judge_after is not None and judge_after > judge_before and after.score < before.score
    if after.score < before.score:
        for path, text in snapshot.items():
            atomic_write(path, text)
        from harness.types import AgentCard

        registry.update(AgentCard.from_dict(card_before), actor=actor, note="repair reverted: contract score dropped")
        return {
            "accepted": False,
            "rolled_back": True,
            "reward_hacked": hacked,
            "before": before.score,
            "after": after.score,
            "reason": "contract score dropped" + (" while the judge score rose (reward hacking)" if hacked else ""),
        }
    evaluate(agent_id, actor=actor, runner=runner, tools=tools, persist=True, contracts=contracts)
    return {
        "accepted": True,
        "rolled_back": False,
        "reward_hacked": False,
        "before": before.score,
        "after": after.score,
        "reason": "applied",
    }
