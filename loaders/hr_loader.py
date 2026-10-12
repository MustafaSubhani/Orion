"""
Orion â€” HR Loader
Handles all HR-domain data sources:
  - HR_Data_MNC_Data Science Lovers.csv (244 MB, 2M rows) -> streaming + stratified sampling
  - HR Anaytics.xlsx (500 rows) -> full ingestion
  - Business-Conduct-Policy.pdf (0.77 MB) -> page-level semantic chunking

Design Principles:
  - Streaming read for massive CSV (chunksize=5000) -> avoids OOM
  - Stratified sampling by (Department Ã— Location Ã— Status) -> representative coverage
  - Per-record text sentences for dense metadata context
  - Policy PDF chunked at sentence boundaries
"""

import os
import hashlib
import pandas as pd

import pypdf

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HR_DIR = os.path.join(PROJECT_ROOT, "data", "hr")

# â”€â”€ Config â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# How many rows to sample from the 2M-row MNC CSV
MNC_SAMPLE_SIZE = 5_000     # 5k representative rows (reduced from 200k for CPU limits)
MNC_CHUNK_SIZE = 5_000      # Streaming chunk size (rows read per iteration)
MNC_TEXT_BATCH = 100        # Rows per embedded text chunk

POLICY_CHUNK_SIZE = 800     # Characters per policy PDF chunk
POLICY_OVERLAP = 150


# â”€â”€ MNC 2M-row HR CSV (Streaming + Stratified Sampling) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

# Actual column names in the MNC CSV
_MNC_COLS = [
    "Employee_ID", "Full_Name", "Department", "Job_Title",
    "Hire_Date", "Location", "Performance_Rating", "Experience_Years",
    "Status", "Work_Mode", "Salary_INR",
]

def _mnc_row_to_text(row: pd.Series) -> str:
    """Format one MNC CSV row as readable employee record text."""
    parts = []
    for col in _MNC_COLS:
        val = row.get(col, None)
        if pd.notna(val) and str(val).strip():
            parts.append(f"{col}: {val}")
    return " | ".join(parts)


def load_mnc_hr_csv() -> list[dict]:
    """
    Stream the 2M-row MNC CSV and produce stratified-sampled text chunks.
    Target: MNC_SAMPLE_SIZE rows, evenly spread across Dept Ã— Location Ã— Status.
    """
    fpath = os.path.join(HR_DIR, "HR_Data_MNC_Data Science Lovers.csv")
    if not os.path.exists(fpath):
        print(f"  [HRLoader] MNC CSV not found: {fpath}")
        return []

    print(f"  [HRLoader] Streaming MNC CSV (2M rows) â€” target {MNC_SAMPLE_SIZE:,} samplesâ€¦")

    # --- Pass 1: collect group keys for stratification ---
    group_counts: dict[tuple, int] = {}
    try:
        for chunk in pd.read_csv(
            fpath, chunksize=MNC_CHUNK_SIZE, low_memory=False,
            usecols=lambda c: c in _MNC_COLS,
        ):
            for key in zip(
                chunk.get("Department", "Unknown").fillna("Unknown"),
                chunk.get("Location", "Unknown").fillna("Unknown"),
                chunk.get("Status", "Unknown").fillna("Unknown"),
            ):
                group_counts[key] = group_counts.get(key, 0) + 1
    except Exception as e:
        print(f"  [HRLoader] Error scanning MNC CSV groups: {e}")
        # Fallback: just sample uniformly
        group_counts = {}

    n_groups = max(len(group_counts), 1)
    per_group_quota = max(1, MNC_SAMPLE_SIZE // n_groups)
    group_sampled: dict[tuple, int] = {}

    # --- Pass 2: collect sampled rows ---
    sampled_rows: list[pd.Series] = []
    try:
        for chunk in pd.read_csv(
            fpath, chunksize=MNC_CHUNK_SIZE, low_memory=False,
            usecols=lambda c: c in _MNC_COLS,
        ):
            chunk = chunk.fillna("")
            for _, row in chunk.iterrows():
                key = (
                    str(row.get("Department", "Unknown")),
                    str(row.get("Location", "Unknown")),
                    str(row.get("Status", "Unknown")),
                )
                if group_sampled.get(key, 0) < per_group_quota:
                    sampled_rows.append(row)
                    group_sampled[key] = group_sampled.get(key, 0) + 1
                # Hard cap to prevent runaway memory
                if len(sampled_rows) >= MNC_SAMPLE_SIZE:
                    break
            if len(sampled_rows) >= MNC_SAMPLE_SIZE:
                break
    except Exception as e:
        print(f"  [HRLoader] Error reading MNC CSV: {e}")
        return []

    print(f"  [HRLoader] Sampled {len(sampled_rows):,} rows from MNC CSV")

    # --- Batch rows into text chunks ---
    chunks = []
    for i in range(0, len(sampled_rows), MNC_TEXT_BATCH):
        batch = sampled_rows[i:i + MNC_TEXT_BATCH]
        lines = [_mnc_row_to_text(row) for row in batch]
        text = "\n".join(l for l in lines if l.strip())
        if text:
            chunks.append({
                "id": f"mnc_hr_batch_{i // MNC_TEXT_BATCH}",
                "text": text,
                "metadata": {
                    "source": "HR_Data_MNC_Data Science Lovers.csv",
                    "type": "hr_employee",
                    "row_start": i,
                    "row_end": min(i + MNC_TEXT_BATCH, len(sampled_rows)),
                },
            })

    print(f"  [HRLoader] MNC CSV -> {len(chunks)} text chunks")
    return chunks


# â”€â”€ HR Analytics XLSX (500 rows) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

# Expected columns in HR Analytics XLSX
_ANALYTICS_PREFERRED_COLS = [
    "EmployeeID", "FirstName", "LastName", "Gender", "DOB", "Age",
    "HireDate", "Department", "JobTitle", "SalaryINR", "ManagerID",
    "Location", "Email", "Phone", "PerformanceScore", "Education", "Active",
]

def _analytics_row_to_text(row: pd.Series, cols: list[str]) -> str:
    parts = []
    for col in cols:
        val = row.get(col, None)
        if pd.notna(val) and str(val).strip():
            parts.append(f"{col}: {val}")
    return " | ".join(parts)


def load_hr_analytics_xlsx() -> list[dict]:
    """
    Load the 500-row HR Analytics Excel file.
    Full ingestion since it's small; 50-row batches for good chunk granularity.
    """
    fpath = os.path.join(HR_DIR, "HR Anaytics.xlsx")
    if not os.path.exists(fpath):
        print(f"  [HRLoader] HR Analytics XLSX not found: {fpath}")
        return []

    try:
        df = pd.read_excel(fpath, engine="openpyxl")
        df = df.fillna("")
        cols = [c for c in _ANALYTICS_PREFERRED_COLS if c in df.columns]
        if not cols:
            cols = df.columns.tolist()

        BATCH = 50
        chunks = []
        for i in range(0, len(df), BATCH):
            batch = df.iloc[i:i + BATCH]
            lines = [_analytics_row_to_text(row, cols) for _, row in batch.iterrows()]
            text = "\n".join(l for l in lines if l.strip())
            if text:
                chunks.append({
                    "id": f"hr_analytics_batch_{i // BATCH}",
                    "text": text,
                    "metadata": {
                        "source": "HR Anaytics.xlsx",
                        "type": "hr_employee",
                        "row_start": i,
                        "row_end": min(i + BATCH, len(df)),
                    },
                })

        print(f"  [HRLoader] HR Analytics XLSX -> {len(df)} rows, {len(chunks)} chunks")
        return chunks
    except Exception as e:
        print(f"  [HRLoader] Error reading HR Analytics XLSX: {e}")
        return []


# â”€â”€ Business Conduct Policy PDF â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _chunk_text(text: str, chunk_size: int = POLICY_CHUNK_SIZE, overlap: int = POLICY_OVERLAP) -> list[str]:
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end].strip()
        if len(chunk) >= 60:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = end - overlap
    return chunks


import docx

def load_corporate_documents() -> list[dict]:
    """Extract and chunk all Corporate policy and sustainability PDFs and DOCX files."""
    chunks = []
    chunk_id = 0
    
    # We now fetch from DOCS_DIR
    DOCS_DIR = os.path.join(PROJECT_ROOT, "data", "documents")
    if not os.path.exists(DOCS_DIR):
        print(f"  [HRLoader] Docs dir not found: {DOCS_DIR}")
        return chunks

    doc_files = [f for f in os.listdir(DOCS_DIR) if f.lower().endswith(".pdf") or f.lower().endswith(".docx")]
    
    for doc_name in doc_files:
        fpath = os.path.join(DOCS_DIR, doc_name)
        try:
            full_text = ""
            if doc_name.lower().endswith(".pdf"):
                reader = pypdf.PdfReader(fpath)
                for page in reader.pages:
                    txt = page.extract_text()
                    if txt:
                        full_text += txt + "\n\n"
            elif doc_name.lower().endswith(".docx"):
                doc = docx.Document(fpath)
                for para in doc.paragraphs:
                    if para.text.strip():
                        full_text += para.text + "\n\n"
            
            text_chunks = _chunk_text(full_text)
            for i, chunk in enumerate(text_chunks):
                chunks.append({
                    "id": f"hr_{doc_name[:5]}_chunk_{chunk_id}",
                    "text": chunk,
                    "metadata": {
                        "source": doc_name,
                        "type": "hr_policy_doc",
                        "chunk_index": i,
                    },
                })
                chunk_id += 1
            print(f"  [HRLoader] {doc_name} -> {len(text_chunks)} chunks")
        except Exception as e:
            print(f"  [HRLoader] Error reading {doc_name}: {e}")
            
    return chunks

# â”€â”€ Public Entry Point â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def load_hr_data() -> list[dict]:
    """
    Master loader â€” aggregates all HR data sources.
    Returns a unified list of {id, text, metadata} dicts.
    """
    print("\n[HRLoader] Starting HR data load...")
    all_chunks: list[dict] = []

    all_chunks.extend(load_mnc_hr_csv())
    all_chunks.extend(load_hr_analytics_xlsx())
    all_chunks.extend(load_corporate_documents())

    print(f"[HRLoader] Total HR chunks: {len(all_chunks)}")
    for c in all_chunks:
        md = c.setdefault("metadata", {})
        src = str(md.get("source", ""))
        md["entity"] = "MNC"
        md["modality"] = "policy" if md.get("type") == "hr_policy_doc" or src.lower().endswith(".pdf") else "table"
    return all_chunks

