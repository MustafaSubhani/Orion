"""Execution sandbox: spec-only pandas plus AST-gated generated code.

Contract:
- No eval() of free Python from workers.
- Generated code cannot import os/subprocess/socket or read env.
- Timeouts kill only the sandbox process.
"""

from __future__ import annotations

import hashlib
import json
import operator
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from harness.config import PROJECT_ROOT
from harness.errors import SandboxError, SchemaInvalidError
from harness.permissions import SALARY_COLUMNS
from retrieval.tables import load_table, table_entity
from sandbox.gate import static_gate

ALLOWED_OPS = {
    "eq": operator.eq,
    "ne": operator.ne,
    "gt": operator.gt,
    "gte": operator.ge,
    "lt": operator.lt,
    "lte": operator.le,
    "in": lambda s, v: s.isin(v if isinstance(v, (list, tuple, set)) else [v]),
    "contains": lambda s, v: s.astype(str).str.contains(str(v), case=False, na=False),
}
ALLOWED_AGGS = {"count", "sum", "mean", "min", "max", "std", "nunique", "size"}


def execute_sql_spec(spec: dict[str, Any], *, role: Optional[str] = None, allow_kb: bool = False) -> dict[str, Any]:
    """Allow-listed aggregations. Unknown columns raise SchemaInvalidError (F4)."""
    if not isinstance(spec, dict):
        raise SchemaInvalidError("sql_spec must be an object")
    table = spec.get("table")
    if not isinstance(table, str):
        raise SchemaInvalidError("sql_spec.table is required")
    if table.startswith("kb_") and not allow_kb:
        raise SchemaInvalidError(f"table '{table}' is a live KB upload. Use kb_query, not sql_spec.")
    if allow_kb and not table.startswith("kb_"):
        raise SchemaInvalidError("kb_query only accepts kb_* tables from the live catalog")
    try:
        df = load_table(table)
    except SchemaInvalidError:
        raise
    except Exception as exc:
        raise SandboxError(f"failed to load table '{table}'", details=str(exc)) from exc

    if role == "limited":
        drop = [c for c in df.columns if c in SALARY_COLUMNS]
        if drop:
            df = df.drop(columns=drop)

    return run_spec_on_frame(df, spec, table=table, entity=table_entity(table))


def run_spec_on_frame(df: pd.DataFrame, spec: dict[str, Any], *, table: str, entity: str) -> dict[str, Any]:
    """Apply an allow-listed spec (filters, group_by, agg, sort, limit) to a DataFrame.

    Shared by frozen tables and file connectors. Unknown columns raise SchemaInvalidError (F4).
    """
    orphan = spec.get("column")
    if isinstance(orphan, str) and orphan.strip():
        if orphan not in df.columns:
            raise SchemaInvalidError(f"unknown column '{orphan}'. available: {list(df.columns)}")

    filters = spec.get("filters") or []
    if not isinstance(filters, list):
        raise SchemaInvalidError("filters must be a list")
    for flt in filters:
        if not isinstance(flt, dict):
            raise SchemaInvalidError("each filter must be an object")
        col = flt.get("column")
        op = flt.get("op", "eq")
        value = flt.get("value")
        if col not in df.columns:
            raise SchemaInvalidError(f"unknown column '{col}'. available: {list(df.columns)}")
        if op not in ALLOWED_OPS:
            raise SchemaInvalidError(f"unknown op '{op}'. allowed: {sorted(ALLOWED_OPS)}")
        try:
            df = df[ALLOWED_OPS[op](df[col], value)]
        except Exception as exc:
            raise SchemaInvalidError(f"filter failed on {col}: {exc}") from exc

    group_by = spec.get("group_by") or []
    agg = spec.get("agg") or {}
    if group_by:
        missing = [c for c in group_by if c not in df.columns]
        if missing:
            raise SchemaInvalidError(f"unknown group_by columns: {missing}")
        if not agg:
            result = df.groupby(list(group_by), dropna=False).size().reset_index(name="count")
        else:
            named = {}
            for col, fn in agg.items():
                if col not in df.columns:
                    raise SchemaInvalidError(f"unknown agg column '{col}'")
                if fn not in ALLOWED_AGGS:
                    raise SchemaInvalidError(f"unknown agg '{fn}'")
                named[col] = "size" if fn == "count" else fn
            result = df.groupby(list(group_by), dropna=False).agg(named).reset_index()
    elif agg:
        data = {}
        for col, fn in agg.items():
            if col not in df.columns:
                raise SchemaInvalidError(f"unknown agg column '{col}'")
            if fn not in ALLOWED_AGGS:
                raise SchemaInvalidError(f"unknown agg '{fn}'")
            series = df[col]
            if fn in {"count", "size"}:
                data[f"{col}_{fn}"] = [int(series.count())]
            else:
                data[f"{col}_{fn}"] = [getattr(series, fn)()]
        result = pd.DataFrame(data)
    else:
        cols = spec.get("columns")
        if cols:
            missing = [c for c in cols if c not in df.columns]
            if missing:
                raise SchemaInvalidError(f"unknown columns: {missing}")
            result = df[list(cols)]
        else:
            result = df

    sort = spec.get("sort")
    if sort and isinstance(sort, dict):
        col = sort.get("column")
        if col in result.columns:
            result = result.sort_values(col, ascending=bool(sort.get("ascending", False)))

    limit = int(spec.get("limit") or 50)
    limit = max(1, min(limit, 200))
    result = result.head(limit)
    return result_payload(result, table=table, entity=entity)


def result_payload(result: pd.DataFrame, *, table: str, entity: str) -> dict[str, Any]:
    """Uniform tool payload (records, preview, hash) for any table-shaped result."""
    records = json.loads(result.to_json(orient="records", date_format="iso"))
    if result.empty:
        preview = "(empty)"
    else:
        try:
            preview = result.head(20).to_markdown(index=False)
        except Exception:
            preview = result.head(20).to_string(index=False)
    blob = json.dumps(records, default=str, sort_keys=True)
    return {
        "ok": True,
        "table": table,
        "entity": entity,
        "rows": len(result),
        "columns": list(result.columns),
        "records": records,
        "preview": preview,
        "result_hash": hashlib.sha256(blob.encode()).hexdigest()[:16],
        "empty": bool(result.empty),
    }


def run_generated_agent(
    module_path: Path,
    task: str,
    timeout_s: float = 30.0,
    *,
    memory_mb: Optional[int] = None,
    cpu_s: Optional[float] = None,
) -> str:
    """Run generated agent.py in a subprocess with OS-enforced limits, a minimal environment and no network.

    Limits default to the admin settings (`sandbox.memory_mb`, `sandbox.cpu_s`, `sandbox.wall_s`).
    The wall clock is the smaller of `timeout_s` and `sandbox.wall_s`.
    """
    from harness.settings_store import effective
    from sandbox.limits import minimal_env, python_executable, run_limited

    gate = static_gate(module_path)
    if not gate.ok:
        raise SandboxError(f"static gate failed: {gate.reason}")
    runner = Path(__file__).with_name("worker_loop.py")
    wall = min(float(timeout_s), float(effective("sandbox.wall_s")))
    try:
        code, out, err, timed_out = run_limited(
            [python_executable(), str(runner), str(module_path), task],
            env=minimal_env(PROJECT_ROOT),
            cwd=str(PROJECT_ROOT),
            memory_mb=int(memory_mb or effective("sandbox.memory_mb")),
            cpu_s=float(cpu_s or effective("sandbox.cpu_s")),
            wall_s=wall,
        )
    except RuntimeError as exc:
        raise SandboxError(str(exc)) from exc
    if timed_out:
        raise SandboxError(f"generated agent timed out after {wall:g}s")
    if code != 0:
        raise SandboxError("generated agent crashed", details=(err or out)[:800])
    return (out or "").strip()
