"""Version snapshots for cards and generated workers. Repair Agent bumps versions; never mutates in place."""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from harness.config import OUTPUTS_DIR, ensure_dirs

VERSIONS_PATH = OUTPUTS_DIR / "agent_versions.json"


@dataclass
class AgentVersion:
    version: int
    agent_id: str
    snapshot: dict
    created_at: str
    created_by: str
    change_note: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


class VersionStore:
    def __init__(self, path: Path | None = None) -> None:
        ensure_dirs()
        self.path = path or VERSIONS_PATH
        self._data: dict[str, list[AgentVersion]] = {}
        self._lock = threading.RLock()
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for agent_id, versions in raw.items():
            self._data[agent_id] = [AgentVersion(**v) for v in versions]

    def _save(self) -> None:
        from harness.fsutil import atomic_write, file_lock

        with file_lock(self.path):
            payload = {aid: [v.to_dict() for v in vs] for aid, vs in self._data.items()}
            atomic_write(self.path, json.dumps(payload, indent=2))

    def save(
        self,
        agent_id: str,
        snapshot: dict,
        created_by: str,
        change_note: str | None = None,
    ) -> AgentVersion:
        with self._lock:
            bucket = self._data.setdefault(agent_id, [])
            version = AgentVersion(
                version=len(bucket) + 1,
                agent_id=agent_id,
                snapshot=snapshot,
                created_at=datetime.now(timezone.utc).isoformat(),
                created_by=created_by,
                change_note=change_note,
            )
            bucket.append(version)
            self._save()
        return version

    def list(self, agent_id: str) -> list[AgentVersion]:
        return list(self._data.get(agent_id, []))

    def get(self, agent_id: str, version_num: int) -> Optional[AgentVersion]:
        for item in self._data.get(agent_id, []):
            if item.version == version_num:
                return item
        return None

    def latest(self, agent_id: str) -> Optional[AgentVersion]:
        bucket = self._data.get(agent_id, [])
        return bucket[-1] if bucket else None
