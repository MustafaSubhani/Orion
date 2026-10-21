"""SQLAlchemy connector: SQLite, Postgres, MySQL and anything else SQLAlchemy supports.

Read-only by construction (statements are built with SQLAlchemy Core from the allow-listed spec,
never from raw text) and by configuration (SQLite opens `mode=ro`; Postgres and MySQL sessions are
set read-only with a statement timeout). Tables are checked against an allow-list, so system
catalogs are unreachable.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

import pandas as pd
from sqlalchemy import MetaData, Table, create_engine, distinct, event, func, select
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool

from harness.connectors.base import ColumnInfo, ConnectorConfig, ConnectorError, DataConnector
from harness.errors import SchemaInvalidError, ToolRuntimeError
from sandbox.exec_sandbox import result_payload

_AGGS = {"count", "sum", "mean", "min", "max", "nunique", "size"}
_REFLECT_TTL_S = 0.0  # reflect live on every call: a renamed column must surface as F4, not a stale error


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class SqlAlchemyConnector(DataConnector):
    type = "sqlalchemy"

    def __init__(self, config: ConnectorConfig) -> None:
        super().__init__(config)
        self._engine: Optional[Engine] = None
        self._lock = threading.Lock()
        self._local = threading.local()
        self._reflected: dict[str, tuple[float, Table]] = {}

    # ---------------------------------------------------------------- engine
    def _build_engine(self) -> Engine:
        import os

        raw = os.environ.get(self.config.url_env, "") if self.config.url_env else ""
        if not raw:
            raise ConnectorError(f"environment variable {self.config.url_env or '(url_env)'} is not set for connector '{self.id}'")
        try:
            url = make_url(raw)
        except Exception as exc:  # never echo the malformed URL: it may hold credentials
            raise ConnectorError(f"connector '{self.id}' has an invalid URL ({type(exc).__name__})") from exc
        timeout_ms = int(self.config.statement_timeout_s * 1000)
        if url.get_backend_name() == "sqlite":
            if not url.database or url.database == ":memory:":
                raise ConnectorError("sqlite connectors need a file database")
            path = Path(url.database).resolve().as_posix()
            local = self._local

            def creator() -> sqlite3.Connection:
                conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=self.config.statement_timeout_s)

                def guard() -> int:
                    deadline = getattr(local, "deadline", None)
                    return 1 if deadline is not None and time.monotonic() > deadline else 0

                conn.set_progress_handler(guard, 20_000)
                return conn

            return create_engine("sqlite://", creator=creator, poolclass=NullPool)
        backend_name = url.get_backend_name()
        connect_args: dict[str, Any] = {}
        if backend_name in {"postgresql", "mysql", "mariadb"}:
            # Without this a dead host blocks the request thread for the OS TCP timeout (minutes).
            connect_args["connect_timeout"] = max(1, int(self.config.statement_timeout_s))
        try:
            engine = create_engine(url, pool_pre_ping=True, pool_size=2, max_overflow=2, connect_args=connect_args)
        except Exception as exc:  # missing driver or bad dialect; drop the message, it can contain the URL
            raise ConnectorError(f"connector '{self.id}' could not create a database engine ({type(exc).__name__})") from exc
        backend = url.get_backend_name()

        @event.listens_for(engine, "connect")
        def _session_setup(dbapi_conn, _record) -> None:  # pragma: no cover - needs a live server
            cur = dbapi_conn.cursor()
            try:
                if backend == "postgresql":
                    cur.execute("SET default_transaction_read_only = on")
                    cur.execute(f"SET statement_timeout = {timeout_ms}")
                elif backend in {"mysql", "mariadb"}:
                    cur.execute("SET SESSION TRANSACTION READ ONLY")
                    cur.execute(f"SET SESSION MAX_EXECUTION_TIME = {timeout_ms}")
            finally:
                cur.close()

        return engine

    def engine(self) -> Engine:
        with self._lock:
            if self._engine is None:
                self._engine = self._build_engine()
            return self._engine

    def dispose(self) -> None:
        with self._lock:
            if self._engine is not None:
                self._engine.dispose()
                self._engine = None
            self._reflected.clear()

    # ---------------------------------------------------------------- schema
    def _existing_tables(self) -> list[str]:
        from sqlalchemy import inspect

        try:
            return sorted(inspect(self.engine()).get_table_names())
        except ConnectorError:
            raise
        except SQLAlchemyError as exc:
            raise ToolRuntimeError(f"connector '{self.id}' could not list tables") from exc

    def tables(self) -> list[str]:
        existing = self._existing_tables()
        if self.config.tables:
            return [t for t in self.config.tables if t in existing]
        return existing

    def _table(self, name: str) -> Table:
        allowed = self.tables()
        if name not in allowed:
            raise SchemaInvalidError(f"Unknown table '{name}' in connector '{self.id}'. Known: {allowed}")
        now = time.monotonic()
        cached = self._reflected.get(name)
        if cached and now - cached[0] < _REFLECT_TTL_S:
            return cached[1]
        try:
            table = Table(name, MetaData(), autoload_with=self.engine())
        except SQLAlchemyError as exc:
            raise ToolRuntimeError(f"connector '{self.id}' could not read the schema of '{name}'") from exc
        self._reflected[name] = (now, table)
        return table

    def schema(self, table: str, role: Optional[str] = None) -> list[ColumnInfo]:
        self._reflected.pop(table, None)  # schema lookups are how F4 recovery sees a renamed column
        hidden = self.hidden_columns(role)
        return [ColumnInfo(c.name, str(c.type), bool(c.nullable)) for c in self._table(table).columns if c.name not in hidden]

    # ---------------------------------------------------------------- query
    def query(self, spec: dict[str, Any], *, role: Optional[str] = None) -> dict[str, Any]:
        if not isinstance(spec, dict):
            raise SchemaInvalidError("sql_spec must be an object")
        name = spec.get("table")
        if not isinstance(name, str) or not name:
            raise SchemaInvalidError("sql_spec.table is required")
        table = self._table(name)
        hidden = self.hidden_columns(role)
        visible = {c.name: c for c in table.columns if c.name not in hidden}

        def col(column: Any):
            if column not in visible:
                raise SchemaInvalidError(f"unknown column '{column}'. available: {list(visible)}")
            return visible[column]

        orphan = spec.get("column")
        if isinstance(orphan, str) and orphan.strip():
            col(orphan)

        conditions = []
        filters = spec.get("filters") or []
        if not isinstance(filters, list):
            raise SchemaInvalidError("filters must be a list")
        for flt in filters:
            if not isinstance(flt, dict):
                raise SchemaInvalidError("each filter must be an object")
            column = col(flt.get("column"))
            op, value = flt.get("op", "eq"), flt.get("value")
            conditions.append(self._condition(column, op, value))

        group_by = list(spec.get("group_by") or [])
        agg = spec.get("agg") or {}
        group_cols = [col(g) for g in group_by]
        if group_by:
            if agg:
                outputs = [*group_cols, *self._aggregates(agg, col, grouped=True)]
            else:
                outputs = [*group_cols, func.count().label("count")]
        elif agg:
            outputs = self._aggregates(agg, col, grouped=False)
        else:
            wanted = spec.get("columns")
            if wanted:
                outputs = [col(c) for c in wanted]
            else:
                outputs = list(visible.values())

        stmt = select(*outputs)
        if conditions:
            stmt = stmt.where(*conditions)
        if group_cols:
            stmt = stmt.group_by(*group_cols)
        sort = spec.get("sort")
        if isinstance(sort, dict) and sort.get("column") in {getattr(o, "name", None) for o in outputs}:
            target = next(o for o in outputs if getattr(o, "name", None) == sort["column"])
            stmt = stmt.order_by(target.asc() if sort.get("ascending") else target.desc())
        limit = max(1, min(int(spec.get("limit") or 50), 200, self.config.row_cap))
        stmt = stmt.limit(limit)

        frame = self._run(stmt)
        return result_payload(frame, table=f"{self.id}.{name}", entity=self.entity)

    @staticmethod
    def _condition(column, op: str, value: Any):
        if op == "eq":
            return column == value
        if op == "ne":
            return column != value
        if op == "gt":
            return column > value
        if op == "gte":
            return column >= value
        if op == "lt":
            return column < value
        if op == "lte":
            return column <= value
        if op == "in":
            return column.in_(list(value) if isinstance(value, (list, tuple, set)) else [value])
        if op == "contains":
            return column.ilike(f"%{_escape_like(str(value))}%", escape="\\")
        raise SchemaInvalidError(f"unknown op '{op}'. allowed: ['contains', 'eq', 'gt', 'gte', 'in', 'lt', 'lte', 'ne']")

    @staticmethod
    def _aggregates(agg: dict[str, str], col, *, grouped: bool) -> list:
        out = []
        for name, fn in agg.items():
            column = col(name)
            if fn not in _AGGS:
                raise SchemaInvalidError(f"unknown agg '{fn}'. allowed on connectors: {sorted(_AGGS)}")
            label = name if grouped else f"{name}_{fn}"
            if fn in {"count", "size"}:
                expr = func.count() if (grouped and fn == "count") else func.count(column)
            elif fn == "mean":
                expr = func.avg(column)
            elif fn == "nunique":
                expr = func.count(distinct(column))
            else:
                expr = getattr(func, fn)(column)
            out.append(expr.label(label))
        return out

    def _run(self, stmt) -> pd.DataFrame:
        self._local.deadline = time.monotonic() + self.config.statement_timeout_s
        try:
            with self.engine().connect() as conn:
                rows = conn.execute(stmt).mappings().all()
        except ConnectorError:
            raise
        except SQLAlchemyError as exc:
            self._reflected.clear()
            raise ToolRuntimeError(f"connector '{self.id}' query failed ({type(exc).__name__})") from exc
        finally:
            self._local.deadline = None
        return pd.DataFrame([dict(r) for r in rows])
