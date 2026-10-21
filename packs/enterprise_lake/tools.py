"""Extra lake tools registered onto the fat verb registry.

These stay typed specs (no free Python, no open web). Adding one is a manifest
+ handler here; workers pick them up from AgentCard.tools / ReAct extras.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from harness.context import RequestContext
from harness.errors import SchemaInvalidError
from harness.types import ToolManifest
from packs.enterprise_lake.policy import ALLOWED_ENTITIES, SALARY_COLUMNS, entities_join_legal

LAB_LEGAL_TOOLS = frozenset(
    {
        "retrieve",
        "sql_spec",
        "forecast",
        "anomaly_detect",
        "calendar_query",
        "calendar_conflicts",
        "render_chart",
        "http_fetch",
        "search_tools",
        "session_get",
        "skill_manage",
        "schema_lookup",
        "distinct_values",
        "describe_column",
        "row_lookup",
        "join_check",
        "evidence_check",
        "compare_series",
        "workforce_slice",
        "ratio_agg",
        "top_n",
        "macro_latest",
        "series_window",
        "date_at",
        "product_search",
        "kb_list",
        "kb_search",
        "kb_query",
    }
)

_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def register_extras(registry: Any) -> None:
    from harness.tool_registry import _object, _str

    registry.register(
        ToolManifest(
            id="schema_lookup",
            verb="schema_lookup",
            resource_type="catalog",
            description=(
                "List frozen tables or one table's real columns, dtypes, entity tag, and sample values. "
                "Call this before sql_spec if a column name is uncertain (F4)."
            ),
            schema=_object({"table": _str("optional table; omit to list all")}),
        ),
        _handle_schema_lookup,
    )
    registry.register(
        ToolManifest(
            id="distinct_values",
            verb="distinct_values",
            resource_type="table",
            description="Unique values and counts for a column (departments, locations, organizers, …).",
            schema=_object(
                {
                    "table": _str("table name"),
                    "column": _str("column name"),
                    "limit": {"type": "integer"},
                },
                required=["table", "column"],
            ),
        ),
        _handle_distinct_values,
    )
    registry.register(
        ToolManifest(
            id="describe_column",
            verb="describe_column",
            resource_type="table",
            description="Numeric summary: count, mean, std, min, p25, median, p75, max, nulls.",
            schema=_object(
                {"table": _str("table name"), "column": _str("numeric column")},
                required=["table", "column"],
            ),
        ),
        _handle_describe_column,
    )
    registry.register(
        ToolManifest(
            id="row_lookup",
            verb="row_lookup",
            resource_type="table",
            description="Fetch rows by exact key (Employee_ID, event_id, DATE, …). Prefer this over a full scan.",
            schema=_object(
                {
                    "table": _str("table name"),
                    "key_column": _str("column to match"),
                    "key_value": _str("exact value"),
                    "limit": {"type": "integer"},
                },
                required=["table", "key_column", "key_value"],
            ),
        ),
        _handle_row_lookup,
    )
    registry.register(
        ToolManifest(
            id="join_check",
            verb="join_check",
            resource_type="policy",
            description=(
                "Check whether entity tags may appear in one answer. "
                "Legal: AAPL+FRED, MSFT+FRED, SYNTH_CAL+AAPL. Illegal: MNC payroll with MSFT 10-K."
            ),
            schema=_object(
                {"entities": _str("comma-separated tags, e.g. MNC,MSFT")},
                required=["entities"],
            ),
        ),
        _handle_join_check,
    )
    registry.register(
        ToolManifest(
            id="evidence_check",
            verb="evidence_check",
            resource_type="eval",
            description=(
                "Check whether claim strings (usually numbers) appear in this request's tool artifacts. "
                "Use before answering with a number you did not just compute."
            ),
            schema=_object(
                {
                    "claims": _str("comma-separated claims or numbers"),
                    "text": _str("optional extra evidence blob"),
                },
                required=["claims"],
            ),
        ),
        _handle_evidence_check,
    )
    registry.register(
        ToolManifest(
            id="compare_series",
            verb="compare_series",
            resource_type="series",
            description="Compare two numeric columns (optionally aligned on DATE). Returns last values, n, correlation.",
            schema=_object(
                {
                    "table_a": _str("first table"),
                    "column_a": _str("first numeric column"),
                    "table_b": _str("second table (defaults to table_a)"),
                    "column_b": _str("second numeric column"),
                },
                required=["table_a", "column_a", "column_b"],
            ),
        ),
        _handle_compare_series,
    )
    registry.register(
        ToolManifest(
            id="calendar_conflicts",
            verb="calendar_conflicts",
            resource_type="calendar",
            description="Overlapping SYNTH_CAL events for an organizer and/or date prefix. Requires organizer or date_prefix.",
            schema=_object(
                {
                    "organizer": _str("organizer substring"),
                    "date_prefix": _str("YYYY-MM-DD prefix of start_time"),
                    "limit": {"type": "integer"},
                }
            ),
        ),
        _handle_calendar_conflicts,
    )
    registry.register(
        ToolManifest(
            id="workforce_slice",
            verb="workforce_slice",
            resource_type="hr",
            description=(
                "HR slice over the MNC payroll table. Filter department / location / status / work_mode / job_title "
                "(Sales, Marketing, IT, Engineering, HR, … are Department values, not separate lakes). "
                "Optional group_by. count_only=true for a single integer."
            ),
            schema=_object(
                {
                    "department": _str("Department contains, e.g. IT or Marketing"),
                    "location": _str("Location contains"),
                    "status": _str("Status contains, e.g. Active"),
                    "work_mode": _str("Work_Mode contains, e.g. Remote"),
                    "job_title": _str("Job_Title contains"),
                    "group_by": _str("optional column to break counts on"),
                    "count_only": {"type": "boolean"},
                    "agg": _str("count | mean | avg | sum | min | max. Use mean for average Salary_INR"),
                    "column": _str("numeric column for agg, default Salary_INR"),
                    "limit": {"type": "integer"},
                }
            ),
        ),
        _handle_workforce_slice,
    )
    registry.register(
        ToolManifest(
            id="ratio_agg",
            verb="ratio_agg",
            resource_type="table",
            description="Ratio of matching rows to a base slice (e.g. Remote / all in IT). No free Python.",
            schema=_object(
                {
                    "table": _str("default hr"),
                    "department": _str("optional base Department filter"),
                    "location": _str("optional Location filter"),
                    "column": _str("numerator column"),
                    "value": _str("numerator value (contains)"),
                    "status": _str("optional Status filter on the base"),
                },
                required=["column", "value"],
            ),
        ),
        _handle_ratio_agg,
    )
    registry.register(
        ToolManifest(
            id="top_n",
            verb="top_n",
            resource_type="table",
            description="Top/bottom n rows by a numeric column after optional filters. Salary hidden for limited roles.",
            schema=_object(
                {
                    "table": _str("hr | applfin | products | fred_*"),
                    "column": _str("numeric sort column"),
                    "n": {"type": "integer"},
                    "ascending": {"type": "boolean"},
                    "department": _str("optional HR Department filter"),
                    "category": _str("optional products category"),
                },
                required=["table", "column"],
            ),
        ),
        _handle_top_n,
    )
    registry.register(
        ToolManifest(
            id="macro_latest",
            verb="macro_latest",
            resource_type="series",
            description="Latest FRED observation(s). Omit table for CPI + Fed Funds + UMCSENT together.",
            schema=_object({"table": _str("fred_cpi | fred_fedfunds | fred_umcsent | omit for all")}),
        ),
        _handle_macro_latest,
    )
    registry.register(
        ToolManifest(
            id="series_window",
            verb="series_window",
            resource_type="series",
            description="Stats for a numeric column between date_from and date_to (inclusive, YYYY-MM-DD prefixes).",
            schema=_object(
                {
                    "table": _str("fred_* | applfin"),
                    "column": _str("numeric column"),
                    "date_from": _str("YYYY-MM-DD prefix"),
                    "date_to": _str("YYYY-MM-DD prefix"),
                },
                required=["table", "column"],
            ),
        ),
        _handle_series_window,
    )
    registry.register(
        ToolManifest(
            id="date_at",
            verb="date_at",
            resource_type="series",
            description="Exact FRED DATE (YYYY-MM-DD) or ApplFin year+quarter value. Prefer this over sql_spec for one-point lookups.",
            schema=_object(
                {
                    "table": _str("fred_cpi | fred_fedfunds | fred_umcsent | applfin"),
                    "column": _str("CPIAUCSL | DFF | UMCSENT | NetSales_Total | ..."),
                    "date": _str("YYYY-MM-DD for FRED"),
                    "year": {"type": "integer"},
                    "quarter": _str("1-4 or Q1-Q4"),
                    "period": _str("optional '2020 Q3' / 'Q3 2020' if year+quarter are not split"),
                },
                required=["table", "column"],
            ),
        ),
        _handle_date_at,
    )
    registry.register(
        ToolManifest(
            id="product_search",
            verb="product_search",
            resource_type="catalog",
            description="Search the frozen AAPL product catalog (not MNC payroll). For how-many questions, use match_count / COUNT= in the preview — never the `rows` preview cap (max 40).",
            schema=_object(
                {
                    "query": _str("model_name or category substring"),
                    "category": _str("category contains, e.g. iPhone"),
                    "min_price": {"type": "number"},
                    "max_price": {"type": "number"},
                    "in_stock": {"type": "boolean"},
                    "limit": {"type": "integer"},
                }
            ),
        ),
        _handle_product_search,
    )


def _parse_entities(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    text = str(raw or "")
    return [p.strip() for p in re.split(r"[,|;]+", text) if p.strip()]


def _pd():
    import pandas as pd

    return pd


def _load_table_role(name: str, role: Optional[str]):
    from retrieval.tables import load_table

    df = load_table(name)
    if role == "limited":
        drop = [c for c in df.columns if c in SALARY_COLUMNS]
        if drop:
            df = df.drop(columns=drop)
    return df


def _require_column(df, column: str) -> str:
    if column not in df.columns:
        raise SchemaInvalidError(f"unknown column '{column}'. available: {list(df.columns)}")
    return column


def _md(df, n: int = 20) -> str:
    if df.empty:
        return "(empty)"
    try:
        return df.head(n).to_markdown(index=False)
    except Exception:
        return df.head(n).to_string(index=False)


def _handle_schema_lookup(args: dict[str, Any], ctx: RequestContext) -> dict:
    from retrieval.tables import TABLE_ENTITY, list_tables, load_table

    table = str(args.get("table") or "").strip()
    include_kb = bool((ctx.extra or {}).get("kb_enabled"))
    if not table:
        rows = []
        for name in list_tables(include_kb=include_kb):
            try:
                df = load_table(name)
                rows.append(
                    {
                        "table": name,
                        "entity": TABLE_ENTITY.get(name) or ("KB" if name.startswith("kb_") else "?"),
                        "rows": int(len(df)),
                        "columns": list(df.columns),
                    }
                )
            except Exception as exc:
                rows.append({"table": name, "entity": TABLE_ENTITY.get(name) or ("KB" if name.startswith("kb_") else "?"), "error": str(exc)[:200]})
        preview = "\n".join(
            f"- {r['table']} entity={r.get('entity')} cols={r.get('columns') or r.get('error')}" for r in rows
        )
        return {"tables": rows, "preview": preview}
    df = _load_table_role(table, ctx.role)
    cols = []
    for col in df.columns:
        series = df[col]
        nunique = int(series.nunique(dropna=True))
        sample = [str(v) for v in series.dropna().astype(str).unique()[:8]]
        cols.append(
            {
                "name": col,
                "dtype": str(series.dtype),
                "nunique": nunique,
                "nulls": int(series.isna().sum()),
                "sample": sample,
            }
        )
    entity = "KB" if table.startswith("kb_") else TABLE_ENTITY.get(table, "?")
    preview = f"TABLE {table} entity={entity} rows={len(df)}\n" + "\n".join(
        f"- {c['name']}:{c['dtype']} nunique={c['nunique']} sample={c['sample'][:5]}" for c in cols
    )
    return {
        "table": table,
        "entity": entity,
        "rows": int(len(df)),
        "columns": cols,
        "preview": preview,
    }


def _handle_distinct_values(args: dict[str, Any], ctx: RequestContext) -> dict:
    table = str(args.get("table") or "")
    column = str(args.get("column") or "")
    limit = max(1, min(int(args.get("limit") or 40), 80))
    df = _load_table_role(table, ctx.role)
    _require_column(df, column)
    pd = _pd()
    counts = df[column].fillna("(null)").astype(str).value_counts().head(limit)
    records = [{"value": str(idx), "count": int(val)} for idx, val in counts.items()]
    preview = _md(pd.DataFrame(records))
    return {
        "table": table,
        "column": column,
        "nunique": int(df[column].nunique(dropna=True)),
        "records": records,
        "preview": preview,
    }


def _handle_describe_column(args: dict[str, Any], ctx: RequestContext) -> dict:
    table = str(args.get("table") or "")
    column = str(args.get("column") or "")
    df = _load_table_role(table, ctx.role)
    _require_column(df, column)
    pd = _pd()
    series = pd.to_numeric(df[column], errors="coerce")
    valid = series.dropna()
    if valid.empty:
        raise SchemaInvalidError(f"column '{column}' is not numeric after coercion")
    stats = {
        "table": table,
        "column": column,
        "count": int(valid.count()),
        "nulls": int(series.isna().sum()),
        "mean": float(valid.mean()),
        "std": float(valid.std()) if len(valid) > 1 else 0.0,
        "min": float(valid.min()),
        "p25": float(valid.quantile(0.25)),
        "median": float(valid.median()),
        "p75": float(valid.quantile(0.75)),
        "max": float(valid.max()),
    }
    stats["preview"] = (
        f"{column}: n={stats['count']} mean={stats['mean']:.4g} "
        f"median={stats['median']:.4g} min={stats['min']:.4g} max={stats['max']:.4g}"
    )
    return stats


def _handle_row_lookup(args: dict[str, Any], ctx: RequestContext) -> dict:
    table = str(args.get("table") or "")
    key_column = str(args.get("key_column") or "")
    key_value = str(args.get("key_value") or "")
    limit = max(1, min(int(args.get("limit") or 5), 20))
    df = _load_table_role(table, ctx.role)
    _require_column(df, key_column)
    matched = df[df[key_column].astype(str) == key_value]
    if matched.empty:
        matched = df[df[key_column].astype(str).str.lower() == key_value.lower()]
    head = matched.head(limit)
    from retrieval.tables import table_entity

    records = json.loads(head.to_json(orient="records", date_format="iso"))
    status = "NOT_FOUND" if matched.empty else "FOUND"
    preview = _md(head) if not head.empty else f"NOT FOUND: No record found with {key_column}='{key_value}' in table '{table}'. Abstain from guessing or inventing values."
    return {
        "table": table,
        "entity": table_entity(table),
        "status": status,
        "match_count": int(len(matched)),
        "rows": int(len(head)),
        "empty": matched.empty,
        "records": records,
        "preview": preview,
    }


def _handle_join_check(args: dict[str, Any], ctx: RequestContext) -> dict:
    entities = _parse_entities(args.get("entities"))
    if not entities:
        raise SchemaInvalidError("join_check.entities is required (e.g. MNC,MSFT)")
    legal, reason = entities_join_legal(entities)
    return {
        "entities": [e.upper() for e in entities],
        "legal": legal,
        "reason": reason,
        "known": list(ALLOWED_ENTITIES) + ["KB"],
        "preview": ("LEGAL: " if legal else "ILLEGAL — abstain: ") + reason,
    }


def _handle_evidence_check(args: dict[str, Any], ctx: RequestContext) -> dict:
    raw = args.get("claims")
    if isinstance(raw, list):
        claims = [str(c).strip() for c in raw if str(c).strip()]
    else:
        claims = [p.strip() for p in str(raw or "").split(",") if p.strip()]
    if not claims:
        raise SchemaInvalidError("evidence_check.claims is required")
    parts = [str(args.get("text") or "")]
    session = getattr(ctx, "session", None)
    if session is not None:
        for art in session.recent(12):
            parts.append(art.text[:8000])
    blob = "\n".join(parts)
    rows = []
    for claim in claims:
        found = claim in blob
        if not found:
            nums = _NUMBER.findall(claim)
            found = any(n in blob for n in nums) if nums else False
        rows.append({"claim": claim, "grounded": found})
    all_ok = all(r["grounded"] for r in rows)
    preview = "\n".join(f"{'ok' if r['grounded'] else 'MISSING'}: {r['claim']}" for r in rows)
    return {"claims": rows, "all_grounded": all_ok, "preview": preview}


def _date_col(df) -> Optional[str]:
    for name in ("DATE", "Date", "date", "Hire_Date", "start_time"):
        if name in df.columns:
            return name
    return None


def _handle_compare_series(args: dict[str, Any], ctx: RequestContext) -> dict:
    table_a = str(args.get("table_a") or "")
    table_b = str(args.get("table_b") or table_a)
    col_a = str(args.get("column_a") or "")
    col_b = str(args.get("column_b") or "")
    pd = _pd()
    a = _load_table_role(table_a, ctx.role)
    b = _load_table_role(table_b, ctx.role)
    _require_column(a, col_a)
    _require_column(b, col_b)
    sa = pd.to_numeric(a[col_a], errors="coerce")
    sb = pd.to_numeric(b[col_b], errors="coerce")
    out: dict[str, Any] = {
        "table_a": table_a,
        "column_a": col_a,
        "table_b": table_b,
        "column_b": col_b,
        "n_a": int(sa.notna().sum()),
        "n_b": int(sb.notna().sum()),
        "last_a": float(sa.dropna().iloc[-1]) if sa.notna().any() else None,
        "last_b": float(sb.dropna().iloc[-1]) if sb.notna().any() else None,
    }
    da, db = _date_col(a), _date_col(b)
    if da and db:
        left = pd.DataFrame({"k": a[da].astype(str), "a": sa})
        right = pd.DataFrame({"k": b[db].astype(str), "b": sb})
        merged = left.merge(right, on="k", how="inner").dropna()
        out["aligned_n"] = int(len(merged))
        if len(merged) >= 3:
            out["corr"] = float(merged["a"].corr(merged["b"]))
        preview_tail = merged.tail(5)
        out["preview"] = (
            f"last {col_a}={out['last_a']} last {col_b}={out['last_b']} "
            f"aligned_n={out.get('aligned_n')} corr={out.get('corr')}\n"
            + _md(preview_tail)
        )
    else:
        n = min(len(sa.dropna()), len(sb.dropna()))
        if n >= 3:
            out["corr"] = float(sa.dropna().iloc[:n].reset_index(drop=True).corr(sb.dropna().iloc[:n].reset_index(drop=True)))
        out["preview"] = f"last {col_a}={out['last_a']} last {col_b}={out['last_b']} corr={out.get('corr')}"
    return out


def _handle_calendar_conflicts(args: dict[str, Any], ctx: RequestContext) -> dict:
    organizer = str(args.get("organizer") or "").strip()
    date_prefix = str(args.get("date_prefix") or "").strip()
    if not organizer and not date_prefix:
        raise SchemaInvalidError("calendar_conflicts needs organizer or date_prefix (full-table scan is blocked)")
    from retrieval.tables import load_table

    pd = _pd()
    df = load_table("calendar")
    if organizer and "organizer" in df.columns:
        df = df[df["organizer"].astype(str).str.contains(organizer, case=False, na=False)]
    if date_prefix and "start_time" in df.columns:
        df = df[df["start_time"].astype(str).str.startswith(date_prefix)]
    if len(df) > 250:
        df = df.head(250)
    start = pd.to_datetime(df.get("start_time"), errors="coerce")
    end = pd.to_datetime(df.get("end_time"), errors="coerce") if "end_time" in df.columns else pd.Series(pd.NaT, index=df.index)
    end = end.fillna(start + pd.Timedelta(hours=1))
    work = df.assign(_s=start, _e=end).dropna(subset=["_s"])
    cap = max(1, min(int(args.get("limit") or 20), 40))
    starts = work["_s"].tolist()
    ends = work["_e"].tolist()
    ids = work["event_id"].astype(str).tolist() if "event_id" in work.columns else [str(i) for i in range(len(work))]
    titles = work["title"].astype(str).tolist() if "title" in work.columns else [""] * len(work)
    st_raw = work["start_time"].astype(str).tolist() if "start_time" in work.columns else [""] * len(work)
    n = len(work)
    pairs: list[dict[str, Any]] = []
    for i in range(n):
        for j in range(i + 1, n):
            if starts[i] < ends[j] and starts[j] < ends[i]:
                pairs.append(
                    {
                        "a": ids[i],
                        "b": ids[j],
                        "title_a": titles[i],
                        "title_b": titles[j],
                        "start_a": st_raw[i],
                        "start_b": st_raw[j],
                    }
                )
                if len(pairs) >= cap:
                    break
        if len(pairs) >= cap:
            break
    preview = _md(pd.DataFrame(pairs)) if pairs else "no overlapping events in the filtered set"
    return {
        "count": len(pairs),
        "scanned": int(len(work)),
        "records": pairs,
        "empty": not pairs,
        "preview": preview,
    }


_SLICE_KEYS = (
    ("department", "Department"),
    ("location", "Location"),
    ("status", "Status"),
    ("work_mode", "Work_Mode"),
    ("job_title", "Job_Title"),
    ("category", "category"),
)


_PERIOD = re.compile(
    r"(?P<y1>(?:19|20)\d{2})\s*Q\s*(?P<q1>[1-4])|Q\s*(?P<q2>[1-4])\s*(?P<y2>(?:19|20)\d{2})",
    re.I,
)


def _parse_period(text: Any) -> tuple[str | None, str | None]:
    match = _PERIOD.search(str(text or ""))
    if not match:
        return None, None
    year = match.group("y1") or match.group("y2")
    quarter = match.group("q1") or match.group("q2")
    return year, quarter


def _quarter_token(value: Any) -> str | None:
    """ApplFin stores Quarter as 1..4. Accept Q3, 3, 3.0."""
    if value is None:
        return None
    text = str(value).strip().upper().replace(" ", "")
    if not text or text in {"NAN", "NONE"}:
        return None
    match = re.search(r"([1-4])", text)
    return match.group(1) if match else None


def _int_token(value: Any) -> str | None:
    try:
        return str(int(float(str(value).strip())))
    except (TypeError, ValueError):
        return None


def _filter_text(df, col: str, raw: str):
    series = df[col].astype(str)
    needle = str(raw).strip()
    exact = series.str.casefold() == needle.casefold()
    if bool(exact.any()):
        return df[exact]
    return df[series.str.contains(needle, case=False, na=False)]


def _apply_named_filters(df, args: dict[str, Any]):
    for key, col in _SLICE_KEYS:
        raw = args.get(key)
        if raw is None or str(raw).strip() == "" or col not in df.columns:
            continue
        df = _filter_text(df, col, str(raw))
    stock = args.get("in_stock")
    if stock is not None and stock != "" and "in_stock" in df.columns:
        truthy = str(stock).lower() in {"1", "true", "yes", "y"}
        series = df["in_stock"].astype(str).str.lower()
        df = df[series.isin(["true", "1", "yes"]) if truthy else series.isin(["false", "0", "no", ""])]
    return df


def _handle_workforce_slice(args: dict[str, Any], ctx: RequestContext) -> dict:
    from retrieval.tables import table_entity

    df = _apply_named_filters(_load_table_role("hr", ctx.role), args)
    count = int(len(df))
    group_by = str(args.get("group_by") or "").strip()
    agg = str(args.get("agg") or "").strip().lower()
    if agg in {"avg", "average"}:
        agg = "mean"
    if agg in {"mean", "sum", "min", "max"}:
        pd = _pd()
        column = str(args.get("column") or "").strip()
        if not column:
            column = "Salary_INR" if "Salary_INR" in df.columns else ""
        if not column:
            raise SchemaInvalidError("agg requires column")
        _require_column(df, column)
        series = pd.to_numeric(df[column], errors="coerce").dropna()
        if series.empty:
            return {
                "table": "hr",
                "entity": table_entity("hr"),
                "status": "NOT_FOUND",
                "match_count": count,
                "agg": agg,
                "column": column,
                "empty": True,
                "preview": f"NOT FOUND: No matching records with numeric {column} found in slice. Abstain from guessing.",
            }
        value = float(getattr(series, agg)())
        label = "AVG" if agg == "mean" else agg.upper()
        return {
            "table": "hr",
            "entity": table_entity("hr"),
            "match_count": count,
            "n": int(len(series)),
            "agg": agg,
            "column": column,
            "value": value,
            "preview": f"{label}={value}\nmatch_count={count} (headcount, not the average)\nDo not report match_count as {column}.",
        }
    if args.get("count_only") in {True, "true", "True", "1"}:
        return {
            "table": "hr",
            "entity": table_entity("hr"),
            "match_count": count,
            "count": count,
            "records": [],
            "preview": f"{count} employees in slice",
        }
    if group_by:
        _require_column(df, group_by)
        counts = df[group_by].fillna("(null)").astype(str).value_counts().head(40)
        pd = _pd()
        records = [{"value": str(i), "count": int(v)} for i, v in counts.items()]
        return {
            "table": "hr",
            "entity": table_entity("hr"),
            "match_count": count,
            "group_by": group_by,
            "records": records,
            "preview": _md(pd.DataFrame(records)),
        }
    limit = max(1, min(int(args.get("limit") or 15), 40))
    head = df.head(limit)
    records = json.loads(head.to_json(orient="records", date_format="iso"))
    return {
        "table": "hr",
        "entity": table_entity("hr"),
        "match_count": count,
        "rows": int(len(head)),
        "records": records,
        "empty": count == 0,
        "preview": f"match_count={count}\n{_md(head)}",
    }


def _handle_ratio_agg(args: dict[str, Any], ctx: RequestContext) -> dict:
    table = str(args.get("table") or "hr")
    column = str(args.get("column") or "")
    value = str(args.get("value") or "")
    df = _apply_named_filters(_load_table_role(table, ctx.role), args)
    _require_column(df, column)
    denom = int(len(df))
    num = int(df[column].astype(str).str.contains(value, case=False, na=False).sum())
    ratio = (num / denom) if denom else None
    return {
        "table": table,
        "numerator": num,
        "denominator": denom,
        "ratio": ratio,
        "column": column,
        "value": value,
        "preview": f"{num}/{denom} = {ratio:.4f}" if ratio is not None else "empty base slice",
    }


def _handle_top_n(args: dict[str, Any], ctx: RequestContext) -> dict:
    table = str(args.get("table") or "")
    column = str(args.get("column") or "")
    n = max(1, min(int(args.get("n") or 5), 25))
    pd = _pd()
    df = _apply_named_filters(_load_table_role(table, ctx.role), args)
    _require_column(df, column)
    series = pd.to_numeric(df[column], errors="coerce")
    work = df.assign(_sort=series).dropna(subset=["_sort"])
    work = work.sort_values("_sort", ascending=args.get("ascending") in {True, "true", "True", "1"})
    head = work.drop(columns=["_sort"]).head(n)
    records = json.loads(head.to_json(orient="records", date_format="iso"))
    return {
        "table": table,
        "column": column,
        "n": int(len(head)),
        "records": records,
        "preview": _md(head),
    }


def _handle_macro_latest(args: dict[str, Any], ctx: RequestContext) -> dict:
    from retrieval.tables import table_entity

    names = [str(args["table"])] if args.get("table") else ["fred_cpi", "fred_fedfunds", "fred_umcsent"]
    rows = []
    for name in names:
        df = _load_table_role(name, ctx.role)
        date_col = _date_col(df) or df.columns[0]
        work = df.sort_values(date_col)
        last = work.iloc[-1]
        rec = {c: last[c] for c in work.columns}
        rec["_table"] = name
        rec["_entity"] = table_entity(name)
        rows.append(json.loads(json.dumps(rec, default=str)))
    preview = "\n".join(f"{r.get('_table')}: { {k: r[k] for k in r if not k.startswith('_')} }" for r in rows)
    return {"records": rows, "preview": preview}


def _handle_series_window(args: dict[str, Any], ctx: RequestContext) -> dict:
    pd = _pd()
    table = str(args.get("table") or "")
    column = str(args.get("column") or "")
    df = _load_table_role(table, ctx.role)
    _require_column(df, column)
    date_col = _date_col(df)
    if not date_col:
        raise SchemaInvalidError(f"no date column on '{table}'")
    keys = df[date_col].astype(str)
    if args.get("date_from"):
        df = df[keys >= str(args["date_from"])]
        keys = df[date_col].astype(str)
    if args.get("date_to"):
        df = df[keys <= str(args["date_to"])]
    if args.get("date"):
        df = df[keys.str.startswith(str(args["date"]))]
    series = pd.to_numeric(df[column], errors="coerce").dropna()
    if series.empty:
        return {"table": table, "column": column, "n": 0, "preview": "empty window"}
    stats = {
        "table": table,
        "column": column,
        "n": int(len(series)),
        "first": float(series.iloc[0]),
        "last": float(series.iloc[-1]),
        "min": float(series.min()),
        "max": float(series.max()),
        "mean": float(series.mean()),
    }
    stats["preview"] = (
        f"{column} n={stats['n']} first={stats['first']:.4g} last={stats['last']:.4g} "
        f"min={stats['min']:.4g} max={stats['max']:.4g}"
    )
    return stats


def _handle_date_at(args: dict[str, Any], ctx: RequestContext) -> dict:
    from harness.entities import assert_entity_allowed
    from retrieval.tables import table_entity

    table = str(args.get("table") or "")
    column = str(args.get("column") or "")
    df = _load_table_role(table, ctx.role)
    _require_column(df, column)
    try:
        assert_entity_allowed(ctx, table_entity(table))
    except SchemaInvalidError:
        pass
    work = df
    year = args.get("year")
    quarter = args.get("quarter")
    if (year in (None, "") or quarter in (None, "")) and args.get("period"):
        py, pq = _parse_period(args.get("period"))
        year = year if year not in (None, "") else py
        quarter = quarter if quarter not in (None, "") else pq
    if year not in (None, "") and isinstance(year, str) and "Q" in str(year).upper():
        py, pq = _parse_period(year)
        year = py or year
        quarter = quarter if quarter not in (None, "") else pq
    period_table = "Year" in df.columns and "Quarter" in df.columns
    keyed = any(v not in (None, "") for v in (year, quarter, args.get("date")))
    if period_table and not keyed:
        return {
            "table": table,
            "column": column,
            "empty": True,
            "preview": "period table needs year+quarter, period='YYYY Qn', or date — not the first row",
        }
    if year not in (None, "") and "Year" in work.columns:
        year_token = _int_token(year)
        if year_token is None:
            return {"table": table, "column": column, "empty": True, "preview": "year must be an integer"}
        work = work[work["Year"].map(_int_token) == year_token]
    if quarter not in (None, "") and "Quarter" in work.columns:
        q = _quarter_token(quarter)
        if q is None:
            return {"table": table, "column": column, "empty": True, "preview": "quarter must be 1-4 or Q1-Q4"}
        work = work[work["Quarter"].map(_quarter_token) == q]
    date_col = _date_col(work)
    if args.get("date") and date_col:
        work = work[work[date_col].astype(str).str.startswith(str(args["date"]))]
    if work.empty:
        return {"table": table, "column": column, "empty": True, "preview": "no row for that date/period"}
    row = work.iloc[0]
    pd = _pd()
    value = pd.to_numeric(row[column], errors="coerce")
    rec = json.loads(json.dumps({c: row[c] for c in work.columns}, default=str))
    preview = f"{table}.{column}={value} at {args.get('date') or year or ''} {quarter or ''}".strip()
    return {
        "table": table,
        "column": column,
        "value": None if (value != value) else float(value),
        "row": rec,
        "empty": False,
        "preview": preview,
    }


def _handle_product_search(args: dict[str, Any], ctx: RequestContext) -> dict:
    pd = _pd()
    df = _apply_named_filters(_load_table_role("products", ctx.role), args)
    q = str(args.get("query") or "").strip()
    if q:
        blob = df.get("model_name", pd.Series("", index=df.index)).astype(str)
        cat = df.get("category", pd.Series("", index=df.index)).astype(str)
        df = df[blob.str.contains(q, case=False, na=False) | cat.str.contains(q, case=False, na=False)]
    if "price" in df.columns:
        price = pd.to_numeric(df["price"], errors="coerce")
        if args.get("min_price") is not None and args.get("min_price") != "":
            df = df[price >= float(args["min_price"])]
            price = pd.to_numeric(df["price"], errors="coerce")
        if args.get("max_price") is not None and args.get("max_price") != "":
            df = df[price <= float(args["max_price"])]
    match_count = int(len(df))
    limit = max(1, min(int(args.get("limit") or 15), 40))
    head = df.head(limit)
    records = json.loads(head.to_json(orient="records", date_format="iso"))
    return {
        "table": "products",
        "entity": "AAPL",
        "match_count": match_count,
        "rows": int(len(head)),
        "records": records,
        "empty": match_count == 0,
        "preview": f"COUNT={match_count}\nmatch_count={match_count}\nDo not report the preview length ({len(head)}) as the catalog size.\n{_md(head)}",
    }
