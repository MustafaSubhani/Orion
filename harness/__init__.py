"""OrionHarness control plane."""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness.config import Settings, get_settings

if TYPE_CHECKING:  # the planner is imported lazily at run time; this keeps type checkers aware of the names
    from harness.planner import OrchestrationResult, coordinate, coordinate_stream

__all__ = ["OrchestrationResult", "Settings", "coordinate", "coordinate_stream", "get_settings"]


def __getattr__(name: str):
    if name in {"coordinate", "coordinate_stream", "OrchestrationResult"}:
        from harness import planner

        return getattr(planner, name)
    raise AttributeError(name)
