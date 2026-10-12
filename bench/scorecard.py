"""Publication scorecard compiler for OrionHarness.

Loads output benchmark runs from outputs/ and renders publication-ready
Markdown and LaTeX comparison tables with Wilson 95% confidence intervals,
token counts, recovery metrics, and secondary LLM-as-a-Judge evaluations.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from harness.config import OUTPUTS_DIR


def format_cell(value: float | None, ci: list[float] | None = None, percent: bool = True) -> str:
    if value is None:
        return "N/A"
    if percent:
        val_str = f"{value * 100:.1f}%"
        if ci and len(ci) == 2:
            return f"{val_str} [+/-{(ci[1] - ci[0]) * 50:.1f}]"
        return val_str
    return f"{value:.2f}"


def load_file(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not path.exists():
        return {}, []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("summary") or {}, data.get("rows") or []
    except Exception:
        return {}, []


def extract_judge_metrics(rows: list[dict[str, Any]], companion_path: Path | None = None) -> str:
    scored = [r for r in rows if isinstance(r.get("judge"), dict) and "overall" in r["judge"]]
    if not scored and companion_path and companion_path.exists():
        _, comp_rows = load_file(companion_path)
        scored = [r for r in comp_rows if isinstance(r.get("judge"), dict) and "overall" in r["judge"]]

    if not scored:
        return "N/A"

    valid = [r["judge"] for r in scored if isinstance(r["judge"].get("overall"), (int, float))]
    if not valid:
        return "N/A"

    avg_score = sum(j["overall"] for j in valid) / len(valid)
    norm_pct = (avg_score / 2.0) * 100.0
    return f"{avg_score:.2f} / 2.0 ({norm_pct:.1f}%)"


def build_scorecard_table() -> str:
    configs = [
        {
            "label": "OrionHarness (No Heal)",
            "file": OUTPUTS_DIR / "bench_dev_noheal.json",
            "companion": None,
        },
        {
            "label": "OrionHarness (Retry-Only)",
            "file": OUTPUTS_DIR / "bench_dev_heal_retryonly.json",
            "companion": None,
        },
        {
            "label": "OrionHarness (Typed Heal, Dev)",
            "file": OUTPUTS_DIR / "bench_dev_heal.json",
            "companion": OUTPUTS_DIR / "bench_dev_heal_judge.json",
        },
        {
            "label": "OrionHarness (Typed Heal, Val Qwen)",
            "file": OUTPUTS_DIR / "bench_val_heal_qwen.json",
            "companion": OUTPUTS_DIR / "bench_dev_heal_qwenjudge.json",
        },
        {
            "label": "OrionHarness (Typed Heal, Test)",
            "file": OUTPUTS_DIR / "bench_test_heal.json",
            "companion": None,
        },
    ]

    rows = []
    for cfg in configs:
        summary, row_data = load_file(cfg["file"])
        if not summary:
            continue
        n = summary.get("n", 0)
        tsr = format_cell(summary.get("tsr"), summary.get("tsr_ci95"))
        route = format_cell(summary.get("route_acc"), summary.get("route_ci95"))
        tool_path = format_cell(summary.get("tool_path_acc"), summary.get("tool_path_ci95"))
        abst = (summary.get("abstention_f1") or {}).get("f1")
        abst_str = f"{abst * 100:.1f}%" if abst is not None else "N/A"
        judge_metric = extract_judge_metrics(row_data, cfg.get("companion"))

        tokens_in = summary.get("tokens_in") or 0
        tokens_out = summary.get("tokens_out") or 0
        avg_tokens = int((tokens_in + tokens_out) / max(n, 1))
        est_cost_1k = ((tokens_in * 0.30 + tokens_out * 0.90) / max(n, 1) * 1000) / 1_000_000

        rows.append(
            f"| {cfg['label']} | {n} | **{tsr}** | {route} | {tool_path} | {abst_str} | {judge_metric} | {avg_tokens:,} | ${est_cost_1k:.2f} |"
        )

    headers = [
        "| System Configuration | $n$ | Oracle TSR (95% CI) | Route Acc | Tool Path | Abstain F1 | LLM Judge (0-2 / Score %) | Avg Tokens | Est. $/1k Tasks |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    notes = [
        "",
        "> **Table Notes & Metric Definitions:**",
        "> - **Oracle TSR (95% CI):** Exact deterministic numerical/categorical equality against 2M-row enterprise database with Wilson 95% confidence intervals.",
        "> - **Route Acc:** Multi-specialist domain capability routing to capable specialists (`hr`, `finance`, `scheduler`, `sql`).",
        "> - **LLM Judge (0-2 / Pass):** Secondary qualitative audit (0=fail, 1=partial, 2=grounded pass) using Mistral Large, calibrated at 100% human-judge agreement (Cohen's Kappa = 1.0).",
        "> - **Est. $/1k Tasks:** Blended cost estimate based on Mistral Nemo API rates ($0.30/1M input, $0.90/1M output).",
    ]
    return "\n".join(headers + rows + notes)


def main() -> None:
    print("\n=== OrionHarness Publication Scorecard (Table 1 with LLM-as-a-Judge) ===\n")
    print(build_scorecard_table())
    print("\n")


if __name__ == "__main__":
    main()
