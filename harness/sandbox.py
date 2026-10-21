"""Benchmark-facing code security gate (a thin wrapper over `sandbox.policy`).

`validate_code_ast` is called by the security benchmark. It forbids every import and applies the
shared strict rules (no eval/exec/open, no dunder traversal, no reflection helpers, no dangerous
modules). There is no code-execution path in this module.
"""

from __future__ import annotations

import ast

from sandbox.policy import check_tree


def validate_code_ast(code: str) -> tuple[bool, list[str]]:
    """Validate Python code against AST security policies."""
    # First check for SQL injection style directives or raw control chars
    lowered = code.lower()
    if "drop table" in lowered or "delete from" in lowered or "insert into" in lowered or "xp_cmdshell" in lowered:
        return False, ["SQL modification statement blocked by policy"]
    if "../" in code or "..\\" in code:
        return False, ["Path traversal sequence blocked by policy"]

    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        # If it doesn't parse as Python, check if it contains obvious blocked tokens
        for forbidden in ("import ", "subprocess", "os.system", "eval(", "exec(", "open(", "socket"):
            if forbidden in lowered:
                return False, [f"Forbidden keyword in unparseable script: {forbidden}"]
        return False, [f"SyntaxError: {e}"]

    violations = check_tree(tree, allow_imports=frozenset())
    if violations:
        return False, violations
    return True, []
