"""
FastAPI service that turns text into vector embeddings via fastembed.

Runs inside Docker so the host Python environment stays tiny — the main
ai-job-finder code calls this over HTTP and never imports fastembed itself.

Model: Qdrant/MiniLM-L6-v2-v3 (3824-dim? actually 384-dim, ONNX-encoded,
CPU-only). Loads once on startup; subsequent /embed calls reuse the model.

Endpoints:
  GET  /health         → {"model": "...", "dim": 384}
  POST /embed          → body {"texts": ["...", "..."]} → {"vectors": [[...], ...]}
  POST /embed_batch    → alias of /embed for clients that name it differently
"""
from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from typing import List

import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# Model is pinned: 384-dim cosine-friendly, ONNX-encoded, ~90MB on disk after
# first download. Same family used by Qdrant / Pinecone examples.
# Note: fastembed uses the sentence-transformers namespaced name, not the
# Qdrant alias — the latter is not in the supported models list.
MODEL_NAME = os.environ.get("EMBEDDER_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
# Where fastembed caches the ONNX weights. Mounted as a volume in compose
# so we don't re-download across container restarts.
CACHE_DIR = os.environ.get("EMBEDDER_CACHE_DIR", "/data/models")

_state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load the model once at startup. Fail fast if model can't load."""
    t0 = time.time()
    # Import lazily so the lifespan error is informative rather than an
    # import-time stack trace that gets hidden by uvicorn.
    from fastembed import TextEmbedding

    print(f"[embedder] loading model {MODEL_NAME!r} from {CACHE_DIR}...")
    try:
        model = TextEmbedding(model_name=MODEL_NAME, cache_dir=CACHE_DIR)
        # Warm up: embed a tiny string so the first real request doesn't
        # pay the ONNX session setup latency.
        warmup = next(model.embed(["warmup"]))
        dim = int(np.asarray(warmup).shape[0])
    except Exception as e:
        print(f"[embedder] FAILED to load model: {e}")
        raise

    _state["model"] = model
    _state["dim"] = dim
    print(f"[embedder] ready · dim={dim} · loaded in {time.time()-t0:.1f}s")
    yield
    _state.clear()


app = FastAPI(title="ai-job-finder embedder", lifespan=lifespan)


class EmbedRequest(BaseModel):
    texts: List[str]


class EmbedResponse(BaseModel):
    vectors: List[List[float]]
    dim: int
    model: str


@app.get("/health")
async def health():
    if "model" not in _state:
        raise HTTPException(status_code=503, detail="model not loaded")
    return {"model": MODEL_NAME, "dim": _state["dim"], "status": "ok"}


@app.post("/embed", response_model=EmbedResponse)
@app.post("/embed_batch", response_model=EmbedResponse, include_in_schema=False)
async def embed(req: EmbedRequest):
    if "model" not in _state:
        raise HTTPException(status_code=503, detail="model not loaded")
    if not req.texts:
        return EmbedResponse(vectors=[], dim=_state["dim"], model=MODEL_NAME)
    # fastembed's .embed() is a generator; materialize so we can serialize.
    # On CPU with MiniLM this is ~30ms per short text, ~80ms for a 2k-char JD.
    vectors = [np.asarray(v).tolist() for v in _state["model"].embed(req.texts)]
    return EmbedResponse(vectors=vectors, dim=_state["dim"], model=MODEL_NAME)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host=os.environ.get("EMBEDDER_HOST", "0.0.0.0"),
        port=int(os.environ.get("EMBEDDER_PORT", "8787")),
    )
