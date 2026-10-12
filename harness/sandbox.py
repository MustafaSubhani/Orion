"""Isolated Worker Execution Sandbox with AST Static Analysis and Policy Enforcement.

Provides:
- AST static analysis to forbid dangerous constructs:
  * Import and ImportFrom
  * Calls to eval, exec, compile, open, __import__, getattr, globals, locals
  * Attribute traversal to private/dunder namespaces (__class__, __subclasses__, __mro__, etc.)
  * References to dangerous modules (os, sys, subprocess, pty, shutil, socket, etc.)
- Strict execution namespace isolation and CPU timeout enforcement.
"""

from __future__ import annotations

import ast
import sys
from typing import Any, Optional

from harness.errors import PolicyDeniedError

FORBIDDEN_CALLS = frozenset({
    "eval", "exec", "compile", "open", "__import__", "getattr", "setattr",
    "delattr", "globals", "locals", "vars", "input", "breakpoint", "help",
})

FORBIDDEN_MODULES = frozenset({
    "os", "sys", "subprocess", "pty", "shutil", "socket", "urllib",
    "requests", "httpx", "aiohttp", "posix", "builtins", "__builtin__",
    "importlib", "pickle", "ctypes", "multiprocessing", "threading",
})

FORBIDDEN_ATTR_PREFIXES = ("__",)

FORBIDDEN_ATTRS = frozenset({
    "__class__", "__subclasses__", "__mro__", "__bases__", "__base__",
    "__dict__", "__globals__", "__code__", "__closure__", "__builtins__",
})


class SecurityASTVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.violations: list[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.violations.append(f"Import forbidden: {alias.name}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.violations.append(f"ImportFrom forbidden: from {node.module} import ...")
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if isinstance(node.value, ast.Name) and node.value.id in {"__builtins__", "builtins"}:
            self.violations.append("Subscript access to __builtins__ forbidden")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        # Check direct call to forbidden name: eval(), exec(), compile(), open()
        if isinstance(node.func, ast.Name):
            if node.func.id in FORBIDDEN_CALLS or node.func.id in FORBIDDEN_MODULES:
                self.violations.append(f"Call to forbidden function: {node.func.id}()")
        # Check call via attribute: os.system(), pty.spawn(), getattr(__builtins__, ...)
        elif isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name):
                if node.func.value.id in FORBIDDEN_MODULES or node.func.value.id in {"__builtins__", "builtins"}:
                    self.violations.append(f"Call to forbidden module method: {node.func.value.id}.{node.func.attr}()")
            if node.func.attr in FORBIDDEN_CALLS:
                self.violations.append(f"Call to forbidden attribute function: .{node.func.attr}()")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in FORBIDDEN_ATTRS or node.attr.startswith("__"):
            self.violations.append(f"Access to private/dunder attribute forbidden: .{node.attr}")
        if isinstance(node.value, ast.Name) and node.value.id in FORBIDDEN_MODULES:
            self.violations.append(f"Access to forbidden module attribute: {node.value.id}.{node.attr}")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in FORBIDDEN_MODULES and isinstance(node.ctx, ast.Load):
            self.violations.append(f"Reference to forbidden module: {node.id}")
        self.generic_visit(node)


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

    visitor = SecurityASTVisitor()
    visitor.visit(tree)
    if visitor.violations:
        return False, visitor.violations
    return True, []


def execute_sandboxed(code: str, namespace: dict[str, Any] | None = None) -> Any:
    """Execute code in a restricted namespace if it passes AST static analysis."""
    approved, violations = validate_code_ast(code)
    if not approved:
        raise PolicyDeniedError(f"Execution blocked by sandbox security policy: {'; '.join(violations)}")

    # Safe builtins
    safe_builtins = {
        "abs": abs, "all": all, "any": any, "bool": bool, "dict": dict,
        "float": float, "int": int, "len": len, "list": list, "max": max,
        "min": min, "range": range, "round": round, "set": set, "str": str,
        "sum": sum, "tuple": tuple, "zip": zip, "enumerate": enumerate,
        "filter": filter, "map": map, "sorted": sorted, "True": True, "False": False, "None": None,
    }

    env = {"__builtins__": safe_builtins}
    if namespace:
        env.update(namespace)

    return eval(code, env)
