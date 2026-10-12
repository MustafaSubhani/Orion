"""ChromaDB vector store with batch upsert and MMR re-ranking."""

from __future__ import annotations

from typing import Optional

import chromadb

from harness.config import CHROMA_DIR
from retrieval.embeddings import get_embedding, get_embeddings_batch

_INGEST_BATCH = 256
_client = None


def _get_client():
    global _client
    if _client is None:
        CHROMA_DIR.mkdir(parents=True, exist_ok=True)
        _client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    return _client


def get_or_create_collection(name: str):
    return _get_client().get_or_create_collection(
        name=name,
        metadata={"hnsw:space": "cosine"},
    )


def add_documents(
    collection_name: str,
    documents: list[str],
    ids: list[str],
    metadatas: list[dict] | None = None,
) -> None:
    collection = get_or_create_collection(collection_name)
    if metadatas is None:
        metadatas = [{}] * len(documents)
    filtered = [
        (doc, id_, meta)
        for doc, id_, meta in zip(documents, ids, metadatas)
        if doc and doc.strip()
    ]
    if not filtered:
        return
    docs, ids_, metas = map(list, zip(*filtered))
    for start in range(0, len(docs), _INGEST_BATCH):
        end = start + _INGEST_BATCH
        batch_docs = docs[start:end]
        collection.upsert(
            documents=batch_docs,
            embeddings=get_embeddings_batch(batch_docs),
            ids=ids_[start:end],
            metadatas=metas[start:end],
        )
        print(f"  [VectorStore] Upserted {min(end, len(docs))}/{len(docs)} into '{collection_name}'")


def query_collection(
    collection_name: str,
    query: str,
    top_k: int = 5,
    where: dict | None = None,
) -> dict:
    collection = get_or_create_collection(collection_name)
    kwargs = dict(
        query_embeddings=[get_embedding(query)],
        n_results=top_k,
        include=["documents", "metadatas", "distances"],
    )
    if where:
        kwargs["where"] = where
    return collection.query(**kwargs)


def query_collection_diverse(
    collection_name: str,
    query: str,
    top_k: int = 5,
    fetch_k: int | None = None,
    lambda_mult: float = 0.5,
    where: dict | None = None,
) -> dict:
    import numpy as np

    if fetch_k is None:
        fetch_k = min(top_k * 4, 100)
    raw = query_collection(collection_name, query, top_k=fetch_k, where=where)
    cand_docs = raw["documents"][0]
    cand_metas = raw["metadatas"][0]
    cand_dists = raw["distances"][0]
    if not cand_docs:
        return raw
    similarities = [1.0 - d for d in cand_dists]
    cand_emb = np.array(get_embeddings_batch(cand_docs, batch_size=64))
    norms = np.linalg.norm(cand_emb, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1, norms)
    cand_emb_n = cand_emb / norms
    selected_idx: list[int] = []
    remaining = list(range(len(cand_docs)))
    for _ in range(min(top_k, len(cand_docs))):
        best_idx = remaining[0]
        best_score = -float("inf")
        for i in remaining:
            rel = similarities[i]
            if selected_idx:
                sim_to_selected = (cand_emb_n[i] @ cand_emb_n[selected_idx].T).max()
                mmr = lambda_mult * rel - (1 - lambda_mult) * sim_to_selected
            else:
                mmr = rel
            if mmr > best_score:
                best_score = mmr
                best_idx = i
        selected_idx.append(best_idx)
        remaining.remove(best_idx)
    return {
        "documents": [[cand_docs[i] for i in selected_idx]],
        "metadatas": [[cand_metas[i] for i in selected_idx]],
        "distances": [[cand_dists[i] for i in selected_idx]],
    }


def delete_collection(collection_name: str) -> None:
    try:
        _get_client().delete_collection(collection_name)
    except Exception:
        pass


def get_collection_count(collection_name: str) -> int:
    try:
        return _get_client().get_collection(collection_name).count()
    except Exception:
        return 0
