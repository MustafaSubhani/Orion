"""Human approval for tools that change something (WRITE side effect).

Policy (`tools.write_policy`): `deny` blocks every write, `allow` runs them, `confirm` (default)
blocks the call and records a pending approval. An administrator approves or denies it, and an
approval is a single-use grant for the same user, tool and arguments, valid for a limited time.
Eval mode always denies writes.

The model never decides: the grant is checked in the tool registry before the handler runs.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any, Optional

from harness import statedb

GRANT_TTL_S = 3600.0

statedb.register_schema(
    """
    CREATE TABLE IF NOT EXISTS approvals (
        id TEXT PRIMARY KEY,
        tenant TEXT NOT NULL DEFAULT 'default',
        username TEXT NOT NULL,
        request_id TEXT,
        verb TEXT NOT NULL,
        args_hash TEXT NOT NULL,
        args_preview TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at REAL NOT NULL,
        decided_by TEXT,
        decided_at REAL,
        consumed_at REAL
    );
    CREATE INDEX IF NOT EXISTS idx_approvals_lookup ON approvals (tenant, username, verb, args_hash, status);
    """
)


def args_hash(args: dict[str, Any]) -> str:
    blob = json.dumps(args or {}, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _row(row) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def request(*, tenant: str, user: str, request_id: str, verb: str, args: dict[str, Any]) -> dict[str, Any]:
    """Record (or reuse) a pending approval for this exact call and return it."""
    digest = args_hash(args)
    conn = statedb.connect()
    try:
        existing = conn.execute(
            "SELECT * FROM approvals WHERE tenant=? AND username=? AND verb=? AND args_hash=? AND status='pending'",
            (tenant, user, verb, digest),
        ).fetchone()
        if existing is not None:
            return _row(existing)
        approval_id = "apr_" + uuid.uuid4().hex[:12]
        preview = json.dumps(args or {}, default=str, ensure_ascii=False)[:500]
        conn.execute(
            "INSERT INTO approvals (id, tenant, username, request_id, verb, args_hash, args_preview, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
            (approval_id, tenant, user, request_id, verb, digest, preview, time.time()),
        )
        conn.commit()
        return _row(conn.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone())
    finally:
        conn.close()


def consume_grant(*, tenant: str, user: str, verb: str, args: dict[str, Any]) -> Optional[str]:
    """Use up one valid approval for this exact call. Returns its id, or None if there is none."""
    digest = args_hash(args)
    cutoff = time.time() - GRANT_TTL_S
    conn = statedb.connect()
    try:
        row = conn.execute(
            "SELECT id FROM approvals WHERE tenant=? AND username=? AND verb=? AND args_hash=? "
            "AND status='approved' AND consumed_at IS NULL AND decided_at >= ? ORDER BY decided_at LIMIT 1",
            (tenant, user, verb, digest, cutoff),
        ).fetchone()
        if row is None:
            return None
        cur = conn.execute("UPDATE approvals SET consumed_at=? WHERE id=? AND consumed_at IS NULL", (time.time(), row["id"]))
        conn.commit()
        return row["id"] if cur.rowcount else None  # a concurrent caller may have taken it first
    finally:
        conn.close()


def decide(approval_id: str, *, approver: str, tenant: str, approve: bool) -> Optional[dict[str, Any]]:
    """Approve or deny a pending request. Returns the updated row, or None if unknown or already decided."""
    conn = statedb.connect()
    try:
        cur = conn.execute(
            "UPDATE approvals SET status=?, decided_by=?, decided_at=? WHERE id=? AND tenant=? AND status='pending'",
            ("approved" if approve else "denied", approver, time.time(), approval_id, tenant),
        )
        conn.commit()
        if not cur.rowcount:
            return None
        return _row(conn.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone())
    finally:
        conn.close()


def get(approval_id: str, tenant: str) -> Optional[dict[str, Any]]:
    conn = statedb.connect()
    try:
        row = conn.execute("SELECT * FROM approvals WHERE id=? AND tenant=?", (approval_id, tenant)).fetchone()
    finally:
        conn.close()
    return _row(row) if row is not None else None


def list_for(*, tenant: str, user: Optional[str] = None, status: Optional[str] = None, limit: int = 100) -> list[dict[str, Any]]:
    query = "SELECT * FROM approvals WHERE tenant=?"
    params: list[Any] = [tenant]
    if user is not None:
        query += " AND username=?"
        params.append(user)
    if status:
        query += " AND status=?"
        params.append(status)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(max(1, min(limit, 500)))
    conn = statedb.connect()
    try:
        rows = conn.execute(query, params).fetchall()
    finally:
        conn.close()
    return [_row(r) for r in rows]
