"""Bring-your-own-data connectors, available in live chat only (never in eval mode).

`config/connectors.json` (or `ORION_CONNECTORS`) declares sources. Connector tables are addressed
as `<connector_id>.<table>` in the normal `sql_spec` and `schema_lookup` tools, so agents need no
new verbs. Each connector contributes an entity tag and its legal joins to the pack registry, so
an illegal cross-source join abstains exactly like the built-in lake does.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Optional

from harness import tenancy
from harness.config import PROJECT_ROOT
from harness.connectors.base import ColumnInfo, ConnectorConfig, ConnectorError, DataConnector
from harness.connectors.files import FilesConnector
from harness.connectors.sql import SqlAlchemyConnector
from harness.context import RequestContext, is_product
from harness.errors import SchemaInvalidError

log = logging.getLogger("orion.connectors")

__all__ = [
    "ColumnInfo", "ConnectorConfig", "ConnectorError", "ConnectorRegistry", "DataConnector",
    "install_tool_wrappers", "registry", "reload",
]

_TYPES = {"sqlalchemy": SqlAlchemyConnector, "files": FilesConnector}


def config_path() -> Path:
    raw = os.getenv("ORION_CONNECTORS", "").strip()
    return Path(raw) if raw else PROJECT_ROOT / "config" / "connectors.json"


class ConnectorRegistry:
    def __init__(self, configs: Optional[list[ConnectorConfig]] = None) -> None:
        self._connectors: dict[str, DataConnector] = {}
        self._lock = threading.RLock()
        for cfg in configs or []:
            self.add(cfg)

    # ---------------------------------------------------------------- lifecycle
    def add(self, config: ConnectorConfig) -> DataConnector:
        factory = _TYPES.get(config.type)
        if factory is None:
            raise ConnectorError(f"unknown connector type '{config.type}'. known: {sorted(_TYPES)}")
        connector = factory(config)
        with self._lock:
            old = self._connectors.get(config.id)
            if old is not None and hasattr(old, "dispose"):
                old.dispose()
            self._connectors[config.id] = connector
        self._register_entity(connector)
        return connector

    def load_file(self, path: Optional[Path] = None) -> list[str]:
        """(Re)load connectors from JSON. Bad entries are skipped and logged, never fatal."""
        target = path or config_path()
        self.clear()
        if not target.exists():
            return []
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.error("connector config %s is unreadable: %s", target, exc)
            return []
        loaded: list[str] = []
        for item in raw.get("connectors", []):
            try:
                self.add(ConnectorConfig.from_dict(item))
                loaded.append(str(item.get("id")))
            except (ValueError, ConnectorError, KeyError) as exc:
                log.error("skipping connector %s: %s", item.get("id"), exc)
        return loaded

    def clear(self) -> None:
        from harness.packs import registry as packs

        with self._lock:
            for cid, connector in list(self._connectors.items()):
                packs().remove_extension(f"connector:{cid}")
                if hasattr(connector, "dispose"):
                    connector.dispose()
            self._connectors.clear()

    @staticmethod
    def _register_entity(connector: DataConnector) -> None:
        from harness.packs import registry as packs

        cfg = connector.config
        joins: dict[str, frozenset[str]] = {cfg.entity: frozenset({cfg.entity, *cfg.legal_joins})}
        for other in cfg.legal_joins:  # declaring a join is consent from this side; make it symmetric
            joins[other] = frozenset({other, cfg.entity})
        cues = {cfg.entity: tuple(re.compile(rf"\b{re.escape(c)}\b", re.I) for c in cfg.cues)} if cfg.cues else None
        packs().extend_entities(f"connector:{cfg.id}", joins, cues)

    # ---------------------------------------------------------------- lookup
    def get(self, connector_id: str) -> Optional[DataConnector]:
        with self._lock:
            return self._connectors.get(connector_id)

    def all(self) -> list[DataConnector]:
        with self._lock:
            return list(self._connectors.values())

    def for_tenant(self, tenant: Optional[str] = None) -> list[DataConnector]:
        """Connectors a tenant may use (the bound tenant when none is given). Admin listings use `all()`."""
        wanted = tenant or tenancy.current()
        return [c for c in self.all() if c.config.visible_to(wanted)]

    def resolve(self, ref: str, tenant: Optional[str] = None) -> tuple[DataConnector, str]:
        """Split `connector.table`. Unknown connectors are SchemaInvalidError so F4 recovery can list them.

        A connector the tenant may not use is reported exactly like one that does not exist.
        """
        cid, _, table = str(ref or "").partition(".")
        visible = {c.id: c for c in self.for_tenant(tenant)}
        connector = visible.get(cid)
        if connector is None or not table:
            raise SchemaInvalidError(f"Unknown connector table '{ref}'. Connectors: {list(visible)}")
        return connector, table

    def table_refs(self, tenant: Optional[str] = None) -> list[str]:
        out: list[str] = []
        for connector in self.for_tenant(tenant):
            try:
                out.extend(f"{connector.id}.{t}" for t in connector.tables())
            except Exception as exc:  # one unavailable source must not hide the others
                log.warning("connector %s unavailable: %s", connector.id, exc)
        return out

    def prompt_text(self, role: Optional[str], limit_chars: int = 1800, tenant: Optional[str] = None) -> str:
        """Short schema lines for the worker prompt. Masked columns never appear; other tenants' sources are absent."""
        lines: list[str] = []
        for connector in self.for_tenant(tenant):
            try:
                for table in connector.tables():
                    cols = ", ".join(f"{c.name}:{c.type}" for c in connector.schema(table, role))
                    lines.append(f"TABLE {connector.id}.{table} entity={connector.entity} columns=[{cols}]")
            except Exception as exc:
                lines.append(f"CONNECTOR {connector.id} UNAVAILABLE ({type(exc).__name__})")
        text = "\n".join(lines)
        if len(text) > limit_chars:
            text = text[:limit_chars] + "\n...connector schema truncated; call schema_lookup for the rest."
        return text

    # ---------------------------------------------------------------- tool handlers
    def sql_spec(self, args: dict[str, Any], ctx: RequestContext) -> dict[str, Any]:
        from harness.entities import assert_entity_allowed

        connector, table = self.resolve(str(args.get("table") or ""), tenancy.of(ctx))
        assert_entity_allowed(ctx, connector.entity)
        spec = dict(args)
        spec["table"] = table
        return connector.query(spec, role=ctx.role)

    def schema_lookup(self, args: dict[str, Any], ctx: RequestContext) -> dict[str, Any]:
        connector, table = self.resolve(str(args.get("table") or ""), tenancy.of(ctx))
        columns = connector.schema(table, ctx.role)
        preview = f"TABLE {connector.id}.{table} entity={connector.entity}\n" + "\n".join(
            f"- {c.name}: {c.type}" for c in columns
        )
        return {
            "table": f"{connector.id}.{table}",
            "entity": connector.entity,
            "columns": [c.to_dict() for c in columns],
            "preview": preview,
        }


_REGISTRY: Optional[ConnectorRegistry] = None
_LOCK = threading.Lock()


def registry() -> ConnectorRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        with _LOCK:
            if _REGISTRY is None:
                reg = ConnectorRegistry()
                reg.load_file()
                _REGISTRY = reg
    return _REGISTRY


def reload() -> list[str]:
    return registry().load_file()


def set_registry(reg: Optional[ConnectorRegistry]) -> None:
    global _REGISTRY
    with _LOCK:
        _REGISTRY = reg


def install_tool_wrappers(tools: Any) -> None:
    """Make `sql_spec` and `schema_lookup` connector-aware in live chat, leaving eval behaviour untouched."""

    def wrap_sql(inner):
        def handler(args: dict[str, Any], ctx: RequestContext) -> Any:
            if is_product(ctx) and "." in str(args.get("table") or ""):
                return registry().sql_spec(args, ctx)
            return inner(args, ctx)

        return handler

    def wrap_schema(inner):
        def handler(args: dict[str, Any], ctx: RequestContext) -> Any:
            if not is_product(ctx):
                return inner(args, ctx)
            if "." in str(args.get("table") or ""):
                return registry().schema_lookup(args, ctx)
            result = inner(args, ctx)
            refs = registry().table_refs(tenancy.of(ctx))
            if refs and isinstance(result, dict):
                result = dict(result)
                result["connector_tables"] = refs
                result["preview"] = str(result.get("preview", "")) + "\nConnector tables: " + ", ".join(refs)
            return result

        return handler

    tools.wrap("sql_spec", wrap_sql)
    tools.wrap("schema_lookup", wrap_schema)
