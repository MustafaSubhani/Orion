"""Wire the process-level Runtime once. Tests inject fakes through the keyword arguments."""

from __future__ import annotations

from typing import Optional

from harness.agent_registry import AgentRegistry, get_registry
from harness.config import ensure_dirs
from harness.context import Runtime, set_runtime
from harness.gateway import LLMGateway
from harness.healing import Healer
from harness.trace import Tracer, get_tracer


def build_runtime(
    *,
    gateway: Optional[LLMGateway] = None,
    tools=None,
    agents: Optional[AgentRegistry] = None,
    tracer: Optional[Tracer] = None,
    healer: Optional[Healer] = None,
    install: bool = True,
) -> Runtime:
    """Build a Runtime. Anything not passed gets the process default.

    `install=False` returns the runtime without making it the process singleton, so a test can
    hold a private runtime and pass it explicitly.
    """
    ensure_dirs()
    if tools is None:
        from harness.manifests import load_plugin_dir
        from harness.tool_registry import ToolRegistry

        tools = ToolRegistry()
        load_plugin_dir(tools)
    runtime = Runtime(
        gateway=gateway or LLMGateway(),
        tools=tools,
        agents=agents or get_registry(),
        tracer=tracer or get_tracer(),
        healer=healer or Healer(),
    )
    if install:
        set_runtime(runtime)
    return runtime
