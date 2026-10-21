"""Deterministic dump of the agent and tool registry. Used as a parity oracle for the domain pack extraction.

Regenerate only on purpose:  python -m tests.registry_snapshot --write
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

SNAPSHOT_PATH = Path(__file__).parent / "fixtures" / "registry_snapshot.json"


def _plain(value: Any) -> Any:
    return json.loads(json.dumps(value, default=lambda o: getattr(o, "value", str(o)), sort_keys=True))


def build_snapshot() -> dict[str, Any]:
    from harness import config, permissions
    from harness.agent_registry import builtin_cards
    from harness.tool_registry import ToolRegistry

    registry = ToolRegistry()
    cards = {c.id: _plain(asdict(c)) for c in builtin_cards()}
    manifests = {}
    for verb in registry.verbs():
        manifest = registry.manifest(verb)
        assert manifest is not None
        manifests[verb] = _plain(asdict(manifest))
    card_tools = {
        card_id: _plain(registry.openai_tools(card["tools"])) for card_id, card in cards.items()
    }
    return {
        "cards": cards,
        "manifests": manifests,
        "card_tools": card_tools,
        "legal_joins": _plain({k: sorted(v) for k, v in config.LEGAL_JOINS.items()}),
        "allowed_entities": list(config.ALLOWED_ENTITIES),
        "egress_allowlist": list(config.EGRESS_ALLOWLIST),
        "known_collections": list(config.KNOWN_COLLECTIONS),
        "role_tools": _plain({k: sorted(v) for k, v in permissions.ROLE_TOOL_ALLOWLIST.items()}),
        "salary_columns": sorted(permissions.SALARY_COLUMNS),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    snap = build_snapshot()
    text = json.dumps(snap, indent=2, sort_keys=True)
    if args.write:
        SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT_PATH.write_text(text, encoding="utf-8")
        print(f"wrote {SNAPSHOT_PATH} ({len(text)} bytes)")
    else:
        print(text)


if __name__ == "__main__":
    main()
