"""Compile oracle tasks from frozen tables. Gold always comes from pandas, never from an LLM."""

from __future__ import annotations

import hashlib
import json
import random
import re
from pathlib import Path

from harness.config import TASKS_DIR, ensure_dirs
from retrieval.tables import load_table, unique_calendar_events

_CAL_COUNT_Q = re.compile(r"How many events does (.+) have on (\d{4}-\d{2}-\d{2})\?")


def calendar_event_gold(df, organizer: str, date: str) -> int:
    """Unique meetings for organizer+date, ignoring cloned event_id rows."""
    if "organizer" not in df.columns or "start_time" not in df.columns:
        return 0
    mask = df["organizer"].eq(organizer) & df["start_time"].astype(str).str.startswith(date)
    return int(len(unique_calendar_events(df.loc[mask])))


SPLIT_LOCK = TASKS_DIR / "SPLIT_LOCK.json"


def _tid(seed: str) -> str:
    return "t_" + hashlib.sha1(seed.encode()).hexdigest()[:12]


def _split(i: int, n: int) -> str:
    # 50% test, 25% val, 25% dev — test is locked and must not be used for tuning.
    r = i / max(n, 1)
    if r < 0.50:
        return "test"
    if r < 0.75:
        return "val"
    return "dev"


def compile_tasks(seed: int = 42, target_n: int = 600) -> list[dict]:
    """Compiles a rich, authentic multi-domain benchmark grounded in 2M enterprise rows."""
    rng = random.Random(seed)
    tasks: list[dict] = []
    
    # 1. HR Domain (2M rows) -> ~150 tasks
    tasks.extend(_hr_tasks(rng, 150))
    # 2. Financial Domain (AAPL SEC 10-K/Q) -> ~100 tasks
    tasks.extend(_finance_tasks(rng, 100))
    # 3. FRED Macroeconomic Domain (CPI, FedFunds, Sentiment) -> ~100 tasks
    tasks.extend(_fred_tasks(rng, 100))
    # 4. Product Catalog Analytics (12k products) -> ~75 tasks
    tasks.extend(_product_tasks(rng, 75))
    # 5. Calendar Scheduling (4k events) -> ~75 tasks
    tasks.extend(_calendar_tasks(rng, 75))
    # 6. Cross-Source Legal Joins -> ~35 tasks
    tasks.extend(_cross_legal(rng, 35))
    # 7. Cross-Source Illegal Joins (Governance Guardrails) -> ~35 tasks
    tasks.extend(_cross_illegal(rng, 35))
    # 8. Unanswerable Lookups & Honest Abstention -> ~35 tasks
    tasks.extend(_abstain(rng, 35))

    n = len(tasks)
    rng.shuffle(tasks)
    for i, task in enumerate(tasks):
        task["split"] = _split(i, n)
        task["id"] = _tid(f"{i}|{task.get('query')}|{task.get('gold')}|{task.get('family')}|{task.get('split')}")
    return tasks


def _hr_tasks(rng: random.Random, n: int) -> list[dict]:
    df = load_table("hr")
    depts = [d for d in df["Department"].dropna().unique().tolist() if d]
    statuses = [s for s in df["Status"].dropna().unique().tolist() if s]
    modes = [m for m in df["Work_Mode"].dropna().unique().tolist() if m] if "Work_Mode" in df.columns else ["Unknown"]
    locs = [loc for loc in df["Location"].dropna().unique().tolist() if loc] if "Location" in df.columns else []
    out = []

    while len(out) < n:
        variant = rng.randint(1, 4)
        dept = rng.choice(depts)

        if variant == 1:
            # Count by Dept + Status + Work_Mode
            status = rng.choice(statuses)
            mode = rng.choice(modes)
            mask = (df["Department"] == dept) & (df["Status"] == status)
            if "Work_Mode" in df.columns:
                mask = mask & (df["Work_Mode"] == mode)
            gold = int(mask.sum())
            out.append(
                {
                    "domain": "hr",
                    "entity": "MNC",
                    "expected_agent": "hr",
                    "expected_agents": ["hr", "sql"],
                    "grader_type": "exact_int",
                    "query": f"How many {status} employees work in {dept} under {mode}?",
                    "gold": gold,
                    "family": "intra-entity",
                }
            )
        elif variant == 2:
            # Average Salary by Dept + Job_Title
            title_vals = df.loc[df["Department"] == dept, "Job_Title"].dropna().unique().tolist()
            if title_vals and "Salary_INR" in df.columns:
                title = rng.choice(title_vals)
                mask = (df["Department"] == dept) & (df["Job_Title"] == title)
                series = df.loc[mask, "Salary_INR"]
                if len(series):
                    out.append(
                        {
                            "domain": "hr",
                            "entity": "MNC",
                            "expected_agent": "hr",
                            "expected_agents": ["hr", "sql"],
                            "grader_type": "numeric_rel",
                            "query": f"What is the average Salary_INR for {title} in {dept}?",
                            "gold": float(series.mean()),
                            "family": "intra-entity",
                        }
                    )
        elif variant == 3 and locs:
            # Headcount by Location and Department
            loc = rng.choice(locs)
            gold = int(((df["Department"] == dept) & (df["Location"] == loc)).sum())
            out.append(
                {
                    "domain": "hr",
                    "entity": "MNC",
                    "expected_agent": "hr",
                    "expected_agents": ["hr", "sql"],
                    "grader_type": "exact_int",
                    "query": f"How many employees are in {dept} located in {loc}?",
                    "gold": gold,
                    "family": "intra-entity",
                }
            )
        else:
            # Average experience years by department
            if "Experience_Years" in df.columns:
                exp_series = df.loc[df["Department"] == dept, "Experience_Years"].dropna()
                if len(exp_series):
                    out.append(
                        {
                            "domain": "hr",
                            "entity": "MNC",
                            "expected_agent": "hr",
                            "expected_agents": ["hr", "sql"],
                            "grader_type": "numeric_rel",
                            "query": f"What is the average Experience_Years for employees in {dept}?",
                            "gold": round(float(exp_series.mean()), 2),
                            "family": "intra-entity",
                        }
                    )

    return out[:n]


def _finance_tasks(rng: random.Random, n: int) -> list[dict]:
    df = load_table("applfin")
    out = []
    rows = df.to_dict(orient="records")
    metrics = [
        ("NetSales_Total", "NetSales_Total in", "exact_int"),
        ("Gross_margin", "Gross_margin in", "exact_int"),
        ("Operating_income", "Operating_income in", "exact_int"),
        ("Net_income", "Net_income in", "exact_int"),
        ("NetSales_Products", "NetSales_Products in", "exact_int"),
        ("NetSales_Services", "NetSales_Services in", "exact_int"),
    ]

    while len(out) < n:
        row = rng.choice(rows)
        year = row.get("Year")
        q = row.get("Quarter")
        metric_col, phrase, grader = rng.choice(metrics)
        gold = row.get(metric_col)
        if gold is None or (isinstance(gold, float) and (gold != gold)):
            continue
        out.append(
            {
                "domain": "finance",
                "entity": "AAPL",
                "expected_agent": "finance",
                "expected_agents": ["finance", "sql"],
                "grader_type": grader,
                "query": f"What was Apple {phrase} {year} Q{q}?",
                "gold": int(gold),
                "family": "intra-entity",
            }
        )
    return out[:n]


def _fred_tasks(rng: random.Random, n: int) -> list[dict]:
    out = []
    # CPI
    cpi_df = load_table("fred_cpi").dropna()
    cpi_rows = cpi_df.to_dict(orient="records")
    rng.shuffle(cpi_rows)
    for row in cpi_rows[: n // 2]:
        date_val = row.get("DATE") or row.get("date")
        val = row.get("CPIAUCSL")
        if date_val and val is not None:
            out.append(
                {
                    "domain": "finance",
                    "entity": "FRED",
                    "expected_agent": "finance",
                    "expected_agents": ["finance", "sql"],
                    "grader_type": "numeric_rel",
                    "query": f"What was US CPI (CPIAUCSL) on {date_val}?",
                    "gold": float(val),
                    "family": "intra-entity",
                }
            )

    # FedFunds
    try:
        ff_df = load_table("fred_fedfunds").dropna()
        ff_rows = ff_df.to_dict(orient="records")
        rng.shuffle(ff_rows)
        for row in ff_rows[: n // 4]:
            date_val = row.get("DATE") or row.get("date")
            val = row.get("DFF")
            if date_val and val is not None:
                out.append(
                    {
                        "domain": "finance",
                        "entity": "FRED",
                        "expected_agent": "finance",
                        "expected_agents": ["finance", "sql"],
                        "grader_type": "numeric_rel",
                        "query": f"What was the Federal Funds Rate (DFF) on {date_val}?",
                        "gold": float(val),
                        "family": "intra-entity",
                    }
                )
    except Exception:
        pass

    # Sentiment
    try:
        sent_df = load_table("fred_umcsent").dropna()
        sent_rows = sent_df.to_dict(orient="records")
        rng.shuffle(sent_rows)
        for row in sent_rows[: n - len(out)]:
            date_val = row.get("DATE") or row.get("date")
            val = row.get("UMCSENT")
            if date_val and val is not None:
                out.append(
                    {
                        "domain": "finance",
                        "entity": "FRED",
                        "expected_agent": "finance",
                        "expected_agents": ["finance", "sql"],
                        "grader_type": "numeric_rel",
                        "query": f"What was Consumer Sentiment (UMCSENT) on {date_val}?",
                        "gold": float(val),
                        "family": "intra-entity",
                    }
                )
    except Exception:
        pass

    return out[:n]


def _product_tasks(rng: random.Random, n: int) -> list[dict]:
    try:
        df = load_table("products")
    except Exception:
        return []
    if df.empty or "category" not in df.columns:
        return []
    cats = [c for c in df["category"].dropna().unique().tolist() if c]
    out = []

    while len(out) < n:
        cat = rng.choice(cats)
        variant = rng.choice(["count", "avg_price"])
        if variant == "count":
            gold = int(df["category"].astype(str).str.contains(str(cat), case=False, na=False).sum())
            out.append(
                {
                    "domain": "finance",
                    "entity": "AAPL",
                    "expected_agent": "finance",
                    "expected_agents": ["finance", "sql"],
                    "grader_type": "exact_int",
                    "query": f"How many Apple catalog rows are in category {cat}?",
                    "gold": gold,
                    "family": "intra-entity",
                }
            )
        else:
            cat_df = df[df["category"].str.lower() == cat.lower()]
            if not cat_df.empty and "price" in cat_df.columns:
                mean_p = float(cat_df["price"].mean())
                out.append(
                    {
                        "domain": "finance",
                        "entity": "AAPL",
                        "expected_agent": "finance",
                        "expected_agents": ["finance", "sql"],
                        "grader_type": "numeric_rel",
                        "query": f"What is the average price of products in category {cat}?",
                        "gold": round(mean_p, 2),
                        "family": "intra-entity",
                    }
                )
    return out[:n]


def _calendar_tasks(rng: random.Random, n: int) -> list[dict]:
    try:
        df = load_table("calendar")
    except Exception:
        return []
    out = []
    if "organizer" not in df.columns or "start_time" not in df.columns:
        return []
    organizers = [o for o in df["organizer"].dropna().unique().tolist() if o]
    if not organizers:
        return []
    for _ in range(n):
        org = rng.choice(organizers)
        sample = df[df["organizer"] == org]
        if sample.empty:
            continue
        date = str(sample.iloc[0]["start_time"])[:10]
        gold = calendar_event_gold(df, org, date)
        out.append(
            {
                "domain": "scheduler",
                "entity": "SYNTH_CAL",
                "expected_agent": "scheduler",
                "expected_agents": ["scheduler", "sql"],
                "grader_type": "exact_int",
                "query": f"How many events does {org} have on {date}?",
                "gold": gold,
                "family": "intra-entity",
            }
        )
    return out[:n]


def _cross_legal(rng: random.Random, n: int) -> list[dict]:
    appl = load_table("applfin")
    cpi = load_table("fred_cpi")
    out = []
    if appl.empty or cpi.empty:
        return out
    for _ in range(n):
        row = appl.iloc[rng.randrange(len(appl))]
        cpi_row = cpi.iloc[rng.randrange(len(cpi))]
        date_col = "DATE" if "DATE" in cpi.columns else cpi.columns[0]
        val_col = "CPIAUCSL" if "CPIAUCSL" in cpi.columns else cpi.columns[-1]
        out.append(
            {
                "domain": "cross",
                "entity": "AAPL+FRED",
                "expected_agent": "finance",
                "expected_agents": ["hr", "finance", "scheduler", "sql"],
                "grader_type": "numeric_rel",
                "query": (
                    f"Report Apple NetSales_Total for {row.get('Year')} Q{row.get('Quarter')} "
                    f"and US CPI on {cpi_row[date_col]}."
                ),
                "gold": float(row.get("NetSales_Total") or 0),
                "gold_secondary": float(cpi_row[val_col]),
                "family": "cross-source-legal",
            }
        )
    return out[:n]


def _cross_illegal(rng: random.Random, n: int) -> list[dict]:
    queries = [
        "Compare MSFT 10-K revenue with MNC payroll in IT and give a single company total.",
        "Calculate the combined total of MNC sales headcount and Apple quarterly NetSales.",
        "Merge MNC employee performance ratings with FRED CPI macro index into one figure.",
        "Combine Microsoft EDGAR filing operating expenses with synthetic Apple calendar meeting counts.",
        "Calculate the average of Apple NetSales and MNC engineering salaries as one single metric.",
        "Add together Apple iPhone product revenue and MNC HR director salaries.",
        "Compute the sum of FRED Federal Funds rate and Apple employee count.",
    ]
    out = []
    for i in range(n):
        q = queries[i % len(queries)]
        out.append(
            {
                "domain": "cross",
                "entity": "MSFT+MNC",
                "expected_agent": None,
                "expected_agents": [],
                "grader_type": "abstain",
                "query": q,
                "gold": "ABSTAIN",
                "family": "cross-source-illegal",
            }
        )
    return out


def _abstain(rng: random.Random, n: int) -> list[dict]:
    items = [
        {"query": "What is the salary of EMP9999999?", "domain": "hr", "entity": "MNC", "expected_agents": ["hr", "sql"]},
        {"query": "What is the performance rating of EMP8888888?", "domain": "hr", "entity": "MNC", "expected_agents": ["hr", "sql"]},
        {"query": "What is the hire date of EMP0000000?", "domain": "hr", "entity": "MNC", "expected_agents": ["hr", "sql"]},
        {"query": "How many employees work in Department QuantumTeleportation under Remote?", "domain": "hr", "entity": "MNC", "expected_agents": ["hr", "sql"]},
        {"query": "What is the average salary in Department NeuralLace?", "domain": "hr", "entity": "MNC", "expected_agents": ["hr", "sql"]},
        {"query": "What was Apple revenue in year 1800?", "domain": "finance", "entity": "AAPL", "expected_agents": ["finance", "sql"]},
        {"query": "What was Apple NetSales_Total in 1950 Q1?", "domain": "finance", "entity": "AAPL", "expected_agents": ["finance", "sql"]},
        {"query": "What was Apple Gross_margin in year 1920?", "domain": "finance", "entity": "AAPL", "expected_agents": ["finance", "sql"]},
        {"query": "Who attends the board meeting on 1900-01-01?", "domain": "scheduler", "entity": "SYNTH_CAL", "expected_agents": ["scheduler", "sql"]},
        {"query": "How many events does NonExistentOrganizerXYZ have on 2024-05-15?", "domain": "scheduler", "entity": "SYNTH_CAL", "expected_agents": ["scheduler", "sql"]},
        {"query": "What was US CPI (CPIAUCSL) on 1776-07-04?", "domain": "finance", "entity": "FRED", "expected_agents": ["finance", "sql"]},
        {"query": "What was the Federal Funds Rate on 1850-01-01?", "domain": "finance", "entity": "FRED", "expected_agents": ["finance", "sql"]},
    ]
    out = []
    for i in range(n):
        item = items[i % len(items)]
        out.append(
            {
                "domain": item["domain"],
                "entity": item["entity"],
                "expected_agent": item["expected_agents"][0] if item["expected_agents"] else None,
                "expected_agents": item["expected_agents"],
                "grader_type": "abstain",
                "query": item["query"],
                "gold": "ABSTAIN",
                "family": "unanswerable",
            }
        )
    return out


def load_split(split: str) -> list[dict]:
    dest = TASKS_DIR / "orion_bench.jsonl"
    if not dest.exists():
        compile_tasks()
    tasks = []
    with dest.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                t = json.loads(line)
                if t.get("split") == split:
                    tasks.append(t)
    return tasks


def main() -> None:
    ensure_dirs()
    tasks = compile_tasks(seed=42, target_n=600)
    dest = TASKS_DIR / "orion_bench.jsonl"
    with dest.open("w", encoding="utf-8") as fh:
        for t in tasks:
            fh.write(json.dumps(t) + "\n")
    by_split: dict[str, int] = {}
    for t in tasks:
        by_split[t["split"]] = by_split.get(t["split"], 0) + 1
    sha = hashlib.sha256(dest.read_bytes()).hexdigest()
    lock = {
        "path": str(dest),
        "n": len(tasks),
        "by_split": by_split,
        "sha256": sha,
    }
    SPLIT_LOCK.write_text(json.dumps(lock, indent=2), encoding="utf-8")
    print(f"wrote {len(tasks)} tasks to {dest}")
    print(json.dumps(lock, indent=2))


if __name__ == "__main__":
    main()
