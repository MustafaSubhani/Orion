"""In-process counters and summaries rendered as Prometheus text. No external dependency.

Contract: every call is O(1) and thread-safe, label values are escaped, and nothing here can raise
into a request path.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from typing import Mapping

_lock = threading.Lock()
_counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)
_sums: dict[tuple[str, tuple[tuple[str, str], ...]], list[float]] = defaultdict(lambda: [0.0, 0.0])
_help: dict[str, str] = {}


def _key(name: str, labels: Mapping[str, object] | None) -> tuple[str, tuple[tuple[str, str], ...]]:
    items = tuple(sorted((str(k), str(v)) for k, v in (labels or {}).items()))
    return name, items


def describe(name: str, text: str) -> None:
    _help[name] = text


def inc(name: str, labels: Mapping[str, object] | None = None, value: float = 1.0) -> None:
    with _lock:
        _counters[_key(name, labels)] += value


def observe(name: str, value: float, labels: Mapping[str, object] | None = None) -> None:
    """Record one observation; exported as `<name>_sum` and `<name>_count`."""
    with _lock:
        pair = _sums[_key(name, labels)]
        pair[0] += float(value)
        pair[1] += 1.0


def reset() -> None:
    with _lock:
        _counters.clear()
        _sums.clear()


def _fmt(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    body = ",".join(
        '{}="{}"'.format(k, v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")) for k, v in labels
    )
    return "{" + body + "}"


def snapshot() -> dict[str, float]:
    """Flat `name{labels}` to value map, used by tests."""
    with _lock:
        out = {f"{n}{_fmt(l)}": v for (n, l), v in _counters.items()}
        for (n, l), (total, count) in _sums.items():
            out[f"{n}_sum{_fmt(l)}"] = total
            out[f"{n}_count{_fmt(l)}"] = count
    return out


def render() -> str:
    lines: list[str] = []
    with _lock:
        counters = sorted(_counters.items())
        sums = sorted(_sums.items())
    seen: set[str] = set()
    for (name, labels), value in counters:
        if name not in seen:
            seen.add(name)
            if name in _help:
                lines.append(f"# HELP {name} {_help[name]}")
            lines.append(f"# TYPE {name} counter")
        lines.append(f"{name}{_fmt(labels)} {value:g}")
    for (name, labels), (total, count) in sums:
        if name not in seen:
            seen.add(name)
            if name in _help:
                lines.append(f"# HELP {name} {_help[name]}")
            lines.append(f"# TYPE {name} summary")
        lines.append(f"{name}_sum{_fmt(labels)} {total:g}")
        lines.append(f"{name}_count{_fmt(labels)} {count:g}")
    return "\n".join(lines) + "\n"


describe("orion_http_requests_total", "HTTP requests by route and status")
describe("orion_http_request_seconds", "HTTP request latency")
describe("orion_tool_calls_total", "Tool calls by verb and outcome")
describe("orion_tool_seconds", "Tool latency by verb")
describe("orion_llm_tokens_total", "LLM tokens by purpose and direction")
describe("orion_heal_total", "Heal actions by failure class")
describe("orion_guard_flags_total", "Guardrail detections by stage")
