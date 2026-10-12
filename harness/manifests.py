"""Load extra ToolManifest rows from JSON without editing workers/generic.py."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from harness.config import PROJECT_ROOT
from harness.context import RequestContext
from harness.types import SideEffect, ToolManifest

PLUGIN_DIR = PROJECT_ROOT / "harness" / "plugins"

Handler = Callable[[dict[str, Any], RequestContext], Any]


def echo_handler(args: dict[str, Any], ctx: RequestContext) -> dict[str, Any]:
    text = str(args.get("text") or "")
    return {"echo": text, "preview": text or "(empty)"}


_HANDLERS: dict[str, Handler] = {
    "echo": echo_handler,
}


def manifest_from_dict(data: dict[str, Any]) -> tuple[ToolManifest, Handler]:
    verb = str(data["verb"])
    handler_name = str(data.get("handler") or data.get("builtin_handler") or "echo")
    handler = _HANDLERS.get(handler_name)
    if handler is None:
        raise ValueError(f"unknown plugin handler '{handler_name}'")
    manifest = ToolManifest(
        id=str(data.get("id") or verb),
        verb=verb,
        resource_type=str(data.get("resource_type") or "plugin"),
        description=str(data.get("description") or verb),
        schema=data.get("schema") or {"type": "object", "properties": {"text": {"type": "string"}}},
        side_effect=SideEffect.READ,
        circuit_breaker=bool(data.get("circuit_breaker", False)),
    )
    return manifest, handler


def load_plugin_file(registry: Any, path: Path) -> str:
    data = json.loads(path.read_text(encoding="utf-8"))
    manifest, handler = manifest_from_dict(data)
    registry.register(manifest, handler)
    return manifest.verb


def load_plugin_dir(registry: Any, directory: Path | None = None) -> list[str]:
    root = directory or PLUGIN_DIR
    if not root.exists():
        return []
    loaded = []
    for path in sorted(root.glob("*.json")):
        try:
            loaded.append(load_plugin_file(registry, path))
        except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
            print(f"[plugins] skip {path.name}: {exc}")
    return loaded
