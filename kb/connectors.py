"""Live KB connector catalog. Product store only — never the paper lake."""

from __future__ import annotations

import json
from typing import Any

from harness.config import PROJECT_ROOT

CONNECTOR_DIR = PROJECT_ROOT / "kb" / "connectors"


def list_connectors() -> list[dict[str, Any]]:
    path = CONNECTOR_DIR / "catalog.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, list):
        return data
    return list(data.get("connectors") or [])
