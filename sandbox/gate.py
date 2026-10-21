"""AST static gate for generated workers. Never executes the file.

The rules live in `sandbox.policy` and are shared with the benchmark security gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sandbox.policy import FORBIDDEN_MODULES, WORKER_IMPORTS, check_source

# Kept for compatibility with code that imported the old constants.
BANNED_IMPORTS = set(FORBIDDEN_MODULES)
ALLOWED_CODE_IMPORTS = set(WORKER_IMPORTS)


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
    violations = check_source(source, allow_imports=WORKER_IMPORTS)
    if violations:
        return GateResult(False, violations[0])
    return GateResult(True, "ok")
