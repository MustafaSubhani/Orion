"""Lazy sentence-transformer embeddings. Pinned to all-MiniLM-L6-v2 (384-d)."""

from __future__ import annotations

from harness.config import EMBEDDING_MODEL

_model = None


def _load():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(EMBEDDING_MODEL)
    return _model


def get_embedding(text: str) -> list[float]:
    return _load().encode(text, show_progress_bar=False).tolist()


def get_embeddings_batch(texts: list[str], batch_size: int = 64) -> list[list[float]]:
    return _load().encode(
        texts,
        batch_size=batch_size,
        show_progress_bar=False,
        convert_to_numpy=True,
    ).tolist()
