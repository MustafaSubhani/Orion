"""Connector contract: a read-only, row-capped, role-masked window onto an enterprise data source.

Contract for every implementation:
- Read only. No statement is ever built from a raw string; queries come from the allow-listed spec.
- Results never exceed `row_cap` rows, and sensitive columns for the caller's role are invisible
  (an unknown-column error lists only visible columns, so masked names do not leak).
- Secrets are never read from config files. `url_env` names an environment variable.
- Errors are typed: unknown table or column is SchemaInvalidError (F4), infrastructure trouble is
  ToolRuntimeError (F1). Messages never contain connection strings.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

DEFAULT_ROW_CAP = 200
_URL_CREDENTIALS = re.compile(r"(\w[\w+.-]*://)[^/\s@]*@")


def scrub(text: str) -> str:
    """Remove `user:password@` from any connection string inside `text`."""
    return _URL_CREDENTIALS.sub(r"\1***@", text or "")


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    type: str
    nullable: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "type": self.type, "nullable": self.nullable}


@dataclass
class ConnectorConfig:
    id: str
    type: str
    entity: str
    url_env: str = ""
    path: str = ""  # files connector: a folder
    legal_joins: list[str] = field(default_factory=list)
    cues: list[str] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)  # allow-list; empty means every table found
    sensitive_columns: dict[str, list[str]] = field(default_factory=dict)  # role -> hidden columns
    row_cap: int = DEFAULT_ROW_CAP
    statement_timeout_s: float = 15.0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConnectorConfig":
        missing = [k for k in ("id", "type", "entity") if not data.get(k)]
        if missing:
            raise ValueError(f"connector config is missing {missing}")
        cid = str(data["id"])
        if not cid.replace("_", "").replace("-", "").isalnum():
            raise ValueError(f"connector id '{cid}' may only contain letters, digits, '-' and '_'")
        return cls(
            id=cid,
            type=str(data["type"]),
            entity=str(data["entity"]).upper(),
            url_env=str(data.get("url_env") or ""),
            path=str(data.get("path") or ""),
            legal_joins=[str(x).upper() for x in data.get("legal_joins") or []],
            cues=[str(x) for x in data.get("cues") or []],
            tables=[str(x) for x in data.get("tables") or []],
            sensitive_columns={str(r): [str(c) for c in cols] for r, cols in (data.get("sensitive_columns") or {}).items()},
            row_cap=max(1, min(int(data.get("row_cap") or DEFAULT_ROW_CAP), 5000)),
            statement_timeout_s=float(data.get("statement_timeout_s") or 15.0),
        )


class ConnectorError(Exception):
    """Configuration or availability problem. Safe to show an administrator."""


class DataConnector(ABC):
    type: str = ""

    def __init__(self, config: ConnectorConfig) -> None:
        self.config = config

    @property
    def id(self) -> str:
        return self.config.id

    @property
    def entity(self) -> str:
        return self.config.entity

    def hidden_columns(self, role: Optional[str]) -> frozenset[str]:
        return frozenset(self.config.sensitive_columns.get(role or "", []))

    @abstractmethod
    def tables(self) -> list[str]:
        """Table names the agent may use (the allow-list intersected with what exists)."""

    @abstractmethod
    def schema(self, table: str, role: Optional[str] = None) -> list[ColumnInfo]:
        """Live column list. Called on every lookup so a renamed column is seen immediately (F4)."""

    @abstractmethod
    def query(self, spec: dict[str, Any], *, role: Optional[str] = None) -> dict[str, Any]:
        """Run an allow-listed spec and return the standard tool payload."""

    def test(self) -> dict[str, Any]:
        """Connectivity check for the admin API. Never raises and never returns credentials."""
        try:
            names = self.tables()
            return {"id": self.id, "ok": True, "tables": names[:50], "table_count": len(names)}
        except Exception as exc:  # report any failure to the admin without leaking secrets
            return {"id": self.id, "ok": False, "error": scrub(str(exc))[:200]}

    def describe(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "entity": self.entity,
            "legal_joins": self.config.legal_joins,
            "tables": self.config.tables or "all",
            "row_cap": self.config.row_cap,
        }
