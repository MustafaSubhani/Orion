"""Append-only flight recorder. Eval reads traces; workers only append."""

from __future__ import annotations

import hashlib
import json
import random
import string
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional
from uuid import uuid4

from harness.config import TRACES_DIR, ensure_dirs
from harness.types import Span, SpanKind, utc_now

_write_lock = threading.Lock()


def new_request_id() -> str:
    now = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    rand = "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
    return f"ORION-{now}-{rand}"


def hash_payload(value: Any) -> str:
    blob = json.dumps(value, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


class Tracer:
    """One JSONL file per request plus a rolling audit log."""

    def __init__(self, traces_dir: Path | None = None) -> None:
        ensure_dirs()
        self.traces_dir = traces_dir or TRACES_DIR
        self.traces_dir.mkdir(parents=True, exist_ok=True)
        self.audit_path = self.traces_dir.parent / "audit_log.jsonl"
        self._spans: dict[str, list[Span]] = {}
        self._span_lock = threading.Lock()

    def _request_path(self, request_id: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in request_id)
        return self.traces_dir / f"{safe}.jsonl"

    def write_meta(self, request_id: str, meta: dict[str, Any]) -> None:
        """Ownership and summary for one request (product mode). Lets the API list and authorise traces."""
        from harness.fsutil import atomic_write

        safe = self._request_path(request_id).stem
        atomic_write(self.traces_dir / f"{safe}.meta.json", json.dumps(meta, ensure_ascii=False, default=str))

    def read_meta(self, request_id: str) -> Optional[dict[str, Any]]:
        safe = self._request_path(request_id).stem
        path = self.traces_dir / f"{safe}.meta.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def list_requests(self, *, user: Optional[str] = None, tenant: Optional[str] = None, limit: int = 50) -> list[dict[str, Any]]:
        """Newest-first request summaries, optionally filtered to one user or tenant."""
        paths = sorted(self.traces_dir.glob("*.meta.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        out: list[dict[str, Any]] = []
        for path in paths:
            try:
                meta = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if user is not None and meta.get("user") != user:
                continue
            if tenant is not None and meta.get("tenant", "default") != tenant:
                continue
            out.append(meta)
            if len(out) >= max(1, limit):
                break
        return out

    def append(self, span: Span) -> None:
        with self._span_lock:
            self._spans.setdefault(span.request_id, []).append(span)
        line = json.dumps(span.to_dict(), ensure_ascii=False, default=str)
        path = self._request_path(span.request_id)
        try:
            with _write_lock:
                with open(path, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
                with open(self.audit_path, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
        except OSError as exc:
            print(f"[TRACE] write failed: {exc}")

    def spans_for(self, request_id: str) -> list[Span]:
        with self._span_lock:
            cached = self._spans.get(request_id)
            if cached:
                return list(cached)
        path = self._request_path(request_id)
        if not path.exists():
            return []
        out: list[Span] = []
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                    out.append(
                        Span(
                            request_id=raw["request_id"],
                            span_id=raw["span_id"],
                            parent_id=raw.get("parent_id"),
                            kind=SpanKind(raw.get("kind", "tool")),
                            name=raw.get("name", ""),
                            started_at=raw.get("started_at", ""),
                            ended_at=raw.get("ended_at"),
                            latency_ms=float(raw.get("latency_ms") or 0),
                            args_hash=raw.get("args_hash", ""),
                            result_hash=raw.get("result_hash", ""),
                            error_class=raw.get("error_class"),
                            tokens_in=int(raw.get("tokens_in") or 0),
                            tokens_out=int(raw.get("tokens_out") or 0),
                            extra=raw.get("extra") or {},
                        )
                    )
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue
        return out

    def log_event(
        self,
        event_type: str,
        user: str,
        request_id: str,
        payload: dict[str, Any],
        role: Optional[str] = None,
    ) -> None:
        event = {
            "timestamp": utc_now(),
            "request_id": request_id,
            "user": user or "anonymous",
            "role": role or "unknown",
            "event_type": event_type,
            "payload": payload,
        }
        try:
            with _write_lock:
                with open(self.audit_path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        except OSError as exc:
            print(f"[TRACE] audit write failed: {exc}")

    @contextmanager
    def span(
        self,
        request_id: str,
        kind: SpanKind,
        name: str,
        *,
        parent_id: Optional[str] = None,
        args: Any = None,
        extra: Optional[dict[str, Any]] = None,
    ) -> Iterator[Span]:
        started = time.perf_counter()
        record = Span(
            request_id=request_id,
            span_id=uuid4().hex[:12],
            parent_id=parent_id,
            kind=kind,
            name=name,
            started_at=utc_now(),
            args_hash=hash_payload(args) if args is not None else "",
            extra=extra or {},
        )
        try:
            yield record
        except Exception as exc:
            record.error_class = type(exc).__name__
            record.extra["exception"] = str(exc)[:500]
            raise
        finally:
            record.ended_at = utc_now()
            record.latency_ms = (time.perf_counter() - started) * 1000
            self.append(record)


_TRACER: Optional[Tracer] = None


def get_tracer() -> Tracer:
    global _TRACER
    if _TRACER is None:
        _TRACER = Tracer()
    return _TRACER
