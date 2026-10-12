"""Catalog + files for the live KB. Isolated from data/frozen and bench/."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from harness.config import KB_DIR
from harness.errors import SchemaInvalidError

_LOCK = threading.Lock()


def root() -> Path:
    override = os.environ.get("ORION_KB_DIR", "").strip()
    return Path(override) if override else KB_DIR


def files_dir() -> Path:
    return root() / "files"


def tables_dir() -> Path:
    return root() / "tables"


def chunks_dir() -> Path:
    return root() / "chunks"


def catalog_path() -> Path:
    return root() / "catalog.json"


def ensure_kb_dirs() -> None:
    for path in (root(), files_dir(), tables_dir(), chunks_dir()):
        path.mkdir(parents=True, exist_ok=True)


def _empty_catalog() -> dict[str, Any]:
    return {"resources": [], "tables": {}}


def load_catalog() -> dict[str, Any]:
    ensure_kb_dirs()
    path = catalog_path()
    if not path.exists():
        return _empty_catalog()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_catalog()
    data.setdefault("resources", [])
    data.setdefault("tables", {})
    return data


def save_catalog(data: dict[str, Any]) -> None:
    ensure_kb_dirs()
    tmp = catalog_path().with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(catalog_path())


def upsert_resource(record: dict[str, Any], table: dict[str, Any] | None = None) -> dict[str, Any]:
    with _LOCK:
        cat = load_catalog()
        rid = record["id"]
        cat["resources"] = [r for r in cat["resources"] if r.get("id") != rid]
        cat["resources"].append(record)
        if table:
            cat["tables"][table["name"]] = table
        save_catalog(cat)
        return record


def list_resources() -> list[dict[str, Any]]:
    return list(load_catalog().get("resources") or [])


def kb_table_names() -> list[str]:
    return sorted((load_catalog().get("tables") or {}).keys())


def get_table_meta(name: str) -> dict[str, Any]:
    tables = load_catalog().get("tables") or {}
    meta = tables.get(name)
    if not meta:
        raise SchemaInvalidError(f"Unknown KB table '{name}'. Known: {list(tables)}")
    return meta


def load_kb_table(name: str):
    try:
        import pandas as pd
    except ImportError as exc:
        raise SchemaInvalidError("pandas is required to query live KB tables") from exc
    meta = get_table_meta(name)
    path = root() / str(meta["path"])
    if not path.exists():
        raise SchemaInvalidError(f"KB table file missing for '{name}'")
    if path.suffix == ".parquet":
        try:
            return pd.read_parquet(path)
        except Exception:
            csv_fallback = path.with_suffix(".csv")
            if csv_fallback.exists():
                return pd.read_csv(csv_fallback)
            raise
    if path.suffix == ".jsonl":
        return pd.read_json(path, lines=True)
    return pd.read_csv(path)


def write_chunks(resource_id: str, chunks: list[dict[str, Any]]) -> Path:
    ensure_kb_dirs()
    path = chunks_dir() / f"{resource_id}.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for chunk in chunks:
            fh.write(json.dumps(chunk, ensure_ascii=False) + "\n")
    return path


def iter_chunks() -> list[dict[str, Any]]:
    ensure_kb_dirs()
    out: list[dict[str, Any]] = []
    for path in sorted(chunks_dir().glob("*.jsonl")):
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                out.append(json.loads(line))
        except (OSError, json.JSONDecodeError):
            continue
    return out
