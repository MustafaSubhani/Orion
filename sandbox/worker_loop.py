"""Subprocess entry for generated workers.

Order of operations (it matters): block on a `go` line so the parent can attach OS limits first,
then cut the network, then compile and run the generated file with a tiny builtins allow-list.
The environment is minimal and holds no secrets.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# The exact builtins a generated worker may use. A test pins this set.
SAFE_BUILTIN_NAMES = (
    "abs", "min", "max", "sum", "len", "range", "enumerate", "list", "dict", "str", "int", "float",
    "bool", "print", "round", "sorted", "set", "tuple", "zip", "any", "all", "map", "filter", "isinstance",
)


def _cut_network() -> None:
    """Make sockets unusable even if some allowed module reaches for them."""
    import socket

    class _NoNetwork(socket.socket):
        def __init__(self, *args, **kwargs):
            raise PermissionError("network is disabled in the sandbox")

    socket.socket = _NoNetwork  # type: ignore[misc,assignment]
    socket.create_connection = lambda *a, **k: (_ for _ in ()).throw(PermissionError("network is disabled"))  # type: ignore[assignment]
    socket.getaddrinfo = lambda *a, **k: (_ for _ in ()).throw(PermissionError("network is disabled"))  # type: ignore[assignment]


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: worker_loop.py <agent.py> <task>", file=sys.stderr)
        return 2
    if sys.stdin.readline().strip() != "go":  # parent attaches its limits, then releases us
        return 3
    module_path = Path(sys.argv[1])
    task = sys.argv[2]
    _cut_network()
    import builtins

    safe_builtins = {name: getattr(builtins, name) for name in SAFE_BUILTIN_NAMES}
    safe_builtins.update({"True": True, "False": False, "None": None})
    ns: dict = {}
    source = module_path.read_text(encoding="utf-8")
    try:
        exec(compile(source, str(module_path), "exec"), {"__builtins__": safe_builtins}, ns)  # noqa: S102
    except Exception as exc:
        print(f"load error: {exc}", file=sys.stderr)
        return 1
    fn = ns.get("answer")
    if not callable(fn):
        print("generated agent must define answer(task) -> str", file=sys.stderr)
        return 1
    try:
        result = fn(task)
    except MemoryError:
        print("run error: memory limit reached", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"run error: {exc}", file=sys.stderr)
        return 1
    if isinstance(result, (dict, list)):
        print(json.dumps(result, default=str))
    else:
        print(str(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
