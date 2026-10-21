"""One AST policy for untrusted Python. Both the generated-worker gate and the benchmark security gate call it.

The strict rules are always on: no `eval`/`exec`/`open`, no dunder traversal, no reflection helpers, no
dangerous modules. The only knob is which imports are allowed. Generated workers get a small
allow-list of pure modules; the benchmark gate allows none.
"""

from __future__ import annotations

import ast
from typing import Optional

FORBIDDEN_CALLS = frozenset(
    {
        "eval", "exec", "compile", "open", "__import__", "getattr", "setattr",
        "delattr", "globals", "locals", "vars", "input", "breakpoint", "help",
    }
)
FORBIDDEN_MODULES = frozenset(
    {
        "os", "sys", "subprocess", "pty", "shutil", "socket", "urllib", "requests", "httpx",
        "aiohttp", "posix", "builtins", "__builtin__", "importlib", "pickle", "ctypes",
        "multiprocessing", "threading", "pathlib", "shelve", "signal", "ssl", "asyncio", "http",
    }
)
FORBIDDEN_ATTRS = frozenset(
    {
        "__class__", "__subclasses__", "__mro__", "__bases__", "__base__", "__dict__",
        "__globals__", "__code__", "__closure__", "__builtins__",
    }
)
SHELL_ATTRS = frozenset({"system", "popen", "environ"})
FORBIDDEN_IMPORT_PREFIXES = ("harness", "bench", "backend", "packs", "kb", "lab", "repair")

# Pure modules a generated worker may import. (They only run if the sandbox runner exposes them.)
WORKER_IMPORTS = frozenset({"json", "math", "re", "datetime", "typing", "dataclasses", "decimal", "statistics"})


class _Visitor(ast.NodeVisitor):
    def __init__(self, allow_imports: Optional[frozenset[str]]) -> None:
        self.allow = allow_imports
        self.violations: list[str] = []

    def _check_import(self, module: str, original: str, kind: str) -> None:
        top = module.split(".")[0] if module else ""
        if top in FORBIDDEN_MODULES or top.startswith(FORBIDDEN_IMPORT_PREFIXES):
            self.violations.append(f"banned import: {top}")
        elif self.allow is not None and top not in self.allow:
            if not self.allow:
                self.violations.append(f"{kind} forbidden: {original}")
            else:
                self.violations.append(f"import not on allow-list: {top}")

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._check_import(alias.name, alias.name, "Import")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        if node.level:  # relative imports reach into the package, never allowed
            self.violations.append("relative import forbidden")
        else:
            self._check_import(module, f"from {module} import ...", "ImportFrom")
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if isinstance(node.value, ast.Name) and node.value.id in {"__builtins__", "builtins"}:
            self.violations.append("Subscript access to __builtins__ forbidden")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Name):
            if func.id in FORBIDDEN_CALLS or func.id in FORBIDDEN_MODULES:
                self.violations.append(f"Call to forbidden function: {func.id}()")
        elif isinstance(func, ast.Attribute):
            if isinstance(func.value, ast.Name) and (
                func.value.id in FORBIDDEN_MODULES or func.value.id in {"__builtins__", "builtins"}
            ):
                self.violations.append(f"Call to forbidden module method: {func.value.id}.{func.attr}()")
            if func.attr in FORBIDDEN_CALLS:
                self.violations.append(f"Call to forbidden attribute function: .{func.attr}()")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in FORBIDDEN_ATTRS or node.attr.startswith("__"):
            self.violations.append(f"Access to private/dunder attribute forbidden: .{node.attr}")
        if node.attr in SHELL_ATTRS:
            self.violations.append(f"banned attribute: {node.attr}")
        if isinstance(node.value, ast.Name) and node.value.id in FORBIDDEN_MODULES:
            self.violations.append(f"Access to forbidden module attribute: {node.value.id}.{node.attr}")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in FORBIDDEN_MODULES and isinstance(node.ctx, ast.Load):
            self.violations.append(f"Reference to forbidden module: {node.id}")
        elif node.id in FORBIDDEN_CALLS and isinstance(node.ctx, ast.Load):
            self.violations.append(f"banned name: {node.id}")
        self.generic_visit(node)


def check_tree(tree: ast.AST, *, allow_imports: Optional[frozenset[str]]) -> list[str]:
    """Violations of the sandbox policy for a parsed module (empty list means it passes).

    `allow_imports=None` skips the import allow-list (the banned-module rule still applies);
    an empty set forbids every import.
    """
    visitor = _Visitor(allow_imports)
    visitor.visit(tree)
    return list(dict.fromkeys(visitor.violations))


def check_source(source: str, *, allow_imports: Optional[frozenset[str]]) -> list[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"syntax error: {exc}"]
    return check_tree(tree, allow_imports=allow_imports)
