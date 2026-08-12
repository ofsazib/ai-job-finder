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


def test_enrich_repost_history_marks_second_url(tmp_path, monkeypatch):
    import finder
    monkeypatch.chdir(tmp_path)
    first = {"company": "Acme", "title": "Backend Engineer", "url": "https://a/1"}
    second = {**first, "url": "https://a/2"}

    initial = finder.enrich_repost_history(
        [first], now="2026-08-01T00:00:00+00:00"
    )[0]
    assert initial["is_repost"] is False
    assert initial["repost_count"] == 1

    repost = finder.enrich_repost_history(
        [second], now="2026-08-11T00:00:00+00:00"
    )[0]
    assert repost["is_repost"] is True
    assert repost["repost_count"] == 2
    assert repost["first_seen_at"] == "2026-08-01T00:00:00+00:00"


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


def test_analyze_manual_job_uses_shared_pipeline(tmp_path, monkeypatch):
    import finder
    monkeypatch.chdir(tmp_path)
    (tmp_path / "resume.md").write_text("Python")
    (tmp_path / "output").mkdir()
    (tmp_path / "output/search_profile.json").write_text(json.dumps({
        "must_have_skills": ["python"], "seniority": "senior"
    }))
    with patch.object(
        finder, "analyze_jobs", return_value=[{"url": "manual:test", "score": 80}]
    ) as analyze:
        result = finder.analyze_manual_job({
            "description": "Python API role", "company": "Acme"
        })
    assert result["score"] == 80
    assert analyze.call_args.args[0][0]["source"] == "manual"


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

    # Profile with real must-have skills so the skill-overlap score is exercised.
    # The mock job's description is "Python FastAPI" with tags ["python"], so
    # both python + fastapi must-haves hit → coverage = 100% → 70 pts baseline.
    profile = {"must_have_skills": ["python", "fastapi"], "nice_to_have_skills": [],
               "seniority": "senior"}

    # Model returns only scoring fields keyed by url — base fields must survive.
    model_out = [{
        "url": "https://acme.example/jobs/1", "title": "Senior Backend Engineer",
        "score": 91, "verdict": "apply", "match_reasons": ["a"],
        "red_flags": [], "suggested_angle": "x",
    }]
    with patch.object(finder, "run_json", return_value=model_out):
        merged = finder.analyze_jobs(jobs, profile=profile)

    # Final = 70% LLM (91) + 30% skill_overlap (80) = 63.7 + 24 = 87.7 → 88
    assert merged[0]["score"] == 88
    assert merged[0]["llm_score"] == 91
    assert merged[0]["skill_overlap_score"] == 80
    assert merged[0]["source"] == "remoteok"          # preserved from base record
    assert merged[0]["posted_date"] == "2026-07-19T00:00:00+00:00"
    assert merged[0]["location"] == "Remote"
    written = json.loads((tmp_path / "output" / "jobs.json").read_text())
    assert written == merged


def test_analyze_jobs_applies_saved_preferences(tmp_path, monkeypatch):
    import finder
    monkeypatch.chdir(tmp_path)
    (tmp_path / "resume.md").write_text("# Resume")
    (tmp_path / "output").mkdir()
    (tmp_path / "output/preferences.json").write_text(json.dumps({
        "work_modes": ["remote"],
    }))
    profile = {"must_have_skills": ["python", "fastapi"],
               "nice_to_have_skills": [], "seniority": "senior"}
    model_out = [{
        "url": "https://acme.example/jobs/1", "score": 91,
        "verdict": "apply", "match_reasons": [], "red_flags": [],
        "suggested_angle": "",
    }]
    jobs = _mock_raw_jobs()
    jobs[0]["work_mode"] = "remote"
    with patch.object(finder, "run_json", return_value=model_out):
        job = finder.analyze_jobs(jobs, profile=profile)[0]
    assert job["preference_adjustment"] == 2
    assert job["score"] == 90


def test_analyze_jobs_hard_rejects_clearance_jobs(tmp_path, monkeypatch):
    """A clearance-required job is dropped before LLM scoring (no tokens spent)."""
    import finder
    monkeypatch.chdir(tmp_path)
    (tmp_path / "resume.md").write_text("# Resume")
    profile = {"must_have_skills": ["python"], "nice_to_have_skills": [],
               "seniority": "senior"}
    jobs = [
        {**_mock_raw_jobs()[0], "url": "https://x/good",
         "description": "Python backend, fully remote"},
        {**_mock_raw_jobs()[0], "url": "https://x/bad",
         "title": "Backend Engineer",
         "description": "Active top secret security clearance required. Python shop."},
    ]

    # The good job would be scored; the bad one is hard-rejected in code and the
    # LLM is never called. We assert by checking run_json received only 1 job.
    def fake_run_json(prompt, context="", timeout=300):
        # The context body should only mention the good URL.
        assert "https://x/good" in context
        assert "https://x/bad" not in context
        return [{"url": "https://x/good", "title": "Backend Engineer",
                 "score": 80, "verdict": "apply", "match_reasons": [],
                 "red_flags": [], "suggested_angle": ""}]

    with patch.object(finder, "run_json", side_effect=fake_run_json):
        merged = finder.analyze_jobs(jobs, profile=profile)

    good = next(j for j in merged if j["url"] == "https://x/good")
    bad = next(j for j in merged if j["url"] == "https://x/bad")
    assert good["verdict"] == "apply"
    assert bad["score"] == 0
    assert bad["verdict"] == "skip"
    assert any("security_clearance" in f for f in bad["red_flags"])


def test_normalize_blocks_handles_full_payload():
    import finder
    raw = {
        "stack_fit":     {"score": 85, "notes": "python+django+fastapi all hit"},
        "seniority_fit": {"score": 90, "notes": "senior role, candidate is lead"},
        "location_fit":  {"score": 95},
        "compensation":  {"score": 50, "notes": "no salary published"},
        "culture_fit":   {"notes": "no signal"},  # missing score → 0
    }
    out = finder._normalize_blocks(raw)
    assert set(out.keys()) == {"stack_fit", "seniority_fit", "location_fit",
                               "compensation", "culture_fit"}
    assert out["stack_fit"]["score"] == 85
    assert out["location_fit"]["notes"] == ""
    assert out["culture_fit"]["score"] == 0


def test_normalize_blocks_clamps_and_drops_junk():
    import finder
    raw = {
        "stack_fit": {"score": 150, "notes": "over"},     # clamps to 100
        "seniority_fit": {"score": -10},                   # clamps to 0
        "location_fit": "not a dict",                      # dropped
        "compensation": {"score": "fifty"},                # coerced to 0
    }
    out = finder._normalize_blocks(raw)
    assert out["stack_fit"]["score"] == 100
    assert out["seniority_fit"]["score"] == 0
    assert "location_fit" not in out
    assert out["compensation"]["score"] == 0


def test_normalize_blocks_handles_none_and_lists():
    import finder
    assert finder._normalize_blocks(None) == {}
    assert finder._normalize_blocks([]) == {}
    assert finder._normalize_blocks("stack_fit:80") == {}


def test_analyze_jobs_carries_blocks_through_merge(tmp_path, monkeypatch):
    """When the LLM returns per-block scores, they survive into output/jobs.json."""
    import finder
    monkeypatch.chdir(tmp_path)
    (tmp_path / "resume.md").write_text("# Resume")
    profile = {"must_have_skills": ["python"], "nice_to_have_skills": [],
               "seniority": "senior"}
    jobs = _mock_raw_jobs()

    model_out = [{
        "url": "https://acme.example/jobs/1", "title": "Senior Backend Engineer",
        "score": 88, "verdict": "apply",
        "blocks": {
            "stack_fit":     {"score": 90, "notes": "all must-haves hit"},
            "seniority_fit": {"score": 95, "notes": "lead role, candidate is lead"},
            "location_fit":  {"score": 85, "notes": "remote worldwide"},
            "compensation":  {"score": 50, "notes": "no salary published"},
            "culture_fit":   {"score": 80, "notes": "e-commerce background matches"},
        },
        "match_reasons": [], "red_flags": [], "suggested_angle": "lead with scale",
    }]
    with patch.object(finder, "run_json", return_value=model_out):
        merged = finder.analyze_jobs(jobs, profile=profile)

    assert "blocks" in merged[0]
    assert merged[0]["blocks"]["stack_fit"]["score"] == 90
    assert merged[0]["blocks"]["compensation"]["notes"] == "no salary published"


def test_analyze_jobs_survives_when_llm_omits_blocks(tmp_path, monkeypatch):
    """Older / lazier LLM output without 'blocks' must not break the merge."""
    import finder
    monkeypatch.chdir(tmp_path)
    (tmp_path / "resume.md").write_text("# Resume")
    profile = {"must_have_skills": ["python"], "nice_to_have_skills": [],
               "seniority": "senior"}
    model_out = [{
        "url": "https://acme.example/jobs/1", "title": "Backend Engineer",
        "score": 75, "verdict": "review",
        "match_reasons": [], "red_flags": [], "suggested_angle": "",
    }]
    with patch.object(finder, "run_json", return_value=model_out):
        merged = finder.analyze_jobs(_mock_raw_jobs(), profile=profile)
    assert merged[0]["blocks"] == {}
    # Blend: 70% LLM (75) + 30% skill_overlap (80 — job has python) = 76.5 → 76
    assert merged[0]["score"] == 76


def test_analyze_jobs_keeps_job_missing_from_ai_output(tmp_path, monkeypatch):
    import finder
    monkeypatch.chdir(tmp_path)
    (tmp_path / "resume.md").write_text("# Resume")
    jobs = [
        {**_mock_raw_jobs()[0], "url": "https://x/returned"},
        {**_mock_raw_jobs()[0], "url": "https://x/missing", "title": "Python Engineer"},
    ]
    profile = {"must_have_skills": ["python"], "nice_to_have_skills": [],
               "seniority": "senior"}
    model_out = [{
        "url": "https://x/returned", "score": 80, "verdict": "apply",
        "match_reasons": [], "red_flags": [], "suggested_angle": "",
    }]
    with patch.object(finder, "run_json", return_value=model_out):
        merged = finder.analyze_jobs(jobs, profile=profile)
    missing = next(j for j in merged if j["url"] == "https://x/missing")
    assert missing["verdict"] == "review"
    assert "analysis_missing" in missing["red_flags"]
    assert missing["llm_score"] is None


def test_analyze_jobs_empty_ai_output_preserves_previous_file(tmp_path, monkeypatch):
    import finder
    monkeypatch.chdir(tmp_path)
    (tmp_path / "resume.md").write_text("# Resume")
    (tmp_path / "output").mkdir()
    previous = [{"url": "https://old", "score": 88}]
    jobs_path = tmp_path / "output/jobs.json"
    jobs_path.write_text(json.dumps(previous))
    profile = {"must_have_skills": ["python"], "nice_to_have_skills": [],
               "seniority": "senior"}
    with patch.object(finder, "run_json", return_value=[]):
        with pytest.raises(RuntimeError, match="returned no known jobs"):
            finder.analyze_jobs(_mock_raw_jobs(), profile=profile)
    assert json.loads(jobs_path.read_text()) == previous


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
         patch.object(finder.sources, "filter_recent", side_effect=lambda j, **k: j):
        out = finder.discover_jobs({
            "target_roles": ["Python Engineer"], "keywords": ["python"]
        })

    assert len(out) == 2                               # capped
    assert out[0]["posted_epoch"] >= out[1]["posted_epoch"]  # freshest first
