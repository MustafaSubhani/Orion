"""MCP client: connect external MCP servers and expose their tools to agents.

`config/mcp_servers.json` (or `ORION_MCP_SERVERS`) declares servers over stdio or streamable HTTP.
Secrets stay out of the file: `${VAR}` in `env`, `headers` and `url` is read from the environment.

Contract:
- Each remote tool becomes a registry tool named `mcp__<server>__<tool>` (provider tool names cannot
  contain dots). It gets the same circuit breaker, tracing, guard scanning and timeouts as any tool.
- A tool is READ only if the server marks it read-only (or the server config says so); every other
  tool is WRITE and goes through the approval policy. Unannotated tools default to WRITE.
- Live chat only. Eval mode and `mcp.enabled=false` deny every call.
- One background event loop owns every session, so sessions open and close in the task that made them.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import json
import logging
import os
import re
import threading
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from harness.config import PROJECT_ROOT
from harness.connectors.base import scrub
from harness.context import RequestContext, is_product
from harness.errors import PolicyDeniedError, ToolRuntimeError
from harness.settings_store import effective
from harness.types import SideEffect, ToolManifest

log = logging.getLogger("orion.mcp")
_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_BAD_NAME = re.compile(r"[^A-Za-z0-9_-]")
VERB_PREFIX = "mcp__"


def config_path() -> Path:
    raw = os.getenv("ORION_MCP_SERVERS", "").strip()
    return Path(raw) if raw else PROJECT_ROOT / "config" / "mcp_servers.json"


def _interpolate(value: str) -> str:
    """Replace `${VAR}` from the environment. A missing variable is an error, never an empty secret."""

    def sub(match: re.Match) -> str:
        name = match.group(1)
        if name not in os.environ:
            raise ValueError(f"environment variable {name} is not set")
        return os.environ[name]

    return _ENV_REF.sub(sub, value)


@dataclass
class ServerConfig:
    name: str
    transport: str = "stdio"  # stdio | streamable_http
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str = ""
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    timeout_s: float = 30.0
    default_side_effect: str = "write"  # for tools the server does not mark read-only
    allow: list[str] = field(default_factory=list)  # expose only these tools (empty means all)
    agents: list[str] = field(default_factory=list)  # agent ids that get the tools automatically; ["*"] for all
    search_tool: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ServerConfig":
        name = str(data.get("name") or "")
        if not name or _BAD_NAME.search(name) or "__" in name:
            raise ValueError(f"server name '{name}' must use letters, digits, '-' or '_' (no double underscore)")
        transport = str(data.get("transport") or "stdio")
        if transport not in {"stdio", "streamable_http"}:
            raise ValueError(f"unknown transport '{transport}'")
        side_effect = str(data.get("default_side_effect") or "write")
        if side_effect not in {"read", "write"}:
            raise ValueError("default_side_effect must be 'read' or 'write'")
        if transport == "stdio" and not data.get("command"):
            raise ValueError("stdio servers need a command")
        if transport == "streamable_http" and not data.get("url"):
            raise ValueError("streamable_http servers need a url")
        return cls(
            name=name,
            transport=transport,
            command=_interpolate(str(data.get("command") or "")),
            args=[_interpolate(str(a)) for a in data.get("args") or []],
            env={str(k): _interpolate(str(v)) for k, v in (data.get("env") or {}).items()},
            cwd=str(data.get("cwd") or ""),
            url=_interpolate(str(data.get("url") or "")),
            headers={str(k): _interpolate(str(v)) for k, v in (data.get("headers") or {}).items()},
            timeout_s=max(1.0, float(data.get("timeout_s") or 30.0)),
            default_side_effect=side_effect,
            allow=[str(t) for t in data.get("allow") or []],
            agents=[str(a) for a in data.get("agents") or []],
            search_tool=str(data.get("search_tool") or ""),
        )


@dataclass
class _Server:
    config: ServerConfig
    session: Any = None
    tools: list[Any] = field(default_factory=list)
    error: str = ""
    ready: threading.Event = field(default_factory=threading.Event)
    stop: Optional[asyncio.Event] = None
    task: Optional[concurrent.futures.Future] = None

    def status(self) -> dict[str, Any]:
        return {
            "name": self.config.name,
            "transport": self.config.transport,
            "connected": self.session is not None,
            "tools": len(self.exposed_tools()),
            "error": self.error or None,
        }

    def exposed_tools(self) -> list[Any]:
        allow = set(self.config.allow)
        return [t for t in self.tools if not allow or t.name in allow]


def _attr(obj: Any, *names: str, default: Any = None) -> Any:
    """Read a field that is snake_case in mcp 2.x and camelCase in mcp 1.x."""
    for name in names:
        value = getattr(obj, name, None)
        if value is not None:
            return value
    return default


def verb_name(server: str, tool: str) -> str:
    """Provider-safe tool name, at most 64 characters, stable for a given (server, tool)."""
    safe = _BAD_NAME.sub("_", tool)
    verb = f"{VERB_PREFIX}{server}__{safe}"
    if len(verb) > 64:
        digest = hashlib.sha1(verb.encode()).hexdigest()[:8]
        verb = verb[:55] + "_" + digest
    return verb


def flatten_result(result: Any) -> dict[str, Any]:
    """Reduce a CallToolResult to text, an error flag and structured content."""
    parts: list[str] = []
    for block in _attr(result, "content", default=[]) or []:
        text = getattr(block, "text", None)
        parts.append(text if isinstance(text, str) else f"[{getattr(block, 'type', 'content')}]")
    return {
        "text": "\n".join(parts),
        "is_error": bool(_attr(result, "is_error", "isError", default=False)),
        "structured": _attr(result, "structured_content", "structuredContent"),
    }


def _http_streams(cfg: ServerConfig):
    try:
        from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

        client = create_mcp_http_client(headers=cfg.headers or None)
        return streamable_http_client(cfg.url, http_client=client)
    except ImportError:  # mcp 1.x
        from mcp.client.streamable_http import streamablehttp_client

        return streamablehttp_client(cfg.url, headers=cfg.headers or None)


class McpBridge:
    def __init__(self) -> None:
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()
        self._servers: dict[str, _Server] = {}

    # ---------------------------------------------------------------- loop and lifecycle
    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is None:
                loop = asyncio.new_event_loop()
                thread = threading.Thread(target=loop.run_forever, name="orion-mcp-loop", daemon=True)
                thread.start()
                self._loop, self._thread = loop, thread
            return self._loop

    async def _open(self, stack: AsyncExitStack, cfg: ServerConfig):
        from mcp import ClientSession

        if cfg.transport == "stdio":
            from mcp import StdioServerParameters
            from mcp.client.stdio import stdio_client

            params = StdioServerParameters(command=cfg.command, args=cfg.args, env=cfg.env or None, cwd=cfg.cwd or None)
            streams = await stack.enter_async_context(stdio_client(params))
        else:
            streams = await stack.enter_async_context(_http_streams(cfg))
        return await stack.enter_async_context(ClientSession(streams[0], streams[1]))

    async def _run(self, server: _Server) -> None:
        cfg = server.config
        server.stop = asyncio.Event()
        try:
            async with AsyncExitStack() as stack:
                session = await self._open(stack, cfg)
                await asyncio.wait_for(session.initialize(), cfg.timeout_s)
                listed = await asyncio.wait_for(session.list_tools(), cfg.timeout_s)
                server.tools = list(listed.tools)
                server.session = session
                server.ready.set()
                await server.stop.wait()
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # includes ExceptionGroup from the transport's task group
            server.error = f"{type(exc).__name__}: {scrub(str(exc))[:200]}"
            log.warning("MCP server %s failed: %s", cfg.name, server.error)
        finally:
            server.session = None
            server.ready.set()

    def connect(self, cfg: ServerConfig) -> dict[str, Any]:
        """(Re)connect one server and wait until its tools are listed or it fails."""
        self.disconnect(cfg.name)
        server = _Server(cfg)
        with self._lock:
            self._servers[cfg.name] = server
        server.task = asyncio.run_coroutine_threadsafe(self._run(server), self._ensure_loop())
        if not server.ready.wait(cfg.timeout_s * 2 + 5):
            server.error = "timed out while connecting"
        return server.status()

    def disconnect(self, name: str) -> None:
        with self._lock:
            server = self._servers.pop(name, None)
        if server is None or self._loop is None:
            return
        if server.stop is not None:
            self._loop.call_soon_threadsafe(server.stop.set)
        if server.task is not None:
            try:
                server.task.result(timeout=8)
            except (concurrent.futures.TimeoutError, Exception):
                server.task.cancel()

    def close_all(self) -> None:
        for name in list(self._servers):
            self.disconnect(name)

    # ---------------------------------------------------------------- calls
    def server(self, name: str) -> Optional[_Server]:
        with self._lock:
            return self._servers.get(name)

    def call(self, name: str, tool: str, arguments: dict[str, Any], timeout_s: Optional[float] = None) -> dict[str, Any]:
        server = self.server(name)
        if server is None or server.session is None:
            raise ToolRuntimeError(f"MCP server '{name}' is not connected")
        limit = timeout_s or server.config.timeout_s
        future = asyncio.run_coroutine_threadsafe(
            asyncio.wait_for(server.session.call_tool(tool, arguments=arguments), limit), self._ensure_loop()
        )
        try:
            return flatten_result(future.result(timeout=limit + 2))
        except (concurrent.futures.TimeoutError, asyncio.TimeoutError) as exc:
            future.cancel()
            raise ToolRuntimeError(f"MCP tool '{tool}' on '{name}' timed out after {limit:g}s") from exc
        except ToolRuntimeError:
            raise
        except Exception as exc:  # transport or protocol failure on the remote side
            raise ToolRuntimeError(f"MCP tool '{tool}' on '{name}' failed ({type(exc).__name__})") from exc

    def status(self) -> list[dict[str, Any]]:
        with self._lock:
            return [s.status() for s in self._servers.values()]

    def servers(self) -> list[_Server]:
        with self._lock:
            return list(self._servers.values())


_BRIDGE = McpBridge()


def bridge() -> McpBridge:
    return _BRIDGE


# ---------------------------------------------------------------- config and registration


def load_configs(path: Optional[Path] = None) -> tuple[list[ServerConfig], list[str]]:
    """Parse the config file. Returns `(configs, errors)`; a bad entry never blocks the others."""
    target = path or config_path()
    if not target.exists():
        return [], []
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [], [f"cannot read {target.name}: {exc}"]
    configs: list[ServerConfig] = []
    errors: list[str] = []
    for item in raw.get("servers", []):
        try:
            configs.append(ServerConfig.from_dict(item))
        except (ValueError, TypeError) as exc:
            errors.append(f"{item.get('name', '?')}: {exc}")
    return configs, errors


def connect_all(path: Optional[Path] = None, tools: Any = None) -> dict[str, Any]:
    """Connect every configured server (when `mcp.enabled`) and register their tools. Safe to call again."""
    if not effective("mcp.enabled"):
        bridge().close_all()
        sync_registry(tools)
        return {"enabled": False, "servers": [], "errors": []}
    configs, errors = load_configs(path)
    wanted = {c.name for c in configs}
    for stale in [s.config.name for s in bridge().servers() if s.config.name not in wanted]:
        bridge().disconnect(stale)
    for cfg in configs:
        bridge().connect(cfg)
    sync_registry(tools)
    return {"enabled": True, "servers": bridge().status(), "errors": errors}


def _manifest_for(server: _Server, tool: Any) -> ToolManifest:
    annotations = _attr(tool, "annotations")
    read_only = _attr(annotations, "read_only_hint", "readOnlyHint", default=None) if annotations is not None else None
    if read_only is True:
        effect = SideEffect.READ
    elif read_only is False:
        effect = SideEffect.WRITE
    else:
        effect = SideEffect.READ if server.config.default_side_effect == "read" else SideEffect.WRITE
    schema = _attr(tool, "input_schema", "inputSchema", default={"type": "object", "properties": {}})
    return ToolManifest(
        id=verb_name(server.config.name, tool.name),
        verb=verb_name(server.config.name, tool.name),
        resource_type="mcp",
        description=f"[MCP {server.config.name}] {(tool.description or tool.name)[:400]}",
        schema=schema,
        side_effect=effect,
        timeout_ms=int(server.config.timeout_s * 1000),
    )


def _handler(server_name: str, tool_name: str):
    def handler(args: dict[str, Any], ctx: RequestContext) -> dict[str, Any]:
        if not is_product(ctx) or not effective("mcp.enabled"):
            raise PolicyDeniedError("MCP tools are disabled")
        result = bridge().call(server_name, tool_name, args or {})
        text = result["text"]
        if result["is_error"]:
            raise ToolRuntimeError(f"MCP tool error: {text[:300]}")
        return {"text": text, "preview": text[:1500], "structured": result["structured"], "server": server_name}

    return handler


def sync_registry(tools: Any = None) -> list[str]:
    """Make the tool registry match the connected servers. Returns the registered verbs."""
    if tools is None:
        from harness.context import get_runtime

        tools = get_runtime().tools
    for verb in [v for v in tools.verbs() if v.startswith(VERB_PREFIX)]:
        tools.unregister(verb)
    registered: list[str] = []
    for server in bridge().servers():
        if server.session is None:
            continue
        for tool in server.exposed_tools():
            manifest = _manifest_for(server, tool)
            tools.register(manifest, _handler(server.config.name, tool.name))
            registered.append(manifest.verb)
    return registered


def expand_tool_names(names: list[str], agent_id: str = "") -> list[str]:
    """Resolve `mcp:<server>` (or `mcp:*`) wildcards on a card, plus servers that grant an agent by id."""
    out = [n for n in names if not n.startswith("mcp:")]
    wanted_servers: set[str] = set()
    for entry in names:
        if entry.startswith("mcp:"):
            wanted_servers.add(entry[4:] or "*")
    for server in bridge().servers():
        if server.config.agents and ("*" in server.config.agents or agent_id in server.config.agents):
            wanted_servers.add(server.config.name)
    for server in bridge().servers():
        if server.session is None or not ("*" in wanted_servers or server.config.name in wanted_servers):
            continue
        for tool in server.exposed_tools():
            verb = verb_name(server.config.name, tool.name)
            if verb not in out:
                out.append(verb)
    return out


# ---------------------------------------------------------------- web search backend


def _parse_search(text: str, structured: Any, limit: int) -> list[dict[str, str]]:
    rows: Any = None
    if isinstance(structured, dict):
        rows = structured.get("results")
    if rows is None and text.strip().startswith(("[", "{")):
        try:
            loaded = json.loads(text)
            rows = loaded.get("results") if isinstance(loaded, dict) else loaded
        except json.JSONDecodeError:
            rows = None
    out: list[dict[str, str]] = []
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            url = str(row.get("url") or row.get("link") or row.get("href") or "")
            if url:
                snippet = str(row.get("snippet") or row.get("description") or row.get("content") or "")
                out.append({"title": str(row.get("title") or url), "url": url, "snippet": snippet[:300]})
    if not out:
        for url in dict.fromkeys(re.findall(r"https?://[^\s)\]>\"']+", text)):
            out.append({"title": url, "url": url, "snippet": ""})
    return out[:limit]


def search(server_name: str, query: str, limit: int) -> list[dict[str, str]]:
    """Run a search through an MCP server's search tool. Used by `web_search` with backend `mcp:<server>`."""
    server = bridge().server(server_name)
    if server is None or server.session is None:
        raise ToolRuntimeError(f"MCP server '{server_name}' is not connected")
    tools = server.exposed_tools()
    chosen = next((t for t in tools if t.name == server.config.search_tool), None) or next(
        (t for t in tools if "search" in t.name.lower()), None
    )
    if chosen is None:
        raise ToolRuntimeError(f"MCP server '{server_name}' has no search tool")
    result = bridge().call(server_name, chosen.name, {"query": query})
    if result["is_error"]:
        raise ToolRuntimeError(f"MCP search failed: {result['text'][:200]}")
    return _parse_search(result["text"], result["structured"], limit)


def bridge_status() -> dict[str, Any]:
    """Safe summary for `/health` and the admin API."""
    return {
        "available": True,
        "enabled": bool(effective("mcp.enabled")),
        "servers": bridge().status(),
    }


def start_in_background(tools: Any = None) -> threading.Thread:
    """Connect configured servers without delaying API startup (stdio servers can take seconds)."""

    def run() -> None:
        try:
            connect_all(tools=tools)
        except Exception:  # startup must never fail because an optional server is down
            log.exception("MCP startup failed")

    thread = threading.Thread(target=run, name="orion-mcp-start", daemon=True)
    thread.start()
    return thread
