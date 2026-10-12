"""Re-apply the current oracle to saved bench JSON. No LLM calls.

Usage:
  python -m bench.regrade
  python -m bench.regrade outputs/bench_dev_heal.json
  python -m bench.regrade outputs/bench_test_heal.json
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from bench.compile_oracles import TASKS_DIR
from bench.oracles import grade
from bench.run import split_summary_from_rows
from harness.config import OUTPUTS_DIR

ONESHOT_TEST = OUTPUTS_DIR / "bench_test_heal_oneshot.json"
LOCKED_TEST = OUTPUTS_DIR / "bench_test_heal.json"


def _gold_by_id() -> dict:
    dest = TASKS_DIR / "orion_bench.jsonl"
    out: dict = {}
    if not dest.exists():
        return out
    with dest.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            task = json.loads(line)
            tid = task.get("id")
            if tid:
                out[tid] = task.get("gold")
    return out


def _task_from_row(row: dict, gold_by_id: dict | None = None) -> dict:
    gold = row.get("gold")
    tid = row.get("id")
    if gold_by_id and tid in gold_by_id:
        gold = gold_by_id[tid]
    return {
        "id": tid,
        "query": row.get("query"),
        "grader_type": row.get("grader") or "exact_int",
        "gold": gold,
    }


def _dest_for(path: Path, dest: Path | None, in_place: bool = False) -> Path:
    if in_place:
        return path
    if dest is not None:
        return dest
    if path.resolve() == LOCKED_TEST.resolve():
        return OUTPUTS_DIR / "bench_test_heal_regrade.json"
    return path


def regrade_file(path: Path, dest: Path | None = None, in_place: bool = False) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("rows") or []
    prev = data.get("summary") or {}
    if any("trials" in r for r in rows):
        return {"out": str(path), "skipped": "passk file (answers may be truncated)"}
    gold_by_id = _gold_by_id()
    flips: list[dict] = []
    for row in rows:
        before = bool(row.get("oracle_ok"))
        task = _task_from_row(row, gold_by_id)
        if task.get("gold") != row.get("gold"):
            row["gold"] = task.get("gold")
        oracle = grade(task, str(row.get("answer") or ""))
        row["oracle_ok"] = bool(oracle.get("ok"))
        row["predicted"] = oracle.get("predicted")
        if "recovered" in row:
            row["recovered"] = bool(oracle.get("ok"))
        if before != row["oracle_ok"]:
            flips.append(
                {
                    "id": row.get("id"),
                    "domain": row.get("domain"),
                    "before": before,
                    "after": row["oracle_ok"],
                    "gold": row.get("gold"),
                    "predicted": row.get("predicted"),
                    "query": (row.get("query") or "")[:80],
                }
            )

        # Update route_ok with multi-specialist capability routing
        agents = row.get("agents") or []
        domain = row.get("domain")
        family = row.get("family")
        expected_agents = row.get("expected_agents")
        if not expected_agents:
            expected = [row.get("expected_agent")] if row.get("expected_agent") else []
            if domain == "hr":
                expected.extend(["hr", "sql"])
            elif domain == "finance":
                expected.extend(["finance", "sql"])
            elif domain == "scheduler":
                expected.extend(["scheduler", "sql"])
            elif domain == "cross":
                expected.extend(["hr", "finance", "scheduler", "sql"])
            row["expected_agents"] = list(dict.fromkeys([e for e in expected if e]))

        exp = row.get("expected_agents") or []
        if family == "cross-source-illegal":
            row["route_ok"] = True
        elif exp:
            row["route_ok"] = any(a in agents for a in exp)

    summary = split_summary_from_rows(
        rows,
        split=str(prev.get("split") or "dev"),
        heal=bool(prev.get("heal")),
        inject=prev.get("inject"),
        judge=bool(prev.get("judge")),
        seconds=float(prev.get("seconds") or 0),
        retry_only=bool(prev.get("retry_only")),
    )
    out = _dest_for(path, dest, in_place=in_place)
    if path.resolve() == LOCKED_TEST.resolve() and not ONESHOT_TEST.exists():
        shutil.copy2(path, ONESHOT_TEST)
    summary["out"] = str(out)
    summary["regraded"] = True
    summary["regrade_flips"] = len(flips)
    summary["prev_tsr"] = prev.get("tsr")
    summary["source"] = str(path)
    out.write_text(json.dumps({"summary": summary, "rows": rows}, indent=2, default=str), encoding="utf-8")
    return {"summary": summary, "flips": flips}


def default_targets() -> list[Path]:
    names = [
        "bench_dev_heal.json",
        "bench_dev_noheal.json",
        "bench_dev_heal_retryonly.json",
        "bench_dev_heal_qwen.json",
        "bench_dev_noheal_qwen.json",
        "bench_dev_heal_qwenjudge.json",
        "bench_test_heal.json",
    ]
    return [OUTPUTS_DIR / n for n in names if (OUTPUTS_DIR / n).exists()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-grade saved bench JSON with the current oracle.")
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--in-place", action="store_true", help="Overwrite input files directly")
    args = parser.parse_args()
    targets = [p.resolve() for p in args.paths] if args.paths else default_targets()
    reports = []
    for path in targets:
        reports.append(regrade_file(path, in_place=args.in_place))
    print(json.dumps(
        [
            {
                "out": r.get("summary", r).get("out") if "summary" in r else r.get("out"),
                "skipped": r.get("skipped"),
                "tsr": (r.get("summary") or {}).get("tsr"),
                "prev_tsr": (r.get("summary") or {}).get("prev_tsr"),
                "flips": len(r.get("flips") or []),
                "by_domain": (r.get("summary") or {}).get("by_domain"),
                "flip_rows": r.get("flips"),
            }
            for r in reports
        ],
        indent=2,
        default=str,
    ))


if __name__ == "__main__":
    main()
