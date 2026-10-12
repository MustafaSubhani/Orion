"""Materialize pinned parquet/CSV tables from the existing corpus. Data files are never rewritten."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

from harness.config import DATA_DIR, FROZEN_DIR, ensure_dirs
from retrieval.tables import HR_COLS, PRODUCT_COLS, clear_cache

MANIFEST_PATH = FROZEN_DIR / "MANIFEST.json"
HR_SAMPLE = 20_000
CAL_SAMPLE = 4_000
PRODUCT_SAMPLE = 12_000
SEED = 42


def _write(df: pd.DataFrame, name: str) -> dict:
    FROZEN_DIR.mkdir(parents=True, exist_ok=True)
    parquet = FROZEN_DIR / f"{name}.parquet"
    csv = FROZEN_DIR / f"{name}.csv"
    try:
        df.to_parquet(parquet, index=False)
        dest = parquet
    except Exception:
        df.to_csv(csv, index=False)
        dest = csv
    digest = hashlib.sha256(dest.read_bytes()).hexdigest()
    return {"name": name, "path": str(dest.relative_to(FROZEN_DIR.parent.parent)), "rows": int(len(df)), "sha256": digest, "columns": list(df.columns)}


def freeze_hr() -> dict:
    path = DATA_DIR / "hr" / "HR_Data_MNC_Data Science Lovers.csv"
    if not path.exists():
        return {"name": "hr", "error": f"missing {path}"}
    df = pd.read_csv(path, usecols=lambda c: c in HR_COLS, low_memory=False)
    if "Department" in df.columns and "Status" in df.columns:
        # stratified-ish: sample within groups when possible
        parts = []
        grouped = df.groupby(["Department", "Status"], dropna=False)
        per = max(1, HR_SAMPLE // max(len(grouped), 1))
        for _, g in grouped:
            parts.append(g.sample(n=min(len(g), per), random_state=SEED) if len(g) > per else g)
        sampled = pd.concat(parts, ignore_index=True)
        if len(sampled) > HR_SAMPLE:
            sampled = sampled.sample(n=HR_SAMPLE, random_state=SEED)
        df = sampled.reset_index(drop=True)
    else:
        df = df.sample(n=min(HR_SAMPLE, len(df)), random_state=SEED).reset_index(drop=True)
    return _write(df, "hr")


def freeze_applfin() -> dict:
    path = DATA_DIR / "finance" / "ApplFin.csv"
    if not path.exists():
        return {"name": "applfin", "error": "missing ApplFin.csv"}
    return _write(pd.read_csv(path), "applfin")


def freeze_fred() -> list[dict]:
    out = []
    mapping = {
        "fred_cpi": "fred_cpi.csv",
        "fred_fedfunds": "fred_fedfunds.csv",
        "fred_umcsent": "fred_umcsent.csv",
    }
    for name, filename in mapping.items():
        path = DATA_DIR / "finance" / filename
        if not path.exists():
            out.append({"name": name, "error": f"missing {filename}"})
            continue
        out.append(_write(pd.read_csv(path), name))
    return out


def freeze_calendar() -> dict:
    path = DATA_DIR / "scheduler" / "apple_synthetic_calendar_events.json"
    if not path.exists():
        return {"name": "calendar", "error": "missing calendar json"}
    rows: list[dict] = []
    try:
        import ijson

        with open(path, "rb") as fh:
            for i, event in enumerate(ijson.items(fh, "item")):
                if i % max(1, 500_000 // CAL_SAMPLE) != 0:
                    continue
                rows.append(_event_row(event))
                if len(rows) >= CAL_SAMPLE:
                    break
    except Exception:
        # fallback: json.load may OOM on 246MB; try a small python parse of first array items
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, list):
            stride = max(1, len(data) // CAL_SAMPLE)
            for event in data[::stride][:CAL_SAMPLE]:
                rows.append(_event_row(event))
    if not rows:
        return {"name": "calendar", "error": "no events sampled"}
    df = pd.DataFrame(rows)
    if "event_id" in df.columns:
        df = df.drop_duplicates(subset=["event_id"])
    return _write(df, "calendar")


def _event_row(event: dict) -> dict:
    attendees = event.get("attendees") or []
    if isinstance(attendees, str):
        att_s = attendees
    else:
        att_s = ",".join(str(a) for a in attendees)
    return {
        "event_id": str(event.get("event_id") or event.get("id") or event.get("uid") or ""),
        "title": event.get("title") or event.get("summary") or "",
        "organizer": event.get("organizer") or "",
        "start_time": str(event.get("start_time") or event.get("start") or ""),
        "end_time": str(event.get("end_time") or event.get("end") or ""),
        "location": event.get("location") or "",
        "attendees": att_s,
    }


def freeze_products() -> dict:
    path = DATA_DIR / "finance" / "apple_products_dataset_100k.csv"
    if not path.exists():
        return {"name": "products", "error": "missing apple_products_dataset_100k.csv"}
    df = pd.read_csv(path, usecols=lambda c: c in PRODUCT_COLS, low_memory=False)
    if "category" in df.columns:
        parts = []
        grouped = df.groupby("category", dropna=False)
        per = max(1, PRODUCT_SAMPLE // max(len(grouped), 1))
        for _, g in grouped:
            parts.append(g.sample(n=min(len(g), per), random_state=SEED) if len(g) > per else g)
        sampled = pd.concat(parts, ignore_index=True)
        if len(sampled) > PRODUCT_SAMPLE:
            sampled = sampled.sample(n=PRODUCT_SAMPLE, random_state=SEED)
        df = sampled.reset_index(drop=True)
    else:
        df = df.sample(n=min(PRODUCT_SAMPLE, len(df)), random_state=SEED).reset_index(drop=True)
    return _write(df, "products")


def freeze_all() -> dict:
    ensure_dirs()
    FROZEN_DIR.mkdir(parents=True, exist_ok=True)
    records = [freeze_hr(), freeze_applfin(), *freeze_fred(), freeze_calendar(), freeze_products()]
    manifest = {"tables": records}
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    clear_cache()
    return manifest


def main() -> None:
    manifest = freeze_all()
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
