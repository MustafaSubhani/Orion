"""Conversation threads: durable multi-turn memory for live chat, private to their owner.

A thread keeps every message plus a rolling summary of the oldest turns. The summary is produced
by `harness.summarizer` (verified, with an extractive fallback), and `summary_upto` records the last
message folded into it, so the prompt always gets `summary + messages after summary_upto`.

Contract: every read is scoped by tenant and owner. Administrators do not read other users' chats.
Eval mode never touches threads.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Optional

from harness import statedb

statedb.register_schema(
    """
    CREATE TABLE IF NOT EXISTS threads (
        id TEXT PRIMARY KEY,
        tenant TEXT NOT NULL DEFAULT 'default',
        username TEXT NOT NULL,
        title TEXT NOT NULL DEFAULT '',
        summary TEXT NOT NULL DEFAULT '',
        summary_upto INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_threads_owner ON threads (tenant, username, updated_at);
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        thread_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        request_id TEXT,
        created_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages (thread_id, id);
    """
)

MAX_TITLE = 80


def _est_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


def create(*, tenant: str, user: str, title: str = "") -> dict[str, Any]:
    now = time.time()
    thread_id = "thr_" + uuid.uuid4().hex[:12]
    conn = statedb.connect()
    try:
        conn.execute(
            "INSERT INTO threads (id, tenant, username, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (thread_id, tenant, user, " ".join(title.split())[:MAX_TITLE], now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return get(thread_id, tenant, user) or {}


def get(thread_id: str, tenant: str, user: str) -> Optional[dict[str, Any]]:
    """The thread if it exists and belongs to this user and tenant, else None."""
    conn = statedb.connect()
    try:
        row = conn.execute(
            "SELECT * FROM threads WHERE id=? AND tenant=? AND username=?", (thread_id, tenant, user)
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row is not None else None


def list_for(*, tenant: str, user: str, limit: int = 50) -> list[dict[str, Any]]:
    conn = statedb.connect()
    try:
        rows = conn.execute(
            "SELECT id, title, created_at, updated_at, "
            "(SELECT COUNT(*) FROM messages m WHERE m.thread_id = threads.id) AS messages "
            "FROM threads WHERE tenant=? AND username=? ORDER BY updated_at DESC LIMIT ?",
            (tenant, user, max(1, min(limit, 200))),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def rename(thread_id: str, tenant: str, user: str, title: str) -> bool:
    conn = statedb.connect()
    try:
        cur = conn.execute(
            "UPDATE threads SET title=? WHERE id=? AND tenant=? AND username=?",
            (" ".join(title.split())[:MAX_TITLE], thread_id, tenant, user),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def delete(thread_id: str, tenant: str, user: str) -> bool:
    if get(thread_id, tenant, user) is None:
        return False
    conn = statedb.connect()
    try:
        conn.execute("DELETE FROM messages WHERE thread_id=?", (thread_id,))
        conn.execute("DELETE FROM threads WHERE id=?", (thread_id,))
        conn.commit()
    finally:
        conn.close()
    return True


def append(thread_id: str, role: str, content: str, request_id: Optional[str] = None) -> int:
    now = time.time()
    conn = statedb.connect()
    try:
        cur = conn.execute(
            "INSERT INTO messages (thread_id, role, content, request_id, created_at) VALUES (?, ?, ?, ?, ?)",
            (thread_id, role, content, request_id, now),
        )
        conn.execute("UPDATE threads SET updated_at=? WHERE id=?", (now, thread_id))
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def messages(thread_id: str, *, after_id: int = 0, limit: Optional[int] = None) -> list[dict[str, Any]]:
    query = "SELECT id, role, content, request_id, created_at FROM messages WHERE thread_id=? AND id>? ORDER BY id"
    params: list[Any] = [thread_id, after_id]
    if limit:
        query += " LIMIT ?"
        params.append(limit)
    conn = statedb.connect()
    try:
        rows = conn.execute(query, params).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def set_summary(thread_id: str, summary: str, upto: int) -> None:
    conn = statedb.connect()
    try:
        conn.execute("UPDATE threads SET summary=?, summary_upto=? WHERE id=?", (summary, upto, thread_id))
        conn.commit()
    finally:
        conn.close()


def history_text(thread: dict[str, Any], *, max_tokens: int = 1500) -> str:
    """Prompt context for the next turn: the rolling summary plus the turns after it.

    The newest turns win when the budget is tight; the summary is trimmed from its end.
    """
    recent = messages(thread["id"], after_id=int(thread.get("summary_upto") or 0))
    lines = [f"{m['role']}: {m['content']}" for m in recent]
    tail: list[str] = []
    used = 0
    for line in reversed(lines):
        cost = _est_tokens(line)
        if used + cost > max_tokens and tail:
            break
        tail.append(line[: max_tokens * 4])
        used += cost
    tail.reverse()
    parts: list[str] = []
    summary = (thread.get("summary") or "").strip()
    room = max(0, max_tokens - used)
    if summary and room > 50:
        parts.append("Summary of earlier turns:\n" + summary[: room * 4])
    if tail:
        parts.append("Recent turns:\n" + "\n".join(tail))
    return "\n\n".join(parts)


def unsummarized_tokens(thread: dict[str, Any]) -> int:
    recent = messages(thread["id"], after_id=int(thread.get("summary_upto") or 0))
    return sum(_est_tokens(m["content"]) for m in recent) + _est_tokens(thread.get("summary") or "")
