# finder.py
"""AI job finder — 4-step pipeline.

    resume.md
       │
       ▼
  1. Build search profile   AI CLI extracts target roles + keywords from the
       │                    resume (used to pre-filter feed jobs in code).
       ▼
  2. Discover (structured)  Fetch fresh, *dated* postings from job-board feeds
       │                    (sources.py), drop anything older than 30 days,
       │                    then keyword-prefilter to the candidate's field.
       ▼
  3. Analyze & score        AI CLI scores each surviving posting 0–100 against
       │                    the resume and gives a verdict.
       ▼
  4. Cover letters          For every "apply" verdict, the AI CLI drafts a
       │                    tailored cover letter.
       ▼
   output/jobs.json + output/cover_letters/*.md

The key difference from the old Firecrawl scraper: discovery yields real
per-posting dates, so freshness is enforced deterministically (step 2) instead
of being guessed by the model. The AI CLI is only spent on ranking + writing,
where judgement actually helps.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

import sources
from ai_cli import active_backend, run_json, run_text

load_dotenv()

# ── config ────────────────────────────────────────────────
RESUME_FILE = "resume.md"
OUTPUT_DIR = Path("output")
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"  # ship with the code
THRESHOLD = 70                       # score for the UI "above threshold" count
MAX_DAYS = int(os.environ.get("MAX_JOB_AGE_DAYS", "30"))
MAX_JOBS_TO_ANALYZE = int(os.environ.get("MAX_JOBS_TO_ANALYZE", "40"))


def _resume_text() -> str:
    return Path(RESUME_FILE).read_text(encoding="utf-8")


def _selected_sources() -> list[str] | None:
    """Sources from $SOURCES (comma-separated), or the module default."""
    raw = os.environ.get("SOURCES", "").strip()
    if not raw:
        return None
    return [s.strip() for s in raw.split(",") if s.strip()]


# ── step 1: build search profile from resume ──────────────
def build_search_profile() -> dict:
    """Ask the AI CLI to distill the resume into roles + match keywords.

    Keywords drive the deterministic pre-filter in step 2, so the model never
    needs to see jobs the candidate clearly can't do.
    """
    prompt = (PROMPTS_DIR / "build_profile.md").read_text(encoding="utf-8")
    profile = run_json(prompt, context="---RESUME---\n" + _resume_text())

    roles = profile.get("target_roles") or []
    keywords = [k.lower() for k in (profile.get("keywords") or []) if k]
    profile["keywords"] = keywords

    OUTPUT_DIR.mkdir(exist_ok=True)
    (OUTPUT_DIR / "search_profile.json").write_text(json.dumps(profile, indent=2))
    print(f"  Roles: {roles}")
    print(f"  Keywords ({len(keywords)}): {', '.join(keywords[:12])}")
    return profile


# ── step 2: discover fresh, dated postings ────────────────
def discover_jobs(profile: dict) -> list[dict]:
    """Fetch feed jobs, enforce the freshness cutoff, keyword-prefilter."""
    raw = sources.fetch_all(_selected_sources())
    print(f"  Fetched {len(raw)} postings from feeds")

    fresh = sources.filter_recent(raw, max_age_days=MAX_DAYS)
    print(f"  {len(fresh)} within the last {MAX_DAYS} days")

    keywords = profile.get("keywords") or []
    matched = sources.filter_keywords(fresh, keywords)
    print(f"  {len(matched)} match the candidate's keywords")

    # Interleave sources round-robin (freshest first within each) before
    # capping, so one feed's same-day flood can't crowd every other source out
    # of the analysis budget.
    ranked = _interleave_by_source(matched)
    if len(ranked) > MAX_JOBS_TO_ANALYZE:
        print(f"  Capping to {MAX_JOBS_TO_ANALYZE} for analysis (round-robin across sources)")
        ranked = ranked[:MAX_JOBS_TO_ANALYZE]
    return ranked


def _interleave_by_source(jobs: list) -> list:
    """Round-robin jobs across their `source`, freshest first within each.

    Takes the freshest from source A, then B, then C, then the 2nd-freshest
    from A, and so on. Keeps the analysis cap balanced across feeds instead of
    letting whichever source posted most today dominate."""
    from collections import defaultdict
    from itertools import zip_longest

    buckets = defaultdict(list)
    for job in jobs:
        buckets[job.get("source", "")].append(job)
    for bucket in buckets.values():
        bucket.sort(key=lambda j: j.get("posted_epoch", 0), reverse=True)
    return [j for group in zip_longest(*buckets.values()) for j in group if j is not None]


# ── step 3: analyze & score via AI CLI ────────────────────
def analyze_jobs(jobs: list[dict]) -> list[dict]:
    """Score each posting 0–100 against the resume; write output/jobs.json.

    Jobs are passed inline as JSON context (not via a file tool) so this works
    identically across every CLI backend. The model returns scoring metadata
    keyed by url; we merge it back onto the full job records so the UI keeps
    company/location/date/source even if the model omits them.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    prompt = (PROMPTS_DIR / "analyze.md").read_text(encoding="utf-8")

    # Give the model only what it needs to judge — keep the payload small.
    slim = [
        {
            "url": j["url"],
            "title": j["title"],
            "company": j["company"],
            "location": j["location"],
            "posted_date": j["posted_date"],
            "source": j["source"],
            "description": j["description"],
        }
        for j in jobs
    ]
    context = (
        f"Today's date is {today}.\n"
        "---RESUME---\n" + _resume_text() + "\n"
        "---JOBS (JSON)---\n" + json.dumps(slim, indent=2)
    )
    scored = run_json(prompt, context=context, timeout=600)
    if not isinstance(scored, list):
        raise RuntimeError("Analyzer did not return a JSON array of jobs.")

    by_url = {j["url"]: j for j in jobs}
    merged: list[dict] = []
    for entry in scored:
        if not isinstance(entry, dict):
            continue
        url = (entry.get("url") or "").strip()
        base = by_url.get(url, {})
        merged.append({
            "title": entry.get("title") or base.get("title", ""),
            "company": entry.get("company") or base.get("company", ""),
            "location": base.get("location", "Remote"),
            "url": url,
            "posted_date": base.get("posted_date", ""),
            "source": base.get("source", ""),
            "score": entry.get("score", 0),
            "verdict": entry.get("verdict", "review"),
            "match_reasons": entry.get("match_reasons", []),
            "red_flags": entry.get("red_flags", []),
            "suggested_angle": entry.get("suggested_angle", ""),
        })

    merged.sort(key=lambda j: j.get("score", 0), reverse=True)
    OUTPUT_DIR.mkdir(exist_ok=True)
    (OUTPUT_DIR / "jobs.json").write_text(json.dumps(merged, indent=2))
    return merged


# ── step 4: cover letters ─────────────────────────────────
def _slug(text: str, max_len: int = 40) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:max_len]


def generate_cover_letters(jobs: list[dict]) -> None:
    out_dir = OUTPUT_DIR / "cover_letters"
    out_dir.mkdir(parents=True, exist_ok=True)
    prompt = (PROMPTS_DIR / "cover_letter.md").read_text(encoding="utf-8")
    resume = _resume_text()

    for job in jobs:
        company = job.get("company") or "unknown"
        title = job.get("title") or "role"
        out_path = out_dir / f"{_slug(company)}__{_slug(title)}.md"
        print(f"  Cover letter: {company} — {title[:50]}")
        try:
            letter = run_text(
                prompt,
                context="---RESUME---\n" + resume + "\n---JOB---\n" + json.dumps(job, indent=2),
            )
            out_path.write_text(letter, encoding="utf-8")
        except Exception as e:  # noqa: BLE001 — one bad letter shouldn't kill the batch
            print(f"  Failed for {out_path.name}: {e}")


# ── orchestrator ──────────────────────────────────────────
def run_pipeline(on_progress=None) -> dict:
    """Run the full 4-step pipeline.

    Calls on_progress(step, label, status) where step is 1-4, status is
    "running" or "done". Returns {"total", "above_threshold"}.
    """
    def emit(step, label, status):
        if on_progress:
            on_progress(step, label, status)

    OUTPUT_DIR.mkdir(exist_ok=True)
    if not Path(RESUME_FILE).exists():
        raise RuntimeError(f"Missing {RESUME_FILE} — add your resume before running.")

    print(f"Using AI backend: {active_backend()}")

    emit(1, "Building search profile", "running")
    profile = build_search_profile()
    emit(1, "Building search profile", "done")

    emit(2, "Discovering fresh jobs", "running")
    jobs = discover_jobs(profile)
    (OUTPUT_DIR / "raw_jobs.json").write_text(json.dumps(jobs, indent=2))
    if not jobs:
        raise RuntimeError(
            "No fresh matching jobs found. Try widening keywords, adding sources "
            "(SOURCES env), or raising MAX_JOB_AGE_DAYS."
        )
    emit(2, "Discovering fresh jobs", "done")

    emit(3, "Analyzing & scoring", "running")
    all_jobs = analyze_jobs(jobs)
    emit(3, "Analyzing & scoring", "done")

    emit(4, "Generating cover letters", "running")
    good = [j for j in all_jobs if j.get("score", 0) >= THRESHOLD]
    apply_jobs = [j for j in good if j.get("verdict") == "apply"]
    if apply_jobs:
        generate_cover_letters(apply_jobs)
    emit(4, "Generating cover letters", "done")

    return {"total": len(all_jobs), "above_threshold": len(good)}


def run() -> None:
    def print_progress(step, label, status):
        if status == "running":
            print(f"\nStep {step}: {label}...")

    try:
        result = run_pipeline(on_progress=print_progress)
        print(
            f"\nDone! {result['above_threshold']} of {result['total']} "
            f"jobs above threshold ({THRESHOLD})."
        )
    except RuntimeError as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    run()
