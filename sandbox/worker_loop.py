"""Subprocess entry for generated workers. Injects a tiny SDK; no API keys in env."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: worker_loop.py <agent.py> <task>", file=sys.stderr)
        return 2
    module_path = Path(sys.argv[1])
    task = sys.argv[2]
    # Generated files must define `def answer(task: str) -> str` using only allow-listed imports.
    ns: dict = {}
    source = module_path.read_text(encoding="utf-8")
    # compile without builtins that can escape
    safe_builtins = {
        "abs": abs,
        "min": min,
        "max": max,
        "sum": sum,
        "len": len,
        "range": range,
        "enumerate": enumerate,
        "list": list,
        "dict": dict,
        "str": str,
        "int": int,
        "float": float,
        "bool": bool,
        "print": print,
        "True": True,
        "False": False,
        "None": None,
    }
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
