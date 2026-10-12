"""Score saved bench traces with the configured judge. Worker is not rerun.

Usage:
  python -m bench.rejudge outputs/bench_dev_heal.json --tag qwenjudge
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from bench.run import judge_payload, split_summary_from_rows
from harness.config import OUTPUTS_DIR, get_settings
from harness.context import new_context
from harness.runtime import build_runtime
from harness.trace import get_tracer


def rejudge_file(path: Path, *, tag: str) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("rows") or []
    prev = data.get("summary") or {}
    if any("trials" in r for r in rows):
        return {"out": str(path), "skipped": "passk file"}
    build_runtime()
    tracer = get_tracer()
    settings = get_settings()
    t0 = time.time()
    overall2 = 0
    missing = 0
    for i, row in enumerate(rows, start=1):
        request_id = str(row.get("request_id") or "")
        spans = tracer.spans_for(request_id) if request_id else []
        if not spans:
            missing += 1
        from bench.eval_agent import score_run

        ctx = new_context(user="eval", request_id=request_id or f"rejudge-{i}")
        summary, evidence = judge_payload(str(row.get("query") or ""), str(row.get("answer") or ""), spans)
        try:
            card = score_run(
                ctx,
                str(row.get("query") or ""),
                str(row.get("answer") or ""),
                evidence=evidence,
                spans_summary=summary,
            )
            judged = card.to_dict()
        except Exception as exc:
            judged = {"error": str(exc)[:300], "overall": 0}
        judged["trace_missing"] = not bool(spans)
        row["judge"] = judged
        if int(judged.get("overall") or 0) == 2:
            overall2 += 1
        print(
            f"rejudge {i}/{len(rows)} oracle_ok={row.get('oracle_ok')} judge={judged.get('overall')} missing={not spans}",
            flush=True,
        )
    elapsed = time.time() - t0
    summary = split_summary_from_rows(
        rows,
        split=str(prev.get("split") or "dev"),
        heal=bool(prev.get("heal")),
        inject=prev.get("inject"),
        judge=True,
        seconds=float(prev.get("seconds") or 0) + elapsed,
        retry_only=bool(prev.get("retry_only")),
    )
    summary["rejudged"] = True
    summary["rejudge_seconds"] = elapsed
    summary["judge_overall2"] = overall2 / max(len(rows), 1)
    summary["judge_trace_missing"] = missing
    summary["judge_provider"] = settings.judge_provider
    summary["judge_model"] = settings.tiers.judge
    summary["source"] = str(path)
    suffix = tag.strip() or "rejudge"
    stem = path.stem
    out = OUTPUTS_DIR / f"{stem}_{suffix}.json"
    summary["out"] = str(out)
    out.write_text(json.dumps({"summary": summary, "rows": rows}, indent=2, default=str), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-judge saved bench JSON with ORION_JUDGE_*.")
    parser.add_argument("path", type=Path, nargs="?", default=OUTPUTS_DIR / "bench_dev_heal.json")
    parser.add_argument("--tag", default="rejudge", help="output suffix (e.g. qwenjudge)")
    args = parser.parse_args()
    path = args.path.resolve()
    if not path.exists():
        raise SystemExit(f"missing {path}")
    print(json.dumps(rejudge_file(path, tag=args.tag), indent=2, default=str))


if __name__ == "__main__":
    main()
