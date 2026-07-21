"""dl-cognee-reranker — FastAPI app for bge-reranker-v2-m3."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel

from dl_cognee_reranker.reranker import Reranker
from dl_cognee_reranker.settings import load_settings

logger = logging.getLogger(__name__)


class RerankRequest(BaseModel):
    query: str
    candidates: list[str]
    top_k: int = 10


class RerankResult(BaseModel):
    index: int
    score: float
    text: str


class RerankResponse(BaseModel):
    results: list[RerankResult]


# GBrain-compatible /v1/rerank request (llama-server-reranker recipe).
class GBrainRerankRequest(BaseModel):
    model: str = "BAAI/bge-reranker-v2-m3"
    query: str
    documents: list[str]
    top_n: int | None = None


class GBrainRerankItem(BaseModel):
    index: int
    relevance_score: float


class GBrainRerankResponse(BaseModel):
    results: list[GBrainRerankItem]


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    reranker = Reranker(model_name=settings.reranker_model)
    await reranker.warm_up()
    app.state.reranker = reranker
    logger.info("dl-cognee-reranker ready")
    yield


app = FastAPI(lifespan=lifespan)


@app.post("/rerank")
async def rerank(req: RerankRequest) -> RerankResponse:
    reranker: Reranker = app.state.reranker
    scores = reranker.rerank(req.query, req.candidates)
    # Pair scores with candidates, sort descending by score.
    paired = list(zip(range(len(req.candidates)), scores, req.candidates))
    paired.sort(key=lambda x: x[1], reverse=True)
    top = paired[: req.top_k]
    return RerankResponse(
        results=[
            RerankResult(index=idx, score=float(s), text=t)
            for idx, s, t in top
        ]
    )


@app.post("/v1/rerank")
async def rerank_gbrain(req: GBrainRerankRequest) -> GBrainRerankResponse:
    """GBrain-compatible /v1/rerank endpoint (llama-server-reranker recipe).

    Accepts GBrain's reranker requests and returns {results: [{index, relevance_score}]}.
    """
    reranker: Reranker = app.state.reranker
    scores = reranker.rerank(req.query, req.documents)
    paired = list(zip(range(len(req.documents)), scores))
    paired.sort(key=lambda x: x[1], reverse=True)
    top_n = min(req.top_n or len(req.documents), len(req.documents))
    top = paired[:top_n]
    return GBrainRerankResponse(
        results=[
            GBrainRerankItem(index=idx, relevance_score=float(s))
            for idx, s in top
        ]
    )


@app.get("/health")
async def health():
    return {"status": "ok"}
