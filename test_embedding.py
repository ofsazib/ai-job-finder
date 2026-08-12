# embedding.py tests — sidecar + cache logic with HTTP mocked.
# We never start the docker service in tests; _sidecar_alive is patched.

import json
import os
from unittest.mock import patch

import embedding


def test_is_available_returns_false_when_sidecar_down(monkeypatch):
    monkeypatch.setattr(embedding, "_sidecar_alive", lambda: False)
    assert embedding.is_available() is False


def test_is_available_true_when_sidecar_up(monkeypatch):
    monkeypatch.setattr(embedding, "_sidecar_alive", lambda: True)
    assert embedding.is_available() is True


def test_hash_is_stable():
    h1 = embedding._hash("Python Engineer")
    h2 = embedding._hash("Python Engineer")
    assert h1 == h2
    assert h1 != embedding._hash("Python Developer")


def test_hash_changes_on_text_change():
    assert embedding._hash("aa") != embedding._hash("ab")


def test_cosine_numpy_basic():
    # Parallel vectors → 1.0
    a = [1.0, 0.0, 0.0]
    b = [1.0, 0.0, 0.0]
    assert embedding._cosine_numpy(a, b) == 1.0


def test_cosine_numpy_orthogonal_is_zero():
    a = [1.0, 0.0]
    b = [0.0, 1.0]
    assert embedding._cosine_numpy(a, b) == 0.0


def test_cosine_numpy_zero_vector_safe():
    # Zero vector should not divide by zero; returns 0.
    a = [0.0, 0.0]
    b = [1.0, 0.0]
    assert embedding._cosine_numpy(a, b) == 0.0


def test_semantic_match_score_scales_to_0_100():
    # cosine 1.0 → 100, 0.5 → 50, 0.0 → 0
    assert embedding.semantic_match_score([1.0, 0.0], [1.0, 0.0]) == 100
    assert embedding.semantic_match_score([1.0, 0.0], [0.0, 1.0]) == 0
    # cosine 0.707 (45deg) → 71
    score = embedding.semantic_match_score([1.0, 0.0], [0.7071, 0.7071])
    assert 70 <= score <= 72


def test_semantic_match_score_clamps_negative():
    # cosine -1 → clamped to 0
    assert embedding.semantic_match_score([1.0, 0.0], [-1.0, 0.0]) == 0


def test_vec_to_blob_roundtrip():
    vec = [0.1, 0.2, 0.3, 0.4]
    blob = embedding._vec_to_blob(vec)
    out = embedding._blob_to_vec(blob)
    assert len(out) == 4
    for a, b in zip(vec, out):
        assert abs(a - b) < 1e-6


def test_rank_jobs_returns_none_when_sidecar_down(monkeypatch):
    """When the sidecar is unreachable, rank_jobs returns None so the caller
    can fall back to keyword-only ranking without crashing."""
    monkeypatch.setattr(embedding, "_sidecar_alive", lambda: False)
    jobs = [{"url": "u1", "title": "Backend Engineer", "description": "Python"}]
    result = embedding.rank_jobs("resume text", jobs)
    assert result is None


def test_rank_jobs_assigns_semantic_scores(monkeypatch, tmp_path):
    """When the sidecar returns vectors, every job gets a semantic_match_score."""
    monkeypatch.setattr(embedding, "_sidecar_alive", lambda: True)
    monkeypatch.setattr(embedding, "DB_PATH", tmp_path / "emb.sqlite")
    monkeypatch.setattr(embedding, "_extension_loaded", False)  # disable vec ext
    # Reset connection so it reopens with the new DB_PATH.
    embedding._conn = None

    # Mock the HTTP batch-embed to return a 384-dim vector for each input.
    fake_vec = [0.1] * 384
    def fake_embed_batch(texts):
        return [list(fake_vec) for _ in texts]
    monkeypatch.setattr(embedding, "_embed_batch", fake_embed_batch)

    jobs = [
        {"url": "u1", "title": "Python Backend", "company": "A",
         "tags": ["python"], "description": "FastAPI microservices"},
        {"url": "u2", "title": "Frontend Dev", "company": "B",
         "tags": ["react"], "description": "React CSS work"},
    ]
    result = embedding.rank_jobs("resume", jobs)
    assert result is not None
    assert len(result) == 2
    assert "semantic_match_score" in result[0]
    assert "blended_rank_score" in result[0]
    # Since all vectors are identical, cosine = 1.0 → semantic = 100 for both.
    assert result[0]["semantic_match_score"] == 100


def test_rank_jobs_sorts_by_blended_score(monkeypatch, tmp_path):
    """Jobs with higher skill_overlap should rank higher when semantic is equal."""
    monkeypatch.setattr(embedding, "_sidecar_alive", lambda: True)
    monkeypatch.setattr(embedding, "DB_PATH", tmp_path / "emb2.sqlite")
    monkeypatch.setattr(embedding, "_extension_loaded", False)
    embedding._conn = None

    fake_vec = [0.5] * 384
    monkeypatch.setattr(embedding, "_embed_batch",
                        lambda texts: [list(fake_vec) for _ in texts])

    jobs = [
        {"url": "lo", "title": "A", "description": "x", "skill_overlap_score": 20},
        {"url": "hi", "title": "B", "description": "y", "skill_overlap_score": 90},
    ]
    result = embedding.rank_jobs("resume", jobs)
    assert result[0]["url"] == "hi"  # higher skill_overlap wins
