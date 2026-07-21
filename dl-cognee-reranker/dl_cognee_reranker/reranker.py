"""Reranker service wrapping FlagReranker (bge-reranker-v2-m3)."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class Reranker:
    def __init__(self, model_name: str = "BAAI/bge-reranker-v2-m3"):
        self._model_name = model_name
        self._model = None

    async def warm_up(self) -> None:
        """Load the reranker model. Blocks /health until done."""
        from FlagEmbedding import FlagReranker

        self._model = FlagReranker(
            self._model_name,
            use_fp16=False,
            device="cpu",
        )
        _ = self._model.compute_score(["warmup", "test"])
        logger.info("reranker loaded: %s", self._model_name)

    def rerank(self, query: str, candidates: list[str]) -> list[float]:
        """Return relevance scores for each candidate against the query."""
        if self._model is None:
            raise RuntimeError("reranker not warmed up")
        pairs = [[query, c] for c in candidates]
        return self._model.compute_score(pairs)
