"""Wire the process-level Runtime once."""

from __future__ import annotations

from harness.agent_registry import AgentRegistry, get_registry
from harness.context import Runtime, set_runtime
from harness.gateway import LLMGateway
from harness.healing import Healer
from harness.trace import get_tracer
from harness.config import ensure_dirs


def build_runtime() -> Runtime:
    ensure_dirs()
    from harness.manifests import load_plugin_dir
    from harness.tool_registry import ToolRegistry

    tools = ToolRegistry()
    load_plugin_dir(tools)
    runtime = Runtime(
        gateway=LLMGateway(),
        tools=tools,
        agents=get_registry(),
        tracer=get_tracer(),
        healer=Healer(),
    )
    set_runtime(runtime)
    return runtime
