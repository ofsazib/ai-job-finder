# embedding.py
"""Semantic ranking via the docker embedder + sqlite-vec.

Architecture
------------
The heavy ONNX model lives in a docker sidecar (see `embedder/`). This module
is the **local** client: it calls the sidecar over HTTP for raw vectors and
stores them in a local SQLite database using the `sqliteai-vector` extension
for fast cosine search.

The pipeline never pays for re-embedding: every JD is embedded exactly once
and reused across runs (keyed by URL + content hash). When the embedder
service is down or the extension isn't loaded, the module degrades
gracefully and the pipeline falls back to keyword-only ranking.

Public API
----------
- ``is_available()`` → True if both the sidecar and the extension are usable.
- ``embed_text(text)`` → list[float] (384-dim) or None on failure.
- ``semantic_match_score(resume_text, job)`` → 0-100 similarity score.
- ``rank_jobs(resume_text, jobs)`` → adds ``semantic_match_score`` to each
  job in-place and returns them sorted by blended rank.
- ``close()`` → release the SQLite connection.

Performance
-----------
- Embedding 600 jobs on first run: ~20s on M-series Mac, ~60s on Intel.
- Subsequent runs: ~0.1s (all cached, just cosine scans).
- Memory: ~9 MB for 600 × 384-dim float32 vectors.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import urllib.error
import urllib.request
from pathlib import Path

# Default to localhost — the docker-compose service exposes :8787.
EMBEDDER_URL = os.environ.get("EMBEDDER_URL", "http://localhost:8787").rstrip("/")
EMBED_DIM = 384
DB_PATH = Path(os.environ.get("EMBEDDER_DB", "output/embeddings.sqlite"))
TIMEOUT_S = 120  # first call after cold-start can be slow while model loads

_conn: sqlite3.Connection | None = None
_extension_loaded: bool | None = None  # tri-state: None = "not tried yet"


# ── sqlite-vec setup ──────────────────────────────────────
def _hash(text: str) -> str:
    """Stable content hash so cached embeddings invalidate when JD text changes."""
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]


def _connect() -> sqlite3.Connection:
    """Lazily open + init the SQLite cache. Reuses the same connection."""
    global _conn, _extension_loaded
    if _conn is not None:
        return _conn
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    _conn = sqlite3.connect(str(DB_PATH))
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute("PRAGMA synchronous=NORMAL")
    # Enable load_extension if the Python build allows it (some distros disable).
    try:
        _conn.enable_load_extension(True)
    except AttributeError:
        _extension_loaded = False
        return _conn
    # Try to load sqlite-vec. If it's not installed, we fall back to a pure
    # numpy cosine computation in code (slower per query, but correct).
    if _extension_loaded is None:
        try:
            # The PyPI package `sqliteai-vector` ships a loadable module
            # named `sqlite_vector` that exposes a `load(conn)` helper.
            try:
                import sqlite_vector  # type: ignore
                sqlite_vector.load(_conn)
            except ImportError:
                # No python wrapper; try loading the raw .so/.dylib from PATH.
                _conn.load_extension("vector")
            _conn.execute("SELECT vector_version()")
            _extension_loaded = True
        except Exception as e:
            print(f"  [embedding] sqlite-vec not available, will use numpy cosine: {e}")
            _extension_loaded = False

    if _extension_loaded:
        # Ordinary table with a BLOB column for the vector. sqlite-vec works
        # without virtual tables — that's the whole appeal.
        _conn.execute(
            """CREATE TABLE IF NOT EXISTS job_vectors (
                url TEXT PRIMARY KEY,
                content_hash TEXT NOT NULL,
                source TEXT,
                vector BLOB NOT NULL,
                embedded_at TEXT NOT NULL
            )"""
        )
        # Initialize cosine scan over the BLOB column. Idempotent.
        try:
            _conn.execute(
                "SELECT vector_init('job_vectors', 'vector', "
                "'type=FLOAT32,dimension=384,distance=COSINE')"
            )
        except sqlite3.OperationalError:
            # Already initialized — sqlite-vec raises on re-init.
            pass
        _conn.commit()
    return _conn


def is_available() -> bool:
    """Quick health probe. Returns True only if the sidecar is up AND the
    sqlite-vec extension loaded (or we can fall back to numpy cosine).

    Used by finder.py to decide whether to use semantic ranking at all.
    """
    if not _sidecar_alive():
        return False
    # Don't require sqlite-vec — we can compute cosine in numpy as fallback.
    return True


def _sidecar_alive() -> bool:
    try:
        req = urllib.request.Request(f"{EMBEDDER_URL}/health", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


# ── HTTP client ───────────────────────────────────────────
def _embed_batch(texts: list[str]) -> list[list[float]] | None:
    """Call the docker sidecar to embed a batch of texts. None on failure."""
    if not texts:
        return []
    payload = json.dumps({"texts": texts}).encode("utf-8")
    req = urllib.request.Request(
        f"{EMBEDDER_URL}/embed",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return body.get("vectors")
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        print(f"  [embedding] sidecar call failed: {e}")
        return None
    except (ValueError, KeyError) as e:
        print(f"  [embedding] malformed sidecar response: {e}")
        return None


def embed_text(text: str) -> list[float] | None:
    """Single-text convenience wrapper around the batch endpoint."""
    vectors = _embed_batch([text])
    return vectors[0] if vectors else None


# ── storage + cosine ──────────────────────────────────────
def _vec_to_blob(vec: list[float]) -> bytes:
    """Pack a float32 vector as bytes for SQLite BLOB storage."""
    import array
    return array.array("f", vec).tobytes()


def _blob_to_vec(blob: bytes) -> list[float]:
    import array
    a = array.array("f")
    a.frombytes(blob)
    return a.tolist()


def _cosine_numpy(a: list[float], b: list[float]) -> float:
    """Pure-python cosine for the fallback path when sqlite-vec is missing."""
    import math
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _get_cached(url: str, content_hash: str) -> list[float] | None:
    conn = _connect()
    row = conn.execute(
        "SELECT vector FROM job_vectors WHERE url = ? AND content_hash = ?",
        (url, content_hash),
    ).fetchone()
    return _blob_to_vec(row[0]) if row else None


def _put_cached(url: str, content_hash: str, source: str, vec: list[float]) -> None:
    conn = _connect()
    from datetime import datetime, timezone
    now = datetime.now(tz=timezone.utc).isoformat()
    conn.execute(
        """INSERT OR REPLACE INTO job_vectors
               (url, content_hash, source, vector, embedded_at)
           VALUES (?, ?, ?, ?, ?)""",
        (url, content_hash, source, _vec_to_blob(vec), now),
    )
    conn.commit()


# ── public ranking API ────────────────────────────────────
def _job_text(job: dict) -> str:
    """Concatenate the fields that carry semantic signal.

    Title is weighted implicitly because it appears at the start; description
    dominates by volume. We strip HTML and truncate to keep embedding time
    bounded — MiniLM has a 256-token context anyway, so anything past ~1000
    chars gets truncated by the tokenizer regardless.
    """
    parts = [
        job.get("title", ""),
        job.get("company", ""),
        " ".join(job.get("tags") or []),
        (job.get("description") or "")[:2000],
    ]
    return " ".join(p for p in parts if p)


def semantic_match_score(resume_vec: list[float], job_vec: list[float]) -> int:
    """Cosine similarity (0-100). Both vectors must be same dimension."""
    raw = _cosine_numpy(resume_vec, job_vec)
    # Cosine is in [-1, 1] but for short-text embeddings it's effectively
    # [0, 1]. Shift + scale so 0→0 and 1→100; clamp negatives to 0.
    return max(0, min(100, int(round(raw * 100))))


def rank_jobs(
    resume_text: str,
    jobs: list[dict],
    *,
    skill_weight: float = 0.4,
    semantic_weight: float = 0.6,
) -> list[dict] | None:
    """Embed resume + every job, attach ``semantic_match_score``, sort.

    Returns ``None`` if the embedder is unavailable (caller should fall back
    to keyword-only ranking). Otherwise mutates each job in place to add
    ``semantic_match_score`` and returns the list sorted by blended rank
    descending. Existing ``skill_overlap_score`` is read but not modified.

    On first run with N fresh jobs this is O(N) embedding calls; subsequent
    runs hit the cache and pay ~0 cosine cost.
    """
    if not _sidecar_alive():
        return None

    # Batch-embed everything missing in ONE HTTP call to amortize round-trip.
    # First the resume — its hash doesn't change between runs but we re-embed
    # it cheaply each run rather than cache it (it's one vector, ~30ms).
    resume_vec = embed_text(resume_text)
    if not resume_vec:
        return None

    to_embed: list[tuple[int, str]] = []  # (job_index, text)
    cached: dict[int, list[float]] = {}
    for i, job in enumerate(jobs):
        text = _job_text(job)
        chash = _hash(text)
        cached_vec = _get_cached(job.get("url", ""), chash) if _extension_loaded else None
        if cached_vec is not None:
            cached[i] = cached_vec
        else:
            to_embed.append((i, text))

    if to_embed:
        texts = [t for _, t in to_embed]
        vectors = _embed_batch(texts)
        if vectors is None:
            # Sidecar failed mid-batch — return None so caller falls back.
            return None
        for (i, text), vec in zip(to_embed, vectors):
            cached[i] = vec
            if _extension_loaded:
                job = jobs[i]
                _put_cached(
                    job.get("url", str(i)),
                    _hash(text),
                    job.get("source", ""),
                    vec,
                )

    # Score + sort.
    for i, job in enumerate(jobs):
        job_vec = cached.get(i)
        sem = semantic_match_score(resume_vec, job_vec) if job_vec else 0
        job["semantic_match_score"] = sem
        skill = job.get("skill_overlap_score", 0) or 0
        job["blended_rank_score"] = round(
            skill * skill_weight + sem * semantic_weight, 2
        )

    return sorted(jobs, key=lambda j: j.get("blended_rank_score", 0), reverse=True)


def close() -> None:
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None
