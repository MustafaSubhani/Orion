"""Per-request artifact vault. Full tool payloads live here; the prompt only sees stubs.

Contract: workers never put megabyte tables in the LLM window. They store the blob,
send a preview + artifact_id, and may call session_get to expand one item.
Thread-safe: parallel DAG nodes share one store.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


@dataclass
class Artifact:
    id: str
    kind: str
    text: str
    tokens_est: int
    extra: dict[str, Any] = field(default_factory=dict)


class SessionStore:
    """Request-scoped. Do not share across users."""

    def __init__(self) -> None:
        self._items: dict[str, Artifact] = {}
        self._meta: dict[str, str] = {}
        self._lock = threading.RLock()

    def put(self, text: str, *, kind: str = "tool", extra: dict[str, Any] | None = None) -> Artifact:
        blob = text if isinstance(text, str) else str(text)
        art = Artifact(
            id=uuid4().hex[:12],
            kind=kind,
            text=blob,
            tokens_est=max(1, (len(blob) + 3) // 4),
            extra=extra or {},
        )
        with self._lock:
            self._items[art.id] = art
        return art

    def get(self, artifact_id: str) -> Artifact | None:
        with self._lock:
            return self._items.get(artifact_id)

    def recent(self, n: int = 8) -> list[Artifact]:
        with self._lock:
            items = list(self._items.values())
        if n <= 0:
            return []
        return items[-n:]

    def get_meta(self, key: str) -> str | None:
        """Small keyed cache (summaries, probes). Request-scoped like everything else here."""
        with self._lock:
            return self._meta.get(key)

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._meta[key] = value

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)
