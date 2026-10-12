"""
Orion â€” Finance Loader
Handles all finance-domain data sources:
  - SEC Edgar filings (10-K and 10-Q, ~473 MB, 60 documents)
  - Apple financial CSVs (ApplFin.csv, Apple Financial.csv)
  - FRED macroeconomic CSVs (CPI, Fed Funds Rate, Consumer Sentiment)
  - Apple products 100k dataset CSV

Design Principles:
  - Semantic chunking with overlap for SEC text documents
  - Deduplication fingerprinting to skip boilerplate repeats
  - Rich metadata per chunk (source, doc_type, fiscal_year, date_range)
  - No pandas-heavy operations on huge files (CSVs are small here)
"""

import os
import hashlib
import re
import pandas as pd

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FINANCE_DIR = os.path.join(PROJECT_ROOT, "data", "finance")
SEC_DIR = os.path.join(FINANCE_DIR, "sec-edgar-filings")

# â”€â”€ Chunking config â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
CHUNK_SIZE = 1200       # characters per chunk
CHUNK_OVERLAP = 200     # overlap between consecutive chunks
MIN_CHUNK_LEN = 80      # drop chunks shorter than this (headers, whitespace)
DEDUP_PREFIX_LEN = 180  # fingerprint length for dedup (boilerplate detection)


def _chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping character-window chunks."""
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end].strip()
        if len(chunk) >= MIN_CHUNK_LEN:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = end - overlap
    return chunks


def _fingerprint(text: str) -> str:
    prefix = re.sub(r"\s+", " ", text[:DEDUP_PREFIX_LEN].strip().lower())
    return hashlib.md5(prefix.encode()).hexdigest()


# â”€â”€ SEC Filings â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _read_sec_file(file_path: str) -> str:
    """Read a SEC filing text file with fallback encodings."""
    for enc in ("utf-8", "latin-1", "cp1252"):
        try:
            with open(file_path, "r", encoding=enc, errors="replace") as f:
                return f.read()
        except Exception:
            continue
    return ""


def _clean_sec_text(text: str) -> str:
    """Remove SGML tags, excess whitespace, and noise from SEC filings."""
    text = re.sub(r"<[^>]+>", " ", text)            # strip HTML/SGML tags
    text = re.sub(r"&[a-z]+;", " ", text)            # strip HTML entities
    text = re.sub(r"={5,}", "", text)                 # strip horizontal rules
    text = re.sub(r"\s{3,}", "\n\n", text)            # collapse excess whitespace
    return text.strip()


def load_sec_filings() -> list[dict]:
    """
    Walk SEC Edgar filing directories per Ticker and doc_type.
    Produces text chunks annotated with company ticker for comparative RAG.
    Returns list of {id, text, metadata} dicts.
    """
    chunks = []
    seen_fingerprints: set[str] = set()
    chunk_id = 0

    if not os.path.exists(SEC_DIR):
        print(f"  [FinanceLoader] SEC dir not found: {SEC_DIR}")
        return chunks

    tickers = [d for d in os.listdir(SEC_DIR) if os.path.isdir(os.path.join(SEC_DIR, d))]
    
    for ticker in tickers:
        ticker_dir = os.path.join(SEC_DIR, ticker)
        for doc_type in ("10-K", "10-Q", "8-K"):
            type_dir = os.path.join(ticker_dir, doc_type)
            if not os.path.isdir(type_dir):
                continue

            filing_dirs = sorted(os.listdir(type_dir))
            print(f"  [FinanceLoader] Loading {len(filing_dirs)} {doc_type} filings for {ticker}...")

            for filing_name in filing_dirs:
                filing_path = os.path.join(type_dir, filing_name)
                if not os.path.isdir(filing_path):
                    continue

                txt_files = [
                    f for f in os.listdir(filing_path)
                    if f.endswith(".txt") or f.endswith(".htm") or f.endswith(".html")
                ]
                if not txt_files:
                    continue

                primary = max(txt_files, key=lambda f: os.path.getsize(os.path.join(filing_path, f)))
                full_path = os.path.join(filing_path, primary)
                raw = _read_sec_file(full_path)
                if not raw:
                    continue

                text = _clean_sec_text(raw)
                fiscal_year = filing_name[:4] if len(filing_name) >= 4 else "unknown"

                for chunk in _chunk_text(text):
                    fp = _fingerprint(chunk)
                    if fp in seen_fingerprints:
                        continue 
                    seen_fingerprints.add(fp)

                    chunks.append({
                        "id": f"sec_{ticker}_{doc_type}_{chunk_id}",
                        "text": chunk,
                        "metadata": {
                            "source": f"SEC_{ticker}_{doc_type}_{filing_name}",
                            "company": ticker,
                            "doc_type": doc_type,
                            "fiscal_year": fiscal_year,
                            "type": "sec_filing",
                        },
                    })
                    chunk_id += 1

    chunks = chunks[:25000] # Increased limit to 25k chunks (roughly 500MB of dense SEC embeddings) to hit 2GB goals reasonably without breaking memory
    print(f"  [FinanceLoader] SEC filings -> {len(chunks)} unique chunks")
    return chunks


# â”€â”€ Apple Financial CSVs â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _csv_row_to_sentence(row: pd.Series, columns: list[str]) -> str:
    """Convert a DataFrame row into a readable natural-language sentence."""
    parts = []
    for col in columns:
        val = row.get(col)
        if pd.notna(val) and str(val).strip():
            parts.append(f"{col}: {val}")
    return " | ".join(parts)


def load_apple_financial_csvs() -> list[dict]:
    """Load small Apple financial CSV files as sentence chunks."""
    chunks = []
    csv_files = {
        "ApplFin.csv": "apple_fin",
        "Apple Financial.csv": "apple_financial",
    }

    for filename, id_prefix in csv_files.items():
        fpath = os.path.join(FINANCE_DIR, filename)
        if not os.path.exists(fpath):
            print(f"  [FinanceLoader] Not found: {fpath}, skipping.")
            continue
        try:
            df = pd.read_csv(fpath)
            cols = df.columns.tolist()
            # Group rows into chunks of 10 for context density
            BATCH = 10
            for i in range(0, len(df), BATCH):
                batch = df.iloc[i:i + BATCH]
                lines = [_csv_row_to_sentence(row, cols) for _, row in batch.iterrows()]
                text = "\n".join(l for l in lines if l.strip())
                if text:
                    chunks.append({
                        "id": f"{id_prefix}_batch_{i // BATCH}",
                        "text": text,
                        "metadata": {
                            "source": filename,
                            "type": "financial_csv",
                            "row_start": i,
                            "row_end": min(i + BATCH, len(df)),
                        },
                    })
            print(f"  [FinanceLoader] {filename} -> {len(df)} rows, {len(chunks)} chunks so far")
        except Exception as e:
            print(f"  [FinanceLoader] Error reading {filename}: {e}")

    return chunks


# â”€â”€ FRED Macroeconomic Data â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def load_fred_csvs() -> list[dict]:
    """
    Load FRED time-series CSVs into date-range annotated chunks.
    Groups rows in 24-row windows (2 years of monthly data, or ~24 days of daily).
    """
    fred_files = {
        "fred_cpi.csv": ("DATE", "CPIAUCSL", "Consumer Price Index (CPI)"),
        "fred_fedfunds.csv": ("DATE", "DFF", "Federal Funds Effective Rate"),
        "fred_umcsent.csv": ("DATE", "UMCSENT", "University of Michigan Consumer Sentiment"),
    }
    chunks = []
    chunk_id = 0
    WINDOW = 24

    for filename, (date_col, val_col, label) in fred_files.items():
        fpath = os.path.join(FINANCE_DIR, filename)
        if not os.path.exists(fpath):
            print(f"  [FinanceLoader] Not found: {fpath}, skipping.")
            continue
        try:
            df = pd.read_csv(fpath)
            for i in range(0, len(df), WINDOW):
                window = df.iloc[i:i + WINDOW]
                date_from = window[date_col].iloc[0]
                date_to = window[date_col].iloc[-1]
                rows = "\n".join(
                    f"  {row[date_col]}: {row[val_col]}"
                    for _, row in window.iterrows()
                    if pd.notna(row[val_col])
                )
                text = (
                    f"{label} data from {date_from} to {date_to}:\n{rows}"
                )
                chunks.append({
                    "id": f"fred_{filename.replace('.csv','').replace('fred_', '')}_{chunk_id}",
                    "text": text,
                    "metadata": {
                        "source": filename,
                        "type": "fred_macro",
                        "indicator": val_col,
                        "date_range": f"{date_from} to {date_to}",
                    },
                })
                chunk_id += 1
        except Exception as e:
            print(f"  [FinanceLoader] Error reading {filename}: {e}")

    print(f"  [FinanceLoader] FRED CSVs -> {len(chunks)} chunks")
    return chunks


# â”€â”€ Apple Products Dataset â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def load_products_csv() -> list[dict]:
    """
    Load the 100k Apple products dataset.
    Groups records in 50-row batches as product catalog chunks.
    """
    fpath = os.path.join(FINANCE_DIR, "apple_products_dataset_100k.csv")
    if not os.path.exists(fpath):
        print(f"  [FinanceLoader] Products CSV not found: {fpath}")
        return []

    chunks = []
    BATCH = 50
    try:
        df = pd.read_csv(fpath)
        cols = df.columns.tolist()
        for i in range(0, len(df), BATCH):
            if len(chunks) >= 40:
                break
            batch = df.iloc[i:i + BATCH]
            lines = [_csv_row_to_sentence(row, cols) for _, row in batch.iterrows()]
            text = "\n".join(l for l in lines if l.strip())
            if text:
                chunks.append({
                    "id": f"products_batch_{i // BATCH}",
                    "text": text,
                    "metadata": {
                        "source": "apple_products_dataset_100k.csv",
                        "type": "product_catalog",
                        "row_start": i,
                        "row_end": min(i + BATCH, len(df)),
                    },
                })
        print(f"  [FinanceLoader] Products CSV -> {len(df)} rows, {len(chunks)} chunks")
    except Exception as e:
        print(f"  [FinanceLoader] Error reading products CSV: {e}")

    return chunks


# â”€â”€ Excel Financial History â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def load_aapl_excel() -> list[dict]:
    """Load AAPL Historical Financials Excel if it has data."""
    fpath = os.path.join(FINANCE_DIR, "AAPL_Historical_Financials.xlsx")
    if not os.path.exists(fpath):
        return []
    try:
        df = pd.read_excel(fpath, engine="openpyxl")
        if df.empty:
            print(f"  [FinanceLoader] AAPL_Historical_Financials.xlsx is empty, skipping.")
            return []
        BATCH = 20
        chunks = []
        cols = df.columns.tolist()
        for i in range(0, len(df), BATCH):
            batch = df.iloc[i:i + BATCH]
            lines = [_csv_row_to_sentence(row, cols) for _, row in batch.iterrows()]
            text = "\n".join(l for l in lines if l.strip())
            if text:
                chunks.append({
                    "id": f"aapl_excel_batch_{i // BATCH}",
                    "text": text,
                    "metadata": {"source": "AAPL_Historical_Financials.xlsx", "type": "excel_financials"},
                })
        print(f"  [FinanceLoader] AAPL Excel -> {len(chunks)} chunks")
        return chunks
    except Exception as e:
        print(f"  [FinanceLoader] Error reading AAPL Excel: {e}")
        return []


# â”€â”€ Public Entry Point â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def load_finance_data() -> list[dict]:
    """
    Master loader â€” aggregates all finance data sources.
    Returns a unified list of {id, text, metadata} dicts.
    """
    print("\n[FinanceLoader] Starting finance data loadâ€¦")
    all_chunks: list[dict] = []

    all_chunks.extend(load_sec_filings())
    all_chunks.extend(load_apple_financial_csvs())
    all_chunks.extend(load_fred_csvs())
    all_chunks.extend(load_products_csv())
    all_chunks.extend(load_aapl_excel())

    print(f"[FinanceLoader] Total finance chunks: {len(all_chunks)}")
    for c in all_chunks:
        md = c.setdefault("metadata", {})
        typ = str(md.get("type", ""))
        src = str(md.get("source", ""))
        if typ == "sec_filing" or src.startswith("SEC_"):
            md["entity"] = str(md.get("company") or "MSFT")
            md["modality"] = "filing"
        elif typ == "fred_macro" or src.startswith("fred_"):
            md["entity"] = "FRED"
            md["modality"] = "macro"
        else:
            md["entity"] = "AAPL"
            md["modality"] = "table"
    return all_chunks

