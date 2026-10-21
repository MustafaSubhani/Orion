"""Crash-safe, multi-process-safe file helpers.

Contract: `atomic_write` never leaves a half-written file at `path`. `file_lock` serialises
writers across threads and processes. Neither raises on a missing parent directory.
"""

from __future__ import annotations

import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

try:  # filelock is a hard dependency of the product extras, optional for the paper path
    from filelock import FileLock
except ImportError:  # pragma: no cover - fall back to an in-process lock
    FileLock = None  # type: ignore[assignment]

_thread_locks: dict[str, threading.RLock] = {}
_registry_lock = threading.Lock()


def _thread_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _registry_lock:
        return _thread_locks.setdefault(key, threading.RLock())


@contextmanager
def file_lock(path: Path | str, timeout: float = 15.0) -> Iterator[None]:
    """Hold an exclusive lock for `path` (a sibling `.lock` file carries the OS lock)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with _thread_lock(target):
        if FileLock is None:
            yield
            return
        with FileLock(str(target) + ".lock", timeout=timeout):
            yield


def atomic_write(path: Path | str, text: str, *, encoding: str = "utf-8") -> None:
    """Write `text` to `path` by writing a temp file in the same directory, then replacing."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
