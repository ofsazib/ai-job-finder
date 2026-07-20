import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest


# ── ai_cli JSON extraction ────────────────────────────────
def test_extract_json_plain_array():
    import ai_cli
    assert ai_cli._extract_json('[{"a": 1}]') == [{"a": 1}]


def test_extract_json_from_fenced_block():
    import ai_cli
    raw = '```json\n{"score": 90}\n```'
    assert ai_cli._extract_json(raw) == {"score": 90}


def test_extract_json_with_surrounding_prose():
    import ai_cli
    raw = 'Here is the result you asked for:\n[{"url": "x"}]\nHope that helps!'
    assert ai_cli._extract_json(raw) == [{"url": "x"}]


def test_extract_json_ignores_braces_inside_strings():
    import ai_cli
    raw = '{"note": "use { and } carefully", "n": 2}'
    assert ai_cli._extract_json(raw) == {"note": "use { and } carefully", "n": 2}


def test_extract_json_returns_none_when_absent():
    import ai_cli
    assert ai_cli._extract_json("no json at all here") is None


def test_active_backend_defaults_to_claude(monkeypatch):
    import ai_cli
    monkeypatch.delenv("AI_CLI", raising=False)
    assert ai_cli.active_backend() == "claude"


def test_active_backend_rejects_unknown(monkeypatch):
    import ai_cli
    monkeypatch.setenv("AI_CLI", "bogus")
    with pytest.raises(RuntimeError, match="Unknown AI_CLI"):
        ai_cli.active_backend()


def test_backends_pass_prompt_as_single_argv_element():
    import ai_cli
    prompt = 'weird "prompt" with $shell `chars`'
    for build in ai_cli.BACKENDS.values():
        argv = build(prompt)
        assert prompt in argv  # never string-interpolated / shell-escaped


# ── pipeline orchestration (AI + feeds mocked) ────────────
def _mock_raw_jobs():
    return [{
        "title": "Senior Backend Engineer", "company": "Acme", "location": "Remote",
        "url": "https://acme.example/jobs/1", "description": "Python FastAPI",
        "posted_date": "2026-07-19T00:00:00+00:00", "posted_epoch": 1784448000,
        "source": "remoteok", "tags": ["python"],
    }]


def test_run_pipeline_emits_all_steps(tmp_path, monkeypatch):
    import finder
    monkeypatch.chdir(tmp_path)
    (tmp_path / "resume.md").write_text("# Resume\nPython, FastAPI")
    monkeypatch.setenv("AI_CLI", "claude")

    profile = {"target_roles": ["Backend"], "keywords": ["python"]}
    analyzed = [{
        "title": "Senior Backend Engineer", "company": "Acme",
        "url": "https://acme.example/jobs/1", "score": 88, "verdict": "apply",
        "match_reasons": ["stack match"], "red_flags": [], "suggested_angle": "lead with scale",
    }]

    events = []
    with patch.object(finder, "build_search_profile", return_value=profile), \
         patch.object(finder, "discover_jobs", return_value=_mock_raw_jobs()), \
         patch.object(finder, "analyze_jobs", return_value=analyzed), \
         patch.object(finder, "generate_cover_letters") as gen:
        result = finder.run_pipeline(on_progress=lambda s, l, st: events.append((s, st)))

    for step in (1, 2, 3, 4):
        assert (step, "running") in events
        assert (step, "done") in events
    assert result == {"total": 1, "above_threshold": 1}
    gen.assert_called_once()  # one "apply" job → cover letter generated


def test_run_pipeline_raises_when_resume_missing(tmp_path, monkeypatch):
    import finder
    monkeypatch.chdir(tmp_path)
    with pytest.raises(RuntimeError, match="Missing resume.md"):
        finder.run_pipeline()


def test_run_pipeline_raises_when_no_fresh_jobs(tmp_path, monkeypatch):
    import finder
    monkeypatch.chdir(tmp_path)
    (tmp_path / "resume.md").write_text("# Resume")
    profile = {"target_roles": [], "keywords": ["python"]}
    with patch.object(finder, "build_search_profile", return_value=profile), \
         patch.object(finder, "discover_jobs", return_value=[]):
        with pytest.raises(RuntimeError, match="No fresh matching jobs"):
            finder.run_pipeline()


def test_analyze_jobs_merges_scores_onto_full_records(tmp_path, monkeypatch):
    import finder
    monkeypatch.chdir(tmp_path)
    (tmp_path / "resume.md").write_text("# Resume")
    jobs = _mock_raw_jobs()

    # Model returns only scoring fields keyed by url — base fields must survive.
    model_out = [{
        "url": "https://acme.example/jobs/1", "title": "Senior Backend Engineer",
        "score": 91, "verdict": "apply", "match_reasons": ["a"],
        "red_flags": [], "suggested_angle": "x",
    }]
    with patch.object(finder, "run_json", return_value=model_out):
        merged = finder.analyze_jobs(jobs)

    assert merged[0]["score"] == 91
    assert merged[0]["source"] == "remoteok"          # preserved from base record
    assert merged[0]["posted_date"] == "2026-07-19T00:00:00+00:00"
    assert merged[0]["location"] == "Remote"
    written = json.loads((tmp_path / "output" / "jobs.json").read_text())
    assert written == merged


def test_discover_jobs_filters_and_caps(tmp_path, monkeypatch):
    import finder
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MAX_JOBS_TO_ANALYZE", "2")
    # reload so the env-driven module constant is re-read
    import importlib
    importlib.reload(finder)

    raw = [
        {**_mock_raw_jobs()[0], "url": f"https://x/{i}", "posted_epoch": 1784448000 + i,
         "title": "Python Engineer", "description": "python"}
        for i in range(5)
    ]
    with patch.object(finder.sources, "fetch_all", return_value=raw), \
         patch.object(finder.sources, "filter_recent", side_effect=lambda j, **k: j), \
         patch.object(finder.sources, "filter_keywords", side_effect=lambda j, k: j):
        out = finder.discover_jobs({"keywords": ["python"]})

    assert len(out) == 2                               # capped
    assert out[0]["posted_epoch"] >= out[1]["posted_epoch"]  # freshest first
