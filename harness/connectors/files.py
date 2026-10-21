"""Folder-of-files connector (CSV, TSV, Parquet, XLSX). Read-only, role-masked, row-capped.

Tables are the file stems. Files are re-read when their modification time changes, so a replaced
export is picked up without a restart. The spec engine is the same one the frozen tables use.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import pandas as pd

from harness.connectors.base import ColumnInfo, ConnectorConfig, ConnectorError, DataConnector
from harness.errors import SchemaInvalidError, ToolRuntimeError
from sandbox.exec_sandbox import run_spec_on_frame

SUFFIXES = {".csv", ".tsv", ".parquet", ".xlsx"}


class FilesConnector(DataConnector):
    type = "files"

    def __init__(self, config: ConnectorConfig) -> None:
        super().__init__(config)
        self._cache: dict[str, tuple[float, pd.DataFrame]] = {}

    def _root(self) -> Path:
        if not self.config.path:
            raise ConnectorError(f"connector '{self.id}' needs a `path`")
        root = Path(self.config.path).expanduser().resolve()
        if not root.is_dir():
            raise ConnectorError(f"folder for connector '{self.id}' does not exist")
        return root

    def _files(self) -> dict[str, Path]:
        found = {p.stem: p for p in sorted(self._root().iterdir()) if p.is_file() and p.suffix.lower() in SUFFIXES}
        if self.config.tables:
            return {k: v for k, v in found.items() if k in self.config.tables}
        return found

    def tables(self) -> list[str]:
        return list(self._files())

    def _frame(self, table: str) -> pd.DataFrame:
        files = self._files()
        if table not in files:
            raise SchemaInvalidError(f"Unknown table '{table}' in connector '{self.id}'. Known: {list(files)}")
        path = files[table]
        mtime = path.stat().st_mtime
        cached = self._cache.get(table)
        if cached and cached[0] == mtime:
            return cached[1]
        try:
            suffix = path.suffix.lower()
            if suffix == ".parquet":
                frame = pd.read_parquet(path)
            elif suffix == ".xlsx":
                frame = pd.read_excel(path)
            else:
                frame = pd.read_csv(path, sep="\t" if suffix == ".tsv" else ",", low_memory=False)
        except Exception as exc:  # unreadable or corrupt file: typed as a tool failure
            raise ToolRuntimeError(f"connector '{self.id}' could not read '{table}'") from exc
        self._cache[table] = (mtime, frame)
        return frame

    def _masked(self, table: str, role: Optional[str]) -> pd.DataFrame:
        frame = self._frame(table)
        hidden = self.hidden_columns(role)
        drop = [c for c in frame.columns if c in hidden]
        return frame.drop(columns=drop) if drop else frame

    def schema(self, table: str, role: Optional[str] = None) -> list[ColumnInfo]:
        frame = self._masked(table, role)
        return [ColumnInfo(str(c), str(frame[c].dtype), True) for c in frame.columns]

    def query(self, spec: dict[str, Any], *, role: Optional[str] = None) -> dict[str, Any]:
        if not isinstance(spec, dict):
            raise SchemaInvalidError("sql_spec must be an object")
        name = spec.get("table")
        if not isinstance(name, str) or not name:
            raise SchemaInvalidError("sql_spec.table is required")
        capped = dict(spec)
        capped["limit"] = max(1, min(int(spec.get("limit") or 50), self.config.row_cap))
        return run_spec_on_frame(self._masked(name, role), capped, table=f"{self.id}.{name}", entity=self.entity)
