"""AST static gate for generated workers. No pandas, no network, never executes the file."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

BANNED_IMPORTS = {
    "os",
    "subprocess",
    "socket",
    "pathlib",
    "shutil",
    "sys",
    "ctypes",
    "importlib",
    "multiprocessing",
    "requests",
    "httpx",
    "urllib",
    "pickle",
    "shelve",
    "pty",
    "signal",
}
BANNED_NAMES = {"eval", "exec", "compile", "__import__", "open", "input", "breakpoint"}
ALLOWED_CODE_IMPORTS = {"json", "math", "re", "datetime", "typing", "dataclasses", "decimal", "statistics"}


@dataclass
class GateResult:
    ok: bool
    reason: str = "ok"


def static_gate(path: Path) -> GateResult:
    """Reject generated files that could escape the sandbox. Never execute the file."""
    if not path.exists():
        return GateResult(False, f"missing {path}")
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        return GateResult(False, str(exc))
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return GateResult(False, f"syntax error: {exc}")
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            else:
                names = [node.module.split(".")[0]] if node.module else []
            for name in names:
                if name in BANNED_IMPORTS or (name and name.startswith(("harness", "bench"))):
                    return GateResult(False, f"banned import: {name}")
                if name not in ALLOWED_CODE_IMPORTS and name not in {"workers", "sandbox"}:
                    if name not in {"pandas", "numpy"}:
                        return GateResult(False, f"import not on allow-list: {name}")
        if isinstance(node, ast.Name) and node.id in BANNED_NAMES:
            return GateResult(False, f"banned name: {node.id}")
        if isinstance(node, ast.Attribute) and node.attr in {"system", "popen", "environ"}:
            return GateResult(False, f"banned attribute: {node.attr}")
    return GateResult(True, "ok")
