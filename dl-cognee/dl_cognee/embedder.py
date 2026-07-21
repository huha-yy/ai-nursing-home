"""Embedding service — BGEM3FlagModel warm-up + batch embed (1024-dim)."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class Embedder:
    def __init__(self, model_name: str = "BAAI/bge-m3"):
        self._model_name = model_name
        self._model = None

    async def warm_up(self) -> None:
        """Load bge-m3 via FlagEmbedding. Blocks /health until done."""
        from FlagEmbedding import BGEM3FlagModel

        self._model = BGEM3FlagModel(
            self._model_name,
            use_fp16=False,
            device="cpu",
        )
        # Single test embedding to trigger model download + CPU compile.
        _ = self._model.encode(["warmup"])["dense_vecs"].tolist()
        logger.info("bge-m3 embedding model loaded: %s (1024-dim)", self._model_name)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Return 1024-dim embeddings for a batch of texts. Must call warm_up first."""
        if self._model is None:
            raise RuntimeError("embedder not warmed up")
        result = self._model.encode(texts)
        # result['dense_vecs']: np.ndarray, shape (n, 1024)
        return result["dense_vecs"].tolist()
