"""Shared SQLite state for product features (settings, threads, approvals).

Contract: `connect()` returns a short-lived connection with WAL and a busy timeout, and applies every
registered DDL once per database path. The path is read on every call so tests can point
`ORION_STATE_DB` at a temp file. The benchmark path never touches this module.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path

from harness.config import PROJECT_ROOT

_DDL: list[str] = []
_applied: set[str] = set()
_lock = threading.Lock()


def db_path() -> Path:
    raw = os.getenv("ORION_STATE_DB", "").strip()
    return Path(raw) if raw else PROJECT_ROOT / "orion_state.db"


def register_schema(ddl: str) -> None:
    """Add DDL (idempotent `CREATE ... IF NOT EXISTS`) to be applied on the next connect."""
    with _lock:
        if ddl not in _DDL:
            _DDL.append(ddl)
            _applied.clear()


def connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    key = str(path.resolve())
    with _lock:
        pending = key not in _applied
        ddl = list(_DDL)
    if pending:
        for statement in ddl:
            conn.executescript(statement)
        conn.commit()
        with _lock:
            _applied.add(key)
    return conn
