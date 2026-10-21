"""AgentCard registry. Plug-in is a registry event, not an if/else in the planner."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Callable, Optional

from harness.config import LAB_GENERATED_DIR, OUTPUTS_DIR, ensure_dirs
from harness.errors import UnknownAgentError
from harness.types import AgentCard, AgentStatus
from harness.versions import VersionStore

CUSTOM_PATH = OUTPUTS_DIR / "custom_agents.json"

def builtin_cards() -> list[AgentCard]:
    """Cards from every loaded domain pack."""
    from harness.packs import registry

    return registry().agents()


class AgentRegistry:
    def __init__(self) -> None:
        ensure_dirs()
        self._cards: dict[str, AgentCard] = {c.id: c for c in builtin_cards()}
        self.versions = VersionStore()
        self._lock = threading.RLock()
        self._listeners: list[Callable[[str, str], None]] = []
        self._load_custom()
        self._load_generated()

    def add_listener(self, fn: Callable[[str, str], None]) -> None:
        """Subscribe to `(agent_id, event)` where event is register, status, delete or rollback."""
        with self._lock:
            self._listeners.append(fn)

    def _notify(self, agent_id: str, event: str) -> None:
        for fn in list(self._listeners):
            try:
                fn(agent_id, event)
            except Exception:  # a broken listener must not block a registry change
                continue

    def _load_custom(self) -> None:
        if not CUSTOM_PATH.exists():
            return
        try:
            raw = json.loads(CUSTOM_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for agent_id, data in raw.items():
            data.setdefault("id", agent_id)
            data.setdefault("is_custom", True)
            data.setdefault("is_builtin", False)
            card = AgentCard.from_dict(data)
            self._cards[card.id] = card

    def _load_generated(self) -> None:
        if not LAB_GENERATED_DIR.exists():
            return
        for card_path in LAB_GENERATED_DIR.glob("*/card.json"):
            try:
                data = json.loads(card_path.read_text(encoding="utf-8"))
                data.setdefault("is_custom", True)
                data.setdefault("is_builtin", False)
                data.setdefault("module_path", str(card_path.parent / "agent.py"))
                card = AgentCard.from_dict(data)
                self._cards[card.id] = card
            except (OSError, json.JSONDecodeError, KeyError):
                continue

    def _save_custom(self) -> None:
        from harness.fsutil import atomic_write, file_lock

        with file_lock(CUSTOM_PATH):
            custom = {cid: c.to_dict() for cid, c in self._cards.items() if c.is_custom}
            atomic_write(CUSTOM_PATH, json.dumps(custom, indent=2))

    def get(self, agent_id: str) -> Optional[AgentCard]:
        return self._cards.get(agent_id)

    def require(self, agent_id: str) -> AgentCard:
        card = self.get(agent_id)
        if card is None:
            raise UnknownAgentError(f"Unknown agent id '{agent_id}'")
        return card

    def all(self, include_custom: bool = True) -> list[AgentCard]:
        cards = list(self._cards.values())
        if not include_custom:
            cards = [c for c in cards if c.is_builtin]
        return cards

    def active(self) -> list[AgentCard]:
        return [
            c
            for c in self._cards.values()
            if c.status in {AgentStatus.ACTIVE, AgentStatus.DEPLOYED}
        ]

    def custom(self) -> list[AgentCard]:
        return [c for c in self._cards.values() if c.is_custom]

    def register(self, card: AgentCard, *, actor: str = "system") -> AgentCard:
        with self._lock:
            if card.id in self._cards and self._cards[card.id].is_builtin:
                raise ValueError(f"Cannot overwrite builtin agent '{card.id}'")
            self._cards[card.id] = card
            self.versions.save(card.id, card.to_dict(), actor, change_note="register")
            if card.is_custom:
                self._save_custom()
        self._notify(card.id, "register")
        return card

    def set_status(self, agent_id: str, status: AgentStatus, *, actor: str = "system") -> bool:
        with self._lock:
            card = self.get(agent_id)
            if card is None or card.is_builtin:
                return False
            card.status = status
            self._save_custom()
            self.versions.save(agent_id, card.to_dict(), actor, change_note=f"status={status.value}")
        self._notify(agent_id, "status")
        return True

    def delete(self, agent_id: str) -> bool:
        with self._lock:
            card = self.get(agent_id)
            if card is None or card.is_builtin:
                return False
            del self._cards[agent_id]
            self._save_custom()
        self._notify(agent_id, "delete")
        return True

    def set_metadata(self, agent_id: str, key: str, value: Any) -> bool:
        """Attach a small record (such as the latest evaluation) to a custom card without bumping its version."""
        with self._lock:
            card = self.get(agent_id)
            if card is None or card.is_builtin:
                return False
            card.metadata = {**(card.metadata or {}), key: value}
            self._save_custom()
        self._notify(agent_id, "metadata")
        return True

    def update(self, card: AgentCard, *, actor: str = "system", note: str = "update") -> AgentCard:
        """Replace a custom card with an edited copy and record a new version (used by guarded repair)."""
        with self._lock:
            current = self.get(card.id)
            if current is None or current.is_builtin:
                raise ValueError(f"cannot update '{card.id}'")
            self._cards[card.id] = card
            self._save_custom()
            self.versions.save(card.id, card.to_dict(), actor, change_note=note)
        self._notify(card.id, "update")
        return card

    def rollback(self, agent_id: str, version: int, *, actor: str = "system") -> bool:
        """Restore a custom agent to a stored snapshot. Writes a new version rather than rewriting history."""
        with self._lock:
            current = self.get(agent_id)
            snap = self.versions.get(agent_id, version)
            if current is None or current.is_builtin or snap is None:
                return False
            data = dict(snap.snapshot)
            data.setdefault("is_custom", True)
            data.setdefault("is_builtin", False)
            if current.module_path and not data.get("module_path"):
                data["module_path"] = current.module_path
            card = AgentCard.from_dict(data)
            self._cards[agent_id] = card
            self._save_custom()
            self.versions.save(agent_id, card.to_dict(), actor, change_note=f"rollback to v{version}")
        self._notify(agent_id, "rollback")
        return True


# ---- frontend-compatible module-level API ----

_REGISTRY: Optional[AgentRegistry] = None


def get_registry() -> AgentRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = AgentRegistry()
    return _REGISTRY


def get_agent(agent_id: str) -> Optional[AgentCard]:
    return get_registry().get(agent_id)


def get_all_agents(include_custom: bool = True) -> list[AgentCard]:
    return get_registry().all(include_custom=include_custom)


def get_custom_agents() -> list[AgentCard]:
    return get_registry().custom()


def update_agent_status(agent_id: str, status: AgentStatus, actor: str = "system") -> bool:
    return get_registry().set_status(agent_id, status, actor=actor)


def predeploy_check(agent_id: str) -> dict:
    checks = []
    card = get_agent(agent_id)
    if not card or not card.is_custom:
        return {
            "passed": False,
            "checks": [{"name": "agent_exists", "passed": False, "detail": "not a custom agent"}],
        }
    if card.module_path:
        from sandbox.gate import static_gate

        gate = static_gate(Path(card.module_path))
        checks.append({"name": "static_gate", "passed": gate.ok, "detail": gate.reason})
    else:
        checks.append({"name": "static_gate", "passed": True, "detail": "card-only agent (generic worker)"})
    checks.append(
        {
            "name": "has_prompt",
            "passed": bool(card.system_prompt.strip()),
            "detail": "system_prompt present" if card.system_prompt else "missing prompt",
        }
    )
    checks.append(
        {
            "name": "has_tools",
            "passed": bool(card.tools),
            "detail": ",".join(card.tools) if card.tools else "no tools",
        }
    )
    from lab.evaluate import promotion_status

    ok, detail = promotion_status(card)
    checks.append({"name": "evaluation", "passed": ok, "detail": detail})
    return {"passed": all(c["passed"] for c in checks), "checks": checks}


def deploy_agent(agent_id: str) -> bool:
    result = predeploy_check(agent_id)
    if not result["passed"]:
        return False
    return update_agent_status(agent_id, AgentStatus.DEPLOYED)
