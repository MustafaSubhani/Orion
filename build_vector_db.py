"""
Orion — Build Vector Database
===============================
Rebuilds all ChromaDB collections from new real data.

Collections built:
  - finance_data  : SEC filings + Apple financials + FRED + products
  - hr_data       : MNC 2M-row CSV (sampled) + HR Analytics XLSX + Policy PDF
  - scheduler_data: Calendar JSON (50k sampled) + ICS

Usage:
  # Rebuild all collections
  python build_vector_db.py

  # Rebuild a single collection
  python build_vector_db.py --collection finance
  python build_vector_db.py --collection hr
  python build_vector_db.py --collection scheduler
"""

import sys
import os
import argparse
import time

# Ensure project root is on path
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

from loaders.hr_loader import load_hr_data
from loaders.scheduler_loader import load_scheduler_data
from loaders.finance_loader import load_finance_data
from retrieval.vector_store import add_documents, delete_collection


def build_finance_collection():
    print("\n" + "=" * 60)
    print("Building FINANCE collection…")
    print("=" * 60)
    t0 = time.time()
    delete_collection("finance_data")
    chunks = load_finance_data()
    if not chunks:
        print("[build] WARNING: No finance chunks produced — check data paths.")
        return
    documents = [c["text"] for c in chunks]
    ids = [c["id"] for c in chunks]
    metadatas = [c["metadata"] for c in chunks]
    add_documents("finance_data", documents, ids, metadatas)
    elapsed = time.time() - t0
    print(f"[build] Finance collection done: {len(chunks)} chunks in {elapsed:.1f}s")


def build_hr_collection():
    print("\n" + "=" * 60)
    print("Building HR collection…")
    print("=" * 60)
    t0 = time.time()
    delete_collection("hr_data")
    chunks = load_hr_data()
    if not chunks:
        print("[build] WARNING: No HR chunks produced — check data paths.")
        return
    documents = [c["text"] for c in chunks]
    ids = [c["id"] for c in chunks]
    metadatas = [c["metadata"] for c in chunks]
    add_documents("hr_data", documents, ids, metadatas)
    elapsed = time.time() - t0
    print(f"[build] HR collection done: {len(chunks)} chunks in {elapsed:.1f}s")


def build_scheduler_collection():
    print("\n" + "=" * 60)
    print("Building SCHEDULER collection…")
    print("=" * 60)
    t0 = time.time()
    delete_collection("scheduler_data")
    chunks = load_scheduler_data()
    if not chunks:
        print("[build] WARNING: No scheduler chunks produced — check data paths.")
        return
    documents = [c["text"] for c in chunks]
    ids = [c["id"] for c in chunks]
    metadatas = [c["metadata"] for c in chunks]
    add_documents("scheduler_data", documents, ids, metadatas)
    elapsed = time.time() - t0
    print(f"[build] Scheduler collection done: {len(chunks)} chunks in {elapsed:.1f}s")


BUILDERS = {
    "finance": build_finance_collection,
    "hr": build_hr_collection,
    "scheduler": build_scheduler_collection,
}


def main():
    parser = argparse.ArgumentParser(description="Build Orion vector database.")
    parser.add_argument(
        "--collection",
        choices=list(BUILDERS.keys()),
        default=None,
        help="Rebuild only this collection (default: all)",
    )
    args = parser.parse_args()

    print("\n" + "=" * 60)
    print("Orion — Vector Database Builder")
    print("=" * 60)

    total_start = time.time()

    if args.collection:
        BUILDERS[args.collection]()
    else:
        build_finance_collection()
        build_hr_collection()
        build_scheduler_collection()

    total = time.time() - total_start
    print("\n" + "=" * 60)
    print(f"[build] All done! Total time: {total:.1f}s")
    print("=" * 60)


if __name__ == "__main__":
    main()
