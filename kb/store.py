"""Catalog + files for the live KB. Isolated from data/frozen and bench/."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from harness import tenancy
from harness.config import KB_DIR
from harness.errors import SchemaInvalidError

_LOCK = threading.Lock()


def base_root() -> Path:
    """The deployment's KB directory (the default tenant's store)."""
    override = os.environ.get("ORION_KB_DIR", "").strip()
    return Path(override) if override else KB_DIR


def root() -> Path:
    """The bound tenant's KB directory: `kb/` for `default`, `kb/tenants/<slug>/` for any other."""
    return tenancy.scoped_dir(base_root())


def collection_name() -> str:
    """The bound tenant's vector collection."""
    from harness.config import COLLECTION_KB

    return tenancy.collection(COLLECTION_KB)


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
    from harness.fsutil import atomic_write

    ensure_kb_dirs()
    atomic_write(catalog_path(), json.dumps(data, indent=2, ensure_ascii=False))


def delete_resource(resource_id: str) -> bool:
    """Remove a resource, its file, its table, and its chunks. Returns False if it does not exist."""
    with _LOCK:
        cat = load_catalog()
        record = next((r for r in cat["resources"] if r.get("id") == resource_id), None)
        if record is None:
            return False
        cat["resources"] = [r for r in cat["resources"] if r.get("id") != resource_id]
        table_name = record.get("table")
        meta = cat["tables"].pop(table_name, None) if table_name else None
        save_catalog(cat)
    for path in files_dir().glob(f"{resource_id}_*"):
        path.unlink(missing_ok=True)
    (chunks_dir() / f"{resource_id}.jsonl").unlink(missing_ok=True)
    if meta and meta.get("path"):
        (root() / str(meta["path"])).unlink(missing_ok=True)
    try:
        from retrieval.vector_store import get_or_create_collection

        get_or_create_collection(collection_name()).delete(where={"resource_id": resource_id})
    except Exception:  # the vector index is optional; the catalog is the source of truth
        pass
    return True


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
