"""OrionHarness control plane."""

from harness.config import Settings, get_settings

__all__ = ["Settings", "get_settings", "coordinate", "coordinate_stream"]


def __getattr__(name: str):
    if name in {"coordinate", "coordinate_stream", "OrchestrationResult"}:
        from harness import planner

        return getattr(planner, name)
    raise AttributeError(name)
