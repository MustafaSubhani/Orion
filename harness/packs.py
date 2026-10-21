"""Domain pack registry. Core never names a dataset; a pack supplies agents, tools and policy.

A pack is any object with the attributes below (all optional except `name`). Packs load from
`ORION_PACKS` (comma separated, default `enterprise_lake`), either as a built-in name under
`packs/`, a `module:attribute` path, or a Python entry point in the `orion.packs` group.

Contract: aggregation is a union (a second pack can add entities, never remove another pack's).
`entities_join_legal` is the single join gate used by the planner, tools and connectors.
"""

from __future__ import annotations

import importlib
import os
import threading
from typing import Any, Iterable, Optional

KB_ENTITY = "KB"  # live uploads are a platform concept, so the tag belongs to core


class PackRegistry:
    def __init__(self, packs: Iterable[Any]) -> None:
        self.packs: list[Any] = list(packs)
        self._extensions: dict[str, dict[str, frozenset[str]]] = {}
        self._extension_cues: dict[str, dict[str, tuple]] = {}
        self._lock = threading.RLock()

    # ---------------------------------------------------------------- helpers
    def _each(self, method: str, *args: Any) -> list[Any]:
        out = []
        for pack in self.packs:
            fn = getattr(pack, method, None)
            if callable(fn):
                out.append(fn(*args))
        return out

    # ---------------------------------------------------------------- agents and tools
    def agents(self) -> list:
        cards: list = []
        seen: set[str] = set()
        for group in self._each("agents"):
            for card in group:
                if card.id not in seen:
                    seen.add(card.id)
                    cards.append(card)
        return cards

    def register_tools(self, tools: Any) -> None:
        for pack in self.packs:
            fn = getattr(pack, "register_tools", None)
            if callable(fn):
                fn(tools)

    def fault_values(self) -> dict[str, str]:
        """Eval-only names for fault injection (bad_column, bad_table, bad_collection)."""
        merged: dict[str, str] = {}
        for mapping in self._each("fault_values"):
            merged.update(mapping)
        return merged

    def tool_descriptions(self) -> dict[str, str]:
        """Per-verb description overrides, so core tool text can stay dataset-neutral."""
        merged: dict[str, str] = {}
        for mapping in self._each("tool_descriptions"):
            merged.update(mapping)
        return merged

    def lab_tools(self) -> frozenset[str]:
        merged: set[str] = set()
        for tools in self._each("lab_tools"):
            merged |= set(tools)
        return frozenset(merged)

    # ---------------------------------------------------------------- entities and joins
    def allowed_entities(self) -> tuple[str, ...]:
        order: list[str] = []
        for group in self._each("entities"):
            order.extend(group)
        with self._lock:
            for joins in self._extensions.values():
                order.extend(joins)
        order.append(KB_ENTITY)
        return tuple(dict.fromkeys(order))

    def legal_joins(self) -> dict[str, frozenset[str]]:
        merged: dict[str, frozenset[str]] = {}
        sources = list(self._each("legal_joins"))
        with self._lock:
            sources.extend(self._extensions.values())
        for joins in sources:
            for entity, allowed in joins.items():
                merged[entity] = merged.get(entity, frozenset()) | frozenset(allowed) | {entity}
        merged[KB_ENTITY] = frozenset({KB_ENTITY})  # live uploads never join anything else
        return merged

    def extend_entities(
        self,
        source: str,
        joins: dict[str, frozenset[str]],
        cues: Optional[dict[str, tuple]] = None,
    ) -> None:
        """Add or replace entities (and optional mention cues) contributed at runtime. Keyed by `source`."""
        with self._lock:
            self._extensions[source] = {k.upper(): frozenset(v) for k, v in joins.items()}
            if cues:
                self._extension_cues[source] = {k.upper(): tuple(v) for k, v in cues.items()}
            else:
                self._extension_cues.pop(source, None)

    def remove_extension(self, source: str) -> None:
        with self._lock:
            self._extensions.pop(source, None)
            self._extension_cues.pop(source, None)

    def entities_join_legal(self, entities: list[str]) -> tuple[bool, str]:
        """True iff every named entity may appear with every other."""
        ents: list[str] = []
        for raw in entities:
            token = str(raw or "").strip().upper()
            if token:
                ents.append(token)
        ents = list(dict.fromkeys(ents))
        if len(ents) <= 1:
            return True, "single entity or empty"
        joins = self.legal_joins()
        unknown = [e for e in ents if e not in joins]
        if unknown:
            return False, f"unknown entity {unknown[0]}. known: {list(self.allowed_entities())}"
        wanted = set(ents)
        for entity in ents:
            if not wanted.issubset(joins[entity]):
                blocked = sorted(wanted - joins[entity])
                return False, f"{entity} cannot join {blocked} (illegal mix; abstain)"
        return True, "legal join"

    def detect_entities(self, text: str) -> list[str]:
        blob = text or ""
        found: list[str] = []
        with self._lock:
            runtime_cues = list(self._extension_cues.values())
        for cues in [*self._each("entity_cues"), *runtime_cues]:
            for entity, patterns in cues.items():
                if entity not in found and any(p.search(blob) for p in patterns):
                    found.append(entity)
        return found

    def default_entity(self) -> str:
        entities = [e for e in self.allowed_entities() if e != KB_ENTITY]
        return entities[0] if entities else KB_ENTITY

    def lab_entities(self) -> set[str]:
        return {e for e in self.allowed_entities() if e != KB_ENTITY}

    # ---------------------------------------------------------------- policy
    def egress_allowlist(self) -> tuple[str, ...]:
        hosts: list[str] = []
        for group in self._each("egress_allowlist"):
            hosts.extend(group)
        return tuple(dict.fromkeys(hosts))

    def role_tools(self) -> dict[str, frozenset[str]]:
        merged: dict[str, frozenset[str]] = {}
        for mapping in self._each("role_tools"):
            for role, tools in mapping.items():
                tools = frozenset(tools)
                if role not in merged:
                    merged[role] = tools
                elif not merged[role] or not tools:
                    merged[role] = frozenset()  # an empty set means "all tools" and wins
                else:
                    merged[role] = merged[role] | tools
        return merged

    def sensitive_columns(self) -> frozenset[str]:
        cols: set[str] = set()
        for group in self._each("sensitive_columns"):
            cols |= set(group)
        return frozenset(cols)

    def known_collections(self) -> tuple[str, ...]:
        names: list[str] = []
        for group in self._each("known_collections"):
            names.extend(group)
        return tuple(dict.fromkeys(names))

    def default_collection(self, agent_id: str) -> str:
        for name in self._each("default_collection", agent_id):
            if name:
                return name
        collections = self.known_collections()
        return collections[0] if collections else ""

    # ---------------------------------------------------------------- prompts and heuristics
    def synthesis_hint(self) -> str:
        return "\n".join(h for h in self._each("synthesis_hint") if h)

    def domain_mismatch(self, agent_id: str, task: str, text: str) -> bool:
        return any(self._each("domain_mismatch", agent_id, task, text))

    def schema_text(self, include_kb: bool = False) -> str:
        parts = [t for t in self._each("schema_text", include_kb) if t]
        return "\n".join(parts)

    def stale_skill_patterns(self) -> tuple[str, ...]:
        out: list[str] = []
        for group in self._each("stale_skill_patterns"):
            out.extend(group)
        return tuple(out)

    def abstain_suffix(self) -> str:
        return " ".join(s for s in self._each("abstain_suffix") if s)


_REGISTRY: Optional[PackRegistry] = None
_LOCK = threading.Lock()


def _load_one(name: str) -> Any:
    name = name.strip()
    if ":" in name:
        module_name, attr = name.split(":", 1)
        return getattr(importlib.import_module(module_name), attr)
    try:
        module = importlib.import_module(f"packs.{name}")
        return module.PACK
    except ModuleNotFoundError as exc:
        if exc.name != f"packs.{name}":
            raise
    from importlib.metadata import entry_points

    for ep in entry_points(group="orion.packs"):
        if ep.name == name:
            return ep.load()
    raise ValueError(f"unknown domain pack '{name}'")


def load_packs(names: Optional[Iterable[str]] = None) -> list[Any]:
    wanted = list(names) if names is not None else [
        n for n in os.getenv("ORION_PACKS", "enterprise_lake").split(",") if n.strip()
    ]
    return [_load_one(n) for n in wanted]


def registry() -> PackRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        with _LOCK:
            if _REGISTRY is None:
                _REGISTRY = PackRegistry(load_packs())
    return _REGISTRY


def set_registry(reg: Optional[PackRegistry]) -> None:
    """Replace the process registry (tests, embedding). `None` forces a reload from the environment."""
    global _REGISTRY
    with _LOCK:
        _REGISTRY = reg
