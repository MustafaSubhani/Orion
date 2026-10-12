"""Queryable frozen tables. SQL/chart/oracle all read from here — never from data/EXCEL."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import pandas as pd

from harness.config import DATA_DIR, FROZEN_DIR
from harness.errors import SchemaInvalidError

TABLE_FILES = {
    "hr": FROZEN_DIR / "hr.parquet",
    "applfin": FROZEN_DIR / "applfin.parquet",
    "fred_cpi": FROZEN_DIR / "fred_cpi.parquet",
    "fred_fedfunds": FROZEN_DIR / "fred_fedfunds.parquet",
    "fred_umcsent": FROZEN_DIR / "fred_umcsent.parquet",
    "calendar": FROZEN_DIR / "calendar.parquet",
    "products": FROZEN_DIR / "products.parquet",
}

EVENT_CONTENT_COLS = (
    "title",
    "organizer",
    "start_time",
    "end_time",
    "location",
    "attendees",
)


def unique_calendar_events(df: pd.DataFrame) -> pd.DataFrame:
    """Count meetings, not cloned rows with distinct event_id."""
    cols = [c for c in EVENT_CONTENT_COLS if c in df.columns]
    if not cols:
        return df
    return df.drop_duplicates(subset=cols, ignore_index=True)


TABLE_ENTITY = {
    "hr": "MNC",
    "applfin": "AAPL",
    "fred_cpi": "FRED",
    "fred_fedfunds": "FRED",
    "fred_umcsent": "FRED",
    "calendar": "SYNTH_CAL",
    "products": "AAPL",
}

HR_COLS = [
    "Employee_ID",
    "Full_Name",
    "Department",
    "Job_Title",
    "Hire_Date",
    "Location",
    "Performance_Rating",
    "Experience_Years",
    "Status",
    "Work_Mode",
    "Salary_INR",
]

PRODUCT_COLS = [
    "product_id",
    "category",
    "model_name",
    "release_year",
    "price",
    "rating",
    "color",
    "storage",
    "in_stock",
    "review_count",
    "country_origin",
]


def list_tables(*, include_kb: bool = False) -> list[str]:
    names = list(TABLE_FILES.keys())
    if include_kb:
        try:
            from kb.store import kb_table_names

            names.extend(kb_table_names())
        except Exception:
            pass
    return names


def table_entity(name: str) -> str:
    if name.startswith("kb_"):
        return "KB"
    if name not in TABLE_ENTITY:
        raise SchemaInvalidError(f"Unknown table '{name}'")
    return TABLE_ENTITY[name]


def schema_text(*, include_kb: bool = False) -> str:
    lines = []
    for name in list_tables():
        try:
            df = load_table(name)
            cols = ", ".join(f"{c}:{df[c].dtype}" for c in df.columns)
            lines.append(f"TABLE {name} entity={TABLE_ENTITY[name]} rows={len(df)} columns=[{cols}]")
        except Exception as exc:
            lines.append(f"TABLE {name} entity={TABLE_ENTITY[name]} UNAVAILABLE ({exc})")
    if include_kb:
        lines.append("--- live KB (user uploads, not eval gold) ---")
        try:
            from kb.store import kb_table_names

            kb_names = kb_table_names()
        except Exception:
            kb_names = []
        if not kb_names:
            lines.append("(no kb_* tables)")
        for name in kb_names:
            try:
                df = load_table(name)
                cols = ", ".join(f"{c}:{df[c].dtype}" for c in df.columns)
                lines.append(f"TABLE {name} entity=KB rows={len(df)} columns=[{cols}]")
            except Exception as exc:
                lines.append(f"TABLE {name} entity=KB UNAVAILABLE ({exc})")
    return "\n".join(lines)


@lru_cache(maxsize=16)
def load_table(name: str) -> pd.DataFrame:
    if name.startswith("kb_"):
        from kb.store import load_kb_table

        return load_kb_table(name)
    if name not in TABLE_FILES:
        raise SchemaInvalidError(f"Unknown table '{name}'. Known: {list(TABLE_FILES)}")
    frozen = TABLE_FILES[name]
    if frozen.exists():
        return _read(frozen)
    return _fallback(name)


def _read(path: Path) -> pd.DataFrame:
    if path.suffix == ".parquet":
        try:
            return pd.read_parquet(path)
        except Exception:
            csv_fallback = path.with_suffix(".csv")
            if csv_fallback.exists():
                return pd.read_csv(csv_fallback)
            raise
    return pd.read_csv(path)


def _fallback(name: str) -> pd.DataFrame:
    if name == "hr":
        path = DATA_DIR / "hr" / "HR_Data_MNC_Data Science Lovers.csv"
        if not path.exists():
            raise SchemaInvalidError("HR source CSV missing; run python -m retrieval.freeze")
        usecols = lambda c: c in HR_COLS  # noqa: E731
        return pd.read_csv(path, usecols=usecols, nrows=20_000, low_memory=False)
    if name == "applfin":
        path = DATA_DIR / "finance" / "ApplFin.csv"
        if not path.exists():
            raise SchemaInvalidError("ApplFin.csv missing")
        return pd.read_csv(path)
    fred = {
        "fred_cpi": DATA_DIR / "finance" / "fred_cpi.csv",
        "fred_fedfunds": DATA_DIR / "finance" / "fred_fedfunds.csv",
        "fred_umcsent": DATA_DIR / "finance" / "fred_umcsent.csv",
    }
    if name in fred:
        if not fred[name].exists():
            raise SchemaInvalidError(f"{name} CSV missing")
        return pd.read_csv(fred[name])
    if name == "calendar":
        frozen_csv = FROZEN_DIR / "calendar.csv"
        if frozen_csv.exists():
            return pd.read_csv(frozen_csv)
        raise SchemaInvalidError("Calendar table not frozen; run python -m retrieval.freeze")
    if name == "products":
        path = DATA_DIR / "finance" / "apple_products_dataset_100k.csv"
        if not path.exists():
            raise SchemaInvalidError("Apple products CSV missing")
        usecols = lambda c: c in PRODUCT_COLS  # noqa: E731
        return pd.read_csv(path, usecols=usecols, nrows=12_000, low_memory=False)
    raise SchemaInvalidError(f"No fallback for table '{name}'")


def clear_cache() -> None:
    load_table.cache_clear()
