"""Isolated Lab workspace for tests: a temp generated-agents folder, custom-agent file and version store."""

from __future__ import annotations

import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

SQLITE_ROWS = [
    (1, "Ada", "eng", 120.0),
    (2, "Bob", "eng", 100.0),
    (3, "Cy", "ops", 80.0),
    (4, "Di", "ops", 90.0),
    (5, "Ed", "hr", 70.0),
    (6, "Flo", "hr", 75.0),
]


@contextmanager
def lab_workspace():
    """Patch every module that holds a Lab path, then install a fresh AgentRegistry. Restores on exit."""
    from harness import agent_registry, versions

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        generated = root / "generated"
        generated.mkdir()
        with patch.multiple(agent_registry, LAB_GENERATED_DIR=generated, CUSTOM_PATH=root / "custom_agents.json"), patch.object(
            versions, "VERSIONS_PATH", root / "versions.json"
        ), patch("lab.synthesizer.LAB_GENERATED_DIR", generated), patch("lab.evaluate.LAB_GENERATED_DIR", generated):
            previous = agent_registry._REGISTRY
            agent_registry._REGISTRY = agent_registry.AgentRegistry()
            try:
                yield agent_registry._REGISTRY, generated
            finally:
                agent_registry._REGISTRY = previous


def make_sqlite(path: Path) -> None:
    import sqlite3

    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE employees (id INTEGER, name TEXT, dept TEXT, salary REAL)")
    conn.executemany("INSERT INTO employees VALUES (?,?,?,?)", SQLITE_ROWS)
    conn.commit()
    conn.close()
