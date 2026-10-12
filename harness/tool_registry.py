"""Fat registry behind a small verb set. Adding a tool is a manifest + handler, not a new LLM function."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional
from urllib.parse import urlparse

from harness.config import EGRESS_ALLOWLIST, GRAPHS_DIR, KNOWN_COLLECTIONS, get_settings
from harness.context import RequestContext
from harness.errors import (
    CircuitOpenError,
    ErrorClass,
    HarnessError,
    PolicyDeniedError,
    RetrievalMissError,
    SchemaInvalidError,
    ToolRuntimeError,
    UnknownToolError,
)
from harness.permissions import assert_tool_allowed
from harness.types import SideEffect, SpanKind, ToolManifest, ToolResult
from harness.trace import get_tracer

Handler = Callable[[dict[str, Any], RequestContext], Any]


@dataclass
class CircuitState:
    failures: int = 0
    opened_at: float = 0.0

    def allow(self, threshold: int, cooldown_s: float) -> bool:
        if self.opened_at == 0.0:
            return True
        if time.time() - self.opened_at >= cooldown_s:
            self.failures = 0
            self.opened_at = 0.0
            return True
        return False

    def record(self, ok: bool, threshold: int) -> None:
        if ok:
            self.failures = 0
            self.opened_at = 0.0
            return
        self.failures += 1
        if self.failures >= threshold:
            self.opened_at = time.time()


class ToolRegistry:
    def __init__(self) -> None:
        self._manifests: dict[str, ToolManifest] = {}
        self._handlers: dict[str, Handler] = {}
        self._circuits: dict[str, CircuitState] = {}
        self._register_builtins()

    def register(self, manifest: ToolManifest, handler: Handler) -> None:
        self._manifests[manifest.verb] = manifest
        self._handlers[manifest.verb] = handler

    def manifest(self, verb: str) -> Optional[ToolManifest]:
        return self._manifests.get(verb)

    def verbs(self) -> list[str]:
        return sorted(self._manifests)

    def openai_tools(self, allowed: Optional[list[str]] = None) -> list[dict[str, Any]]:
        verbs = allowed or list(self._manifests)
        tools = []
        for verb in verbs:
            m = self._manifests.get(verb)
            if m is None:
                continue
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": m.verb,
                        "description": m.description,
                        "parameters": m.schema,
                    },
                }
            )
        return tools

    def call(self, verb: str, arguments: dict[str, Any], ctx: RequestContext) -> ToolResult:
        ctx.check_time()
        manifest = self._manifests.get(verb)
        if manifest is None:
            raise UnknownToolError(f"Unknown tool '{verb}'. Use search_tools.")
        assert_tool_allowed(ctx.role, verb)
        settings = get_settings()
        circuit_key = f"{ctx.request_id}:{verb}"
        circuit = self._circuits.setdefault(circuit_key, CircuitState())
        if manifest.circuit_breaker and not circuit.allow(
            settings.circuit_fail_threshold, settings.circuit_cooldown_s
        ):
            return ToolResult(
                ok=False,
                verb=verb,
                error_class="CircuitOpen",
                error=f"Tool '{verb}' is paused (circuit open)",
            )

        tracer = get_tracer()
        t0 = time.perf_counter()
        with tracer.span(
            ctx.request_id,
            SpanKind.TOOL,
            f"tool.{verb}",
            parent_id=ctx.parent_span_id,
            args={"verb": verb, "keys": sorted(arguments.keys())},
        ) as span:
            try:
                if str((ctx.extra or {}).get("inject") or "") == "F7" and not (ctx.extra or {}).get("inject_consumed"):
                    ctx.extra["inject_consumed"] = True
                    raise PolicyDeniedError("injected policy deny (F7)")
                handler = self._handlers[verb]
                data = handler(arguments or {}, ctx)
                preview, truncated = _compact(data)
                blob = json.dumps(data, default=str, sort_keys=True)[:20_000]
                result = ToolResult(
                    ok=True,
                    verb=verb,
                    data=data,
                    preview=preview,
                    result_hash=hashlib.sha256(blob.encode()).hexdigest()[:16],
                    truncated=truncated,
                    latency_ms=(time.perf_counter() - t0) * 1000,
                )
                span.result_hash = result.result_hash
                extra = dict(span.extra or {})
                extra["preview"] = (preview or "")[:500]
                extra["verb"] = verb
                span.extra = extra
                circuit.record(True, settings.circuit_fail_threshold)
                return result
            except HarnessError as exc:
                span.error_class = exc.error_class.value
                # Infrastructure failures and unknown tables trip circuit breaker; column/filter schema repair does not
                blob = str(exc.safe_message or "").lower()
                if (
                    exc.error_class in {
                        ErrorClass.TOOL_RUNTIME,
                        ErrorClass.TIMEOUT,
                        ErrorClass.SANDBOX,
                        ErrorClass.AGENT_CRASHED,
                    }
                    or (exc.error_class == ErrorClass.SCHEMA_INVALID and "unknown table" in blob)
                ):
                    circuit.record(False, settings.circuit_fail_threshold)
                return ToolResult(
                    ok=False,
                    verb=verb,
                    error_class=exc.error_class.value,
                    error=exc.safe_message,
                    latency_ms=(time.perf_counter() - t0) * 1000,
                )
            except Exception as exc:
                span.error_class = "ToolRuntime"
                circuit.record(False, settings.circuit_fail_threshold)
                return ToolResult(
                    ok=False,
                    verb=verb,
                    error_class="ToolRuntime",
                    error=str(exc)[:400],
                    latency_ms=(time.perf_counter() - t0) * 1000,
                )

    def _register_builtins(self) -> None:
        self.register(
            ToolManifest(
                id="retrieve",
                verb="retrieve",
                resource_type="chroma",
                description="Retrieve evidence chunks. Optional entity filter (MNC|AAPL|MSFT|FRED|SYNTH_CAL).",
                schema=_object(
                    {
                        "query": _str("search query"),
                        "collection": _str("hr_data | finance_data | scheduler_data"),
                        "k": {"type": "integer", "minimum": 1, "maximum": 20},
                        "entity": _str("optional entity tag"),
                    },
                    required=["query"],
                ),
            ),
            _handle_retrieve,
        )
        self.register(
            ToolManifest(
                id="sql_spec",
                verb="sql_spec",
                resource_type="table",
                description="Constrained aggregation. Never send Python. Tables: hr, applfin, fred_cpi, fred_fedfunds, fred_umcsent, calendar.",
                schema=_object(
                    {
                        "table": _str("table name"),
                        "filters": {"type": "array", "items": {"type": "object"}},
                        "group_by": {"type": "array", "items": {"type": "string"}},
                        "agg": {"type": "object"},
                        "columns": {"type": "array", "items": {"type": "string"}},
                        "sort": {"type": "object"},
                        "limit": {"type": "integer"},
                    },
                    required=["table"],
                ),
            ),
            _handle_sql_spec,
        )
        self.register(
            ToolManifest(
                id="forecast",
                verb="forecast",
                resource_type="series",
                description="ARIMA forecast on a numeric column of applfin or a FRED table.",
                schema=_object(
                    {
                        "table": _str("applfin | fred_cpi | fred_fedfunds | fred_umcsent"),
                        "column": _str("numeric column"),
                        "periods": {"type": "integer", "minimum": 1, "maximum": 12},
                    },
                    required=["table", "column"],
                ),
            ),
            _handle_forecast,
        )
        self.register(
            ToolManifest(
                id="anomaly_detect",
                verb="anomaly_detect",
                resource_type="series",
                description="Z-score anomaly detection on a numeric column.",
                schema=_object(
                    {
                        "table": _str("table name"),
                        "column": _str("numeric column"),
                        "threshold": {"type": "number"},
                    },
                    required=["table", "column"],
                ),
            ),
            _handle_anomaly,
        )
        self.register(
            ToolManifest(
                id="calendar_query",
                verb="calendar_query",
                resource_type="calendar",
                description="Query frozen calendar rows by organizer, date, title, attendee, or event_id. Returns match_count of the full filter; set count_only=true for the integer count.",
                schema=_object(
                    {
                        "organizer": _str("optional organizer substring"),
                        "date_prefix": _str("YYYY-MM-DD prefix of start_time"),
                        "title_contains": _str("optional title substring"),
                        "attendee": _str("optional attendee substring"),
                        "event_id": _str("optional exact event_id"),
                        "count_only": {"type": "boolean", "description": "return match_count without row payloads"},
                        "limit": {"type": "integer"},
                    }
                ),
            ),
            _handle_calendar,
        )
        self.register(
            ToolManifest(
                id="render_chart",
                verb="render_chart",
                resource_type="chart",
                description="Render a chart from a sql_spec-like spec. graph_type: bar|line|pie|scatter.",
                schema=_object(
                    {
                        "table": _str("table name"),
                        "graph_type": _str("bar|line|pie|scatter"),
                        "x": _str("x column"),
                        "y": _str("y column"),
                        "title": _str("title"),
                        "filters": {"type": "array"},
                    },
                    required=["table"],
                ),
                side_effect=SideEffect.WRITE,
            ),
            _handle_chart,
        )
        self.register(
            ToolManifest(
                id="http_fetch",
                verb="http_fetch",
                resource_type="web",
                description="GET an allow-listed URL (SEC, FRED, Apple, Microsoft). No secrets in the sandbox.",
                schema=_object({"url": _str("https url"), "max_bytes": {"type": "integer"}}, required=["url"]),
                side_effect=SideEffect.EGRESS,
                allowed_hosts=EGRESS_ALLOWLIST,
                timeout_ms=15_000,
                max_bytes=200_000,
            ),
            _handle_http,
        )
        self.register(
            ToolManifest(
                id="session_get",
                verb="session_get",
                resource_type="session",
                description="Expand a compacted artifact_id from this request. Use when a tool stub is missing detail.",
                schema=_object(
                    {
                        "artifact_id": _str("id from a [compacted artifact:…] stub"),
                        "max_chars": {"type": "integer"},
                    },
                    required=["artifact_id"],
                ),
            ),
            _handle_session_get,
        )
        self.register(
            ToolManifest(
                id="skill_manage",
                verb="skill_manage",
                resource_type="skill",
                description=(
                    "Procedural memory: list/get saved SKILL.md files, or create/archive one after a working fix. "
                    "op=list|get|create|archive. Limited roles cannot create or archive."
                ),
                schema=_object(
                    {
                        "op": _str("list|get|create|archive"),
                        "name": _str("skill slug"),
                        "description": _str("when to apply this skill"),
                        "procedure": _str("the working steps / corrected spec"),
                        "verbs": _str("comma-separated tool verbs"),
                        "query": _str("optional filter for list"),
                    },
                    required=["op"],
                ),
                side_effect=SideEffect.WRITE,
            ),
            _handle_skill_manage,
        )
        self.register(
            ToolManifest(
                id="search_tools",
                verb="search_tools",
                resource_type="catalog",
                description="List available tool verbs and one-line descriptions.",
                schema=_object({"query": _str("optional filter")}),
            ),
            _handle_search_tools,
        )
        self.register(
            ToolManifest(
                id="search_agents",
                verb="search_agents",
                resource_type="catalog",
                description="List active agent ids and intents.",
                schema=_object({"query": _str("optional filter")}),
            ),
            _handle_search_agents,
        )
        from harness.tool_extras import register_extras
        from kb.tools import register_kb_tools

        register_extras(self)
        register_kb_tools(self)


def _object(properties: dict, required: list[str] | None = None) -> dict:
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _str(desc: str) -> dict:
    return {"type": "string", "description": desc}


def _compact(data: Any, limit: int = 2500) -> tuple[str, bool]:
    if isinstance(data, dict) and "preview" in data:
        text = str(data["preview"])
        extra = {k: data[k] for k in data if k not in {"records", "preview", "graph_json"}}
        text = json.dumps(extra, default=str) + "\n" + text
    else:
        text = json.dumps(data, default=str, ensure_ascii=False)
    if len(text) <= limit:
        return text, False
    return text[:limit] + "\n…[truncated]", True


def _apply_eval_inject(args: dict[str, Any], ctx: RequestContext, verb: str) -> dict[str, Any]:
    """One-shot fault for FDA/RSR. Live chat never sets ctx.extra['inject']."""
    code = str((ctx.extra or {}).get("inject") or "")
    if not code or (ctx.extra or {}).get("inject_consumed"):
        return args
    patched = dict(args)
    if verb == "sql_spec" and code == "F4":
        patched["column"] = "PerformanceScore"
    elif verb == "sql_spec" and code == "F1":
        patched["table"] = "nope"
    elif verb == "retrieve" and code == "F2":
        patched["collection"] = "does_not_exist"
    elif code == "F7":
        ctx.extra["inject_consumed"] = True
        raise PolicyDeniedError("injected policy deny (F7)")
    else:
        return args
    ctx.extra["inject_consumed"] = True
    return patched


def _handle_retrieve(args: dict[str, Any], ctx: RequestContext) -> dict:
    from retrieval.vector_store import query_collection_diverse

    args = _apply_eval_inject(args, ctx, "retrieve")
    query = str(args.get("query") or "")
    if not query:
        raise SchemaInvalidError("retrieve.query is required")
    collection = str(args.get("collection") or _guess_collection(ctx))
    if collection not in KNOWN_COLLECTIONS:
        raise RetrievalMissError(f"unknown collection '{collection}'. known: {list(KNOWN_COLLECTIONS)}")
    k = int(args.get("k") or ctx.extra.get("retrieve_k") or get_settings().retrieve_k)
    k = max(1, min(k, 20))
    where = None
    entity = args.get("entity")
    if entity:
        where = {"entity": str(entity)}
    try:
        raw = query_collection_diverse(collection, query, top_k=k, where=where)
    except Exception:
        try:
            raw = query_collection_diverse(collection, query, top_k=k, where=None)
        except Exception as exc:
            raise RetrievalMissError(f"retrieve failed for collection '{collection}'") from exc
    docs = (raw.get("documents") or [[]])[0]
    metas = (raw.get("metadatas") or [[]])[0]
    dists = (raw.get("distances") or [[]])[0]
    chunks = []
    for i, doc in enumerate(docs):
        meta = metas[i] if i < len(metas) else {}
        chunks.append(
            {
                "chunk_id": meta.get("id") or f"{collection}:{i}",
                "text": doc,
                "metadata": meta,
                "distance": dists[i] if i < len(dists) else None,
            }
        )
    if not chunks:
        raise RetrievalMissError(f"no chunks in '{collection}' for this query")
    from harness.entities import assert_entity_allowed

    for chunk in chunks:
        ent = (chunk.get("metadata") or {}).get("entity")
        if ent:
            assert_entity_allowed(ctx, str(ent))
    return {
        "collection": collection,
        "empty": len(chunks) == 0,
        "k": len(chunks),
        "chunks": chunks,
        "preview": "\n\n".join(
            f"[{c['chunk_id']} entity={c['metadata'].get('entity','?')}]\n{c['text'][:400]}"
            for c in chunks
        ),
    }


def _guess_collection(ctx: RequestContext) -> str:
    pin = ctx.pin_agent or str((ctx.extra or {}).get("worker_agent") or "")
    if pin == "hr":
        return "hr_data"
    if pin == "scheduler":
        return "scheduler_data"
    return "finance_data"


def _handle_sql_spec(args: dict[str, Any], ctx: RequestContext) -> dict:
    from harness.entities import assert_entity_allowed
    from retrieval.tables import table_entity
    from sandbox.exec_sandbox import execute_sql_spec

    args = _apply_eval_inject(args, ctx, "sql_spec")
    table = str(args.get("table") or "")
    if table and not table.startswith("kb_"):
        try:
            assert_entity_allowed(ctx, table_entity(table))
        except SchemaInvalidError:
            pass
    return execute_sql_spec(args, role=ctx.role)


def _handle_forecast(args: dict[str, Any], ctx: RequestContext) -> dict:
    import pandas as pd
    from retrieval.tables import load_table
    from statsmodels.tsa.arima.model import ARIMA

    table = str(args.get("table") or "")
    column = str(args.get("column") or "")
    periods = int(args.get("periods") or 3)
    periods = max(1, min(periods, 12))
    df = load_table(table)
    if column not in df.columns:
        raise SchemaInvalidError(f"unknown column '{column}'. available: {list(df.columns)}")
    work = df
    for date_name in ("DATE", "Date", "date"):
        if date_name in work.columns:
            work = work.sort_values(date_name)
            break
    series = pd.to_numeric(work[column], errors="coerce").dropna()
    if len(series) < 10:
        raise ToolRuntimeError(f"need >=10 points for forecast, got {len(series)}")
    fitted = ARIMA(series, order=(1, 1, 1)).fit()
    fc = fitted.forecast(steps=periods)
    return {
        "table": table,
        "column": column,
        "periods": periods,
        "forecast": [float(x) for x in fc],
        "aic": float(fitted.aic),
        "bic": float(fitted.bic),
        "last_observed": float(series.iloc[-1]),
        "preview": "\n".join(f"t+{i+1}: {v:.4f}" for i, v in enumerate(fc)),
    }


def _handle_anomaly(args: dict[str, Any], ctx: RequestContext) -> dict:
    import pandas as pd
    from retrieval.tables import load_table

    table = str(args.get("table") or "")
    column = str(args.get("column") or "")
    threshold = float(args.get("threshold") or 2.0)
    df = load_table(table)
    if column not in df.columns:
        raise SchemaInvalidError(f"unknown column '{column}'")
    series = pd.to_numeric(df[column], errors="coerce").dropna()
    if len(series) < 5:
        raise ToolRuntimeError("need >=5 points")
    mean, std = float(series.mean()), float(series.std())
    if std == 0:
        return {"anomalies": [], "count": 0, "preview": "no variance"}
    z = (series - mean).abs() / std
    flagged = series[z > threshold]
    values = [float(v) for v in flagged.head(10)]
    return {
        "table": table,
        "column": column,
        "count": int(len(flagged)),
        "mean": mean,
        "std": std,
        "values": values,
        "preview": f"{len(flagged)} anomalies; sample={values[:5]}",
    }


def _handle_calendar(args: dict[str, Any], ctx: RequestContext) -> dict:
    from retrieval.tables import load_table

    df = load_table("calendar")
    if args.get("event_id") and "event_id" in df.columns:
        df = df[df["event_id"].astype(str) == str(args["event_id"])]
    if args.get("organizer") and "organizer" in df.columns:
        df = df[df["organizer"].astype(str).str.contains(str(args["organizer"]), case=False, na=False)]
    if args.get("date_prefix") and "start_time" in df.columns:
        df = df[df["start_time"].astype(str).str.startswith(str(args["date_prefix"]))]
    if args.get("title_contains") and "title" in df.columns:
        df = df[df["title"].astype(str).str.contains(str(args["title_contains"]), case=False, na=False)]
    if args.get("attendee") and "attendees" in df.columns:
        df = df[df["attendees"].astype(str).str.contains(str(args["attendee"]), case=False, na=False)]
    from retrieval.tables import unique_calendar_events

    df = unique_calendar_events(df)
    match_count = int(len(df))
    count_only = args.get("count_only") in {True, "true", "True", 1, "1"}
    if count_only:
        return {
            "rows": match_count,
            "match_count": match_count,
            "count": match_count,
            "records": [],
            "empty": match_count == 0,
            "preview": f"{match_count} matching events",
        }
    limit = max(1, min(int(args.get("limit") or 20), 50))
    head = df.head(limit)
    records = json.loads(head.to_json(orient="records"))
    try:
        preview = head.to_markdown(index=False) if not head.empty else "(no events)"
    except Exception:
        preview = head.to_string(index=False) if not head.empty else "(no events)"
    return {
        "rows": int(len(head)),
        "match_count": match_count,
        "records": records,
        "empty": match_count == 0,
        "preview": f"match_count={match_count}\n{preview}",
    }


def _handle_chart(args: dict[str, Any], ctx: RequestContext) -> dict:
    from datetime import datetime

    import pandas as pd
    from sandbox.exec_sandbox import execute_sql_spec

    spec = {
        "table": args.get("table"),
        "filters": args.get("filters") or [],
        "columns": [c for c in [args.get("x"), args.get("y")] if c],
        "limit": 200,
    }
    # if y given, group by x and sum/mean y when possible
    if args.get("x") and args.get("y"):
        spec["group_by"] = [args["x"]]
        spec["agg"] = {args["y"]: "sum"}
        spec.pop("columns", None)
    data = execute_sql_spec(spec, role=ctx.role)
    records = data.get("records") or []
    plot_df = pd.DataFrame(records)
    if plot_df.empty:
        return {**data, "file_path": None, "preview": "no rows to chart"}
    try:
        import plotly.express as px
    except ImportError as exc:
        raise ToolRuntimeError("plotly is not installed") from exc
    graph_type = str(args.get("graph_type") or "bar")
    x_col = args.get("x") if args.get("x") in plot_df.columns else plot_df.columns[0]
    y_col = args.get("y") if args.get("y") in plot_df.columns else plot_df.columns[-1]
    title = str(args.get("title") or "Orion chart")
    if graph_type == "line":
        fig = px.line(plot_df, x=x_col, y=y_col, title=title, markers=True)
    elif graph_type == "pie":
        fig = px.pie(plot_df, names=x_col, values=y_col, title=title)
    elif graph_type == "scatter":
        fig = px.scatter(plot_df, x=x_col, y=y_col, title=title)
    else:
        fig = px.bar(plot_df, x=x_col, y=y_col, title=title)
    fig.update_layout(template="plotly_dark")
    GRAPHS_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"graph_{graph_type}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.png"
    filepath = GRAPHS_DIR / filename
    try:
        fig.write_image(str(filepath), width=800, height=500, scale=2)
        file_path = str(filepath)
    except Exception:
        file_path = None
    md = f"![{title}](/graphs/{filename})" if file_path else "(image save skipped — kaleido missing?)"
    return {
        **data,
        "file_path": file_path,
        "filename": filename if file_path else None,
        "markdown": md,
        "x": x_col,
        "y": y_col,
        "graph_type": graph_type,
        "preview": f"{md}\n\n{data.get('preview','')}",
    }


def _handle_http(args: dict[str, Any], ctx: RequestContext) -> dict:
    import httpx

    url = str(args.get("url") or "")
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not host:
        raise PolicyDeniedError("http_fetch: invalid URL")
    if not any(host == h or host.endswith("." + h) for h in EGRESS_ALLOWLIST):
        raise PolicyDeniedError(f"host '{host}' is not on the egress allow-list")
    max_bytes = min(int(args.get("max_bytes") or 200_000), 200_000)
    try:
        with httpx.Client(timeout=15.0, follow_redirects=True) as client:
            response = client.get(url, headers={"User-Agent": "OrionHarness/1.0"})
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ToolRuntimeError(f"http_fetch failed: {exc}") from exc
    final_host = urlparse(str(response.url)).hostname or ""
    if not any(final_host == h or final_host.endswith("." + h) for h in EGRESS_ALLOWLIST):
        raise PolicyDeniedError("redirect left the allow-list")
    text = response.text[:max_bytes]
    return {"url": str(response.url), "status": response.status_code, "text": text, "preview": text[:1500]}


def _handle_session_get(args: dict[str, Any], ctx: RequestContext) -> dict:
    aid = str(args.get("artifact_id") or "").strip()
    art = ctx.session.get(aid) if getattr(ctx, "session", None) else None
    if art is None:
        raise SchemaInvalidError(f"unknown artifact_id '{aid}'")
    limit = max(500, min(int(args.get("max_chars") or 8000), 16_000))
    text = art.text[:limit]
    return {
        "artifact_id": aid,
        "kind": art.kind,
        "truncated": len(art.text) > limit,
        "tokens_est": art.tokens_est,
        "text": text,
        "preview": text[:1500],
    }


def _handle_skill_manage(args: dict[str, Any], ctx: RequestContext) -> dict:
    from harness.skills import default_store

    op = str(args.get("op") or "").strip().lower()
    store = default_store()
    if op == "list":
        items = store.list(str(args.get("query") or ""))
        rows = [{"name": s.name, "description": s.description, "hits": s.hits} for s in items]
        return {"skills": rows, "preview": "\n".join(f"- {r['name']}: {r['description']}" for r in rows) or "(none)"}
    if op == "get":
        name = str(args.get("name") or "").strip()
        skill = store.get(name) if name else None
        if skill is None:
            raise SchemaInvalidError(f"unknown skill '{name}'")
        text = skill.to_markdown()
        return {"name": skill.name, "text": text, "preview": text[:1500]}
    if op in {"create", "archive"} and ctx.role == "limited":
        raise PolicyDeniedError("limited role cannot create or archive skills")
    if op == "archive":
        name = str(args.get("name") or "").strip()
        ok = store.archive(name) if name else False
        if not ok:
            raise SchemaInvalidError(f"unknown skill '{name}'")
        store.curate()
        return {"archived": name, "preview": f"archived {name}"}
    if op == "create":
        name = str(args.get("name") or "").strip()
        procedure = str(args.get("procedure") or "").strip()
        if not name or not procedure:
            raise SchemaInvalidError("create requires name and procedure")
        verbs = [v.strip() for v in str(args.get("verbs") or "").split(",") if v.strip()]
        try:
            skill = store.create(
                name=name,
                description=str(args.get("description") or "")[:240],
                procedure=procedure,
                agents=[getattr(ctx, "pin_agent", None) or ""] if getattr(ctx, "pin_agent", None) else [],
                verbs=verbs,
            )
        except ValueError as exc:
            raise PolicyDeniedError(str(exc)) from exc
        store.curate()
        return {"name": skill.slug, "preview": f"saved skill `{skill.slug}`"}
    raise SchemaInvalidError("op must be list, get, create, or archive")


def _handle_search_tools(args: dict[str, Any], ctx: RequestContext) -> dict:
    q = str(args.get("query") or "").strip()
    q_low = q.lower()
    manifests = ctx.runtime.tools._manifests
    allowed: set[str] | None = None
    worker = ctx.pin_agent or str((ctx.extra or {}).get("worker_agent") or "")
    if worker:
        card = ctx.runtime.agents.get(worker)
        if card:
            allowed = set(card.tools) | {
                "search_tools",
                "session_get",
                "skill_manage",
                "schema_lookup",
                "join_check",
                "evidence_check",
            }
    if (ctx.extra or {}).get("kb_enabled") and allowed is not None:
        allowed |= {"kb_list", "kb_search", "kb_query"}
    exact = manifests.get(q) or manifests.get(q_low)
    if exact and (allowed is None or exact.verb in allowed):
        return {
            "tools": [
                {
                    "verb": exact.verb,
                    "description": exact.description,
                    "schema": exact.schema,
                    "side_effect": exact.side_effect.value,
                }
            ],
            "preview": json.dumps({"verb": exact.verb, "schema": exact.schema}, indent=2)[:2500],
        }
    items = []
    for verb, manifest in manifests.items():
        if allowed is not None and verb not in allowed:
            continue
        blob = f"{verb} {manifest.description}".lower()
        if q_low and q_low not in blob:
            continue
        required = (manifest.schema or {}).get("required") or []
        items.append(
            {
                "verb": verb,
                "description": manifest.description,
                "required": required,
                "side_effect": manifest.side_effect.value,
            }
        )
    preview = "\n".join(f"- {i['verb']}{i['required']}: {i['description']}" for i in items)
    return {"tools": items, "preview": preview or "(no matching tools)"}


def _handle_search_agents(args: dict[str, Any], ctx: RequestContext) -> dict:
    q = str(args.get("query") or "").lower()
    items = []
    for card in ctx.runtime.agents.active():
        blob = f"{card.id} {card.intent} {card.description}".lower()
        if q and q not in blob:
            continue
        items.append({"id": card.id, "intent": card.intent, "status": card.status.value})
    return {"agents": items, "preview": "\n".join(f"- {i['id']}: {i['intent']}" for i in items)}
