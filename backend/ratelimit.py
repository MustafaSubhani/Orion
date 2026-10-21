"""Sliding-window limiter for login attempts. In-process, which is enough for one API process.

Contract: `check` raises 429 when a key has failed too often inside the window. `fail` records a
failure and `success` clears the key. Keys combine username and client address so one attacker
cannot lock a victim out from another address.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from backend.errors import ApiError


class LoginThrottle:
    def __init__(self, max_failures: int = 5, window_s: float = 300.0) -> None:
        self.max_failures = max_failures
        self.window_s = window_s
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque[float]:
        events = self._events[key]
        while events and now - events[0] > self.window_s:
            events.popleft()
        return events

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            if len(self._prune(key, now)) >= self.max_failures:
                raise ApiError(429, "rate_limited", "Too many failed attempts. Try again in a few minutes.")

    def fail(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            self._prune(key, now).append(now)

    def success(self, key: str) -> None:
        with self._lock:
            self._events.pop(key, None)

    def reset(self) -> None:
        with self._lock:
            self._events.clear()


login_throttle = LoginThrottle()
