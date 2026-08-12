import importlib
import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "ui").mkdir()
    (tmp_path / "ui" / "index.html").write_text("<html><body>UI</body></html>")
    (tmp_path / "output").mkdir()
    import server
    importlib.reload(server)
    return TestClient(server.app)


def test_index_returns_html(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "UI" in res.text


def test_get_jobs_empty_when_no_file(client):
    res = client.get("/api/jobs")
    assert res.status_code == 200
    assert res.json() == []


def test_get_jobs_merges_status(client, tmp_path):
    jobs = [{"title": "Dev", "company": "Co", "url": "https://example.com",
             "score": 85, "verdict": "apply"}]
    (tmp_path / "output" / "jobs.json").write_text(json.dumps(jobs))
    (tmp_path / "output" / "status.json").write_text(
        json.dumps({"https://example.com": "applied"})
    )
    assert client.get("/api/jobs").json()[0]["status"] == "applied"


def test_get_jobs_status_defaults_to_none(client, tmp_path):
    jobs = [{"title": "Dev", "url": "https://example.com", "score": 85}]
    (tmp_path / "output" / "jobs.json").write_text(json.dumps(jobs))
    assert client.get("/api/jobs").json()[0]["status"] == "none"


def test_post_status_saves_and_clears(client, tmp_path):
    r = client.post("/api/status", json={"url": "https://x.com", "status": "applied"})
    assert r.status_code == 200
    entry = json.loads(
        (tmp_path / "output" / "status.json").read_text()
    )["https://x.com"]
    assert entry["status"] == "applied"
    assert entry["applied_at"]
    assert entry["updated_at"]
    assert entry["stages"] == []
    assert entry["outcome"] == ""

    client.post("/api/status", json={"url": "https://x.com", "status": "none"})
    assert json.loads((tmp_path / "output" / "status.json").read_text()) == {}


def test_post_status_rejects_invalid(client):
    r = client.post("/api/status", json={"url": "https://x.com", "status": "bogus"})
    assert r.status_code == 400


def test_cover_letter_404_when_missing(client):
    r = client.get("/api/cover-letter", params={"company": "Acme", "title": "Dev"})
    assert r.status_code == 404


def test_cover_letter_returns_content(client, tmp_path):
    from finder import _slug
    cl_dir = tmp_path / "output" / "cover_letters"
    cl_dir.mkdir(parents=True)
    slug = f"{_slug('Acme')}__{_slug('Backend Engineer')}"
    (cl_dir / f"{slug}.md").write_text("Dear team, I am a great fit.")

    r = client.get("/api/cover-letter", params={"company": "Acme", "title": "Backend Engineer"})
    assert r.status_code == 200
    assert "great fit" in r.json()["content"]


def test_preferences_default_and_round_trip(client):
    defaults = client.get("/api/preferences")
    assert defaults.status_code == 200
    assert defaults.json()["employment_types"] == []

    saved = client.put("/api/preferences", json={
        "employment_types": ["full-time"],
        "work_modes": ["remote"],
        "preferred_domains": ["healthtech"],
        "avoided_terms": ["unpaid"],
        "minimum_salary": 60000,
        "salary_currency": "USD",
        "allow_on_call": False,
        "willing_to_relocate": False,
    })
    assert saved.status_code == 200
    assert client.get("/api/preferences").json()["minimum_salary"] == 60000


def test_manual_job_rejects_empty_description(client):
    assert client.post("/api/jobs/manual", json={"description": " "}).status_code == 422


def test_discovery_report_empty_then_loaded(client, tmp_path):
    assert client.get("/api/discovery-report").json() == {"sources": {}, "totals": {}}
    report = {"sources": {"remoteok": {"fetched": 3}}, "totals": {"fetched": 3}}
    (tmp_path / "output/discovery_report.json").write_text(json.dumps(report))
    assert client.get("/api/discovery-report").json() == report
