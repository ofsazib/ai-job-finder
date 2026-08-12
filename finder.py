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
import hashlib
import os
import re
from datetime import datetime
from pathlib import Path

from resume import ResumeError, resolve_resume

from dotenv import load_dotenv

import sources
from ai_cli import active_backend, run_json, run_text
from matching import (
    detect_ghost_job_signals,
    disqualifier_hits,
    evaluate_preferences,
    hard_reject,
    role_relevance,
    skill_overlap_score,
)

load_dotenv()

# ── config ────────────────────────────────────────────────
OUTPUT_DIR = Path("output")
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"  # ship with the code
THRESHOLD = 70                       # score for the UI "above threshold" count
MAX_DAYS = int(os.environ.get("MAX_JOB_AGE_DAYS", "30"))
MAX_JOBS_TO_ANALYZE = int(os.environ.get("MAX_JOBS_TO_ANALYZE", "40"))
DISCOVERY_STAGES: dict[str, dict[str, int]] = {}


def _resume_text() -> str:
    return resolve_resume().read_text(encoding="utf-8")


def _selected_sources() -> list[str] | None:
    """Sources from $SOURCES (comma-separated), or the module default."""
    raw = os.environ.get("SOURCES", "").strip()
    if not raw:
        return None
    return [s.strip() for s in raw.split(",") if s.strip()]


# ── step 1: build search profile from resume ──────────────
def build_search_profile() -> dict:
    """Ask the AI CLI to distill the resume into a structured search profile.

    The profile drives three things downstream:
      - keywords → deterministic pre-filter in step 2 (sources.filter_keywords)
      - must_have_skills / nice_to_have_skills → skill_overlap_score in step 3
      - seniority → hard_reject pass in step 3 (junior-only roles for seniors)

    The model never sees jobs the candidate clearly can't do.
    """
    prompt = (PROMPTS_DIR / "build_profile.md").read_text(encoding="utf-8")
    profile = run_json(prompt, context="---RESUME---\n" + _resume_text())

    roles = profile.get("target_roles") or []
    keywords = [k.lower() for k in (profile.get("keywords") or []) if k]
    profile["keywords"] = keywords
    # Normalize new skill fields so matching.py never has to defend against None.
    profile["must_have_skills"] = [
        s.lower() for s in (profile.get("must_have_skills") or []) if s
    ]
    profile["nice_to_have_skills"] = [
        s.lower() for s in (profile.get("nice_to_have_skills") or []) if s
    ]
    profile["seniority"] = (profile.get("seniority") or "senior").lower()
    # Normalize languages: keep as dict if model returned one, else list.
    langs = profile.get("languages") or {}
    if isinstance(langs, list):
        # Convert ["english","bengali"] → {"english":"fluent","bengali":"native"}
        # by assuming the candidate is at least fluent in anything they list.
        langs = {l.lower(): "fluent" for l in langs if isinstance(l, str)}
    elif isinstance(langs, dict):
        langs = {k.lower(): v for k, v in langs.items() if isinstance(k, str)}
    else:
        langs = {}
    # Defensive fallback: most software roles require English, and the resume
    # is in English, so include it if the model forgot.
    if not langs:
        langs = {"english": "fluent"}
    profile["languages"] = langs

    OUTPUT_DIR.mkdir(exist_ok=True)
    (OUTPUT_DIR / "search_profile.json").write_text(json.dumps(profile, indent=2))
    print(f"  Roles: {roles}")
    print(f"  Keywords ({len(keywords)}): {', '.join(keywords[:12])}")
    print(f"  Must-have skills ({len(profile['must_have_skills'])}): "
          f"{', '.join(profile['must_have_skills'][:8])}")
    print(f"  Seniority: {profile['seniority']}")
    print(f"  Languages: {profile['languages']}")
    return profile


# ── step 2: discover fresh, dated postings ────────────────
def discover_jobs(profile: dict) -> list[dict]:
    """Fetch feed jobs, enforce the freshness cutoff, keyword-prefilter."""
    raw = sources.fetch_all(_selected_sources())
    DISCOVERY_STAGES.clear()
    for source, diagnostic in sources.SOURCE_DIAGNOSTICS.items():
        DISCOVERY_STAGES[source] = {"fetched": diagnostic.get("fetched", 0)}
    print(f"  Fetched {len(raw)} postings from feeds")

    fresh = sources.filter_recent(raw, max_age_days=MAX_DAYS)
    for job in fresh:
        DISCOVERY_STAGES.setdefault(job.get("source", "unknown"), {}).setdefault("fresh", 0)
        DISCOVERY_STAGES[job.get("source", "unknown")]["fresh"] += 1
    print(f"  {len(fresh)} within the last {MAX_DAYS} days")

    matched = []
    for job in fresh:
        relevance = role_relevance(job, profile)
        job["role_relevance_score"] = relevance["score"]
        job["role_relevance_reasons"] = relevance["reasons"]
        if relevance["relevant"]:
            matched.append(job)
            stage = DISCOVERY_STAGES.setdefault(job.get("source", "unknown"), {})
            stage["role_relevant"] = stage.get("role_relevant", 0) + 1
    print(f"  {len(matched)} match the candidate's target roles")

    # Interleave sources round-robin (freshest first within each) before
    # capping, so one feed's same-day flood can't crowd every other source out
    # of the analysis budget.
    ranked = _interleave_by_source(enrich_repost_history(matched))
    if len(ranked) > MAX_JOBS_TO_ANALYZE:
        print(f"  Capping to {MAX_JOBS_TO_ANALYZE} for analysis (round-robin across sources)")
        ranked = ranked[:MAX_JOBS_TO_ANALYZE]
    return ranked


def enrich_repost_history(jobs: list[dict], now: str | None = None) -> list[dict]:
    """Attach cross-run observation metadata without hiding probable reposts."""
    path = OUTPUT_DIR / "job_history.json"
    history = json.loads(path.read_text()) if path.exists() else {}
    seen_at = now or datetime.now().astimezone().isoformat()

    for job in jobs:
        fingerprint = sources.job_fingerprint(job)
        url = job.get("url", "")
        entry = history.setdefault(fingerprint, {
            "first_seen_at": seen_at,
            "last_seen_at": seen_at,
            "urls": [],
        })
        if url and url not in entry["urls"]:
            entry["urls"].append(url)
        entry["last_seen_at"] = seen_at
        entry["observation_count"] = len(entry["urls"])
        job.update({
            "fingerprint": fingerprint,
            "is_repost": len(entry["urls"]) > 1,
            "repost_count": len(entry["urls"]),
            "first_seen_at": entry["first_seen_at"],
        })

    OUTPUT_DIR.mkdir(exist_ok=True)
    path.write_text(json.dumps(history, indent=2), encoding="utf-8")
    return jobs


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


def analyze_manual_job(data: dict) -> dict:
    """Normalize one pasted posting and send it through the regular analyzer."""
    description = (data.get("description") or "").strip()
    manual_url = (data.get("url") or "").strip() or (
        "manual:" + hashlib.sha256(description.encode()).hexdigest()[:16]
    )
    job = sources._job(
        data.get("title") or "Unknown role",
        data.get("company") or "Unknown company",
        data.get("location") or "Remote",
        manual_url,
        description,
        datetime.now().astimezone(),
        "manual",
        [],
    )
    enrich_repost_history([job])
    profile_path = OUTPUT_DIR / "search_profile.json"
    profile = json.loads(profile_path.read_text()) if profile_path.exists() else {}
    previous_path = OUTPUT_DIR / "jobs.json"
    previous = json.loads(previous_path.read_text()) if previous_path.exists() else []
    result = analyze_jobs([job], profile=profile)[0]
    merged = {j.get("url", ""): j for j in previous}
    merged[result.get("url", manual_url)] = result
    previous_path.write_text(json.dumps(
        sorted(merged.values(), key=lambda j: j.get("score", 0), reverse=True), indent=2
    ), encoding="utf-8")
    return result


# ── step 3: analyze & score via AI CLI ────────────────────
# The five evaluation dimensions exposed in the dashboard. Order matters —
# the UI renders them in this sequence.
BLOCK_DIMENSIONS = ("stack_fit", "seniority_fit", "location_fit",
                    "compensation", "culture_fit")


def _normalize_blocks(raw) -> dict:
    """Coerce LLM output into a uniform blocks dict.

    The analyzer may omit ``blocks`` (older prompt) or return partial data.
    Missing dimensions default to score 0 + empty note; the UI hides empty
    blocks. Malformed entries are dropped rather than crashing the merge.
    """
    out: dict = {}
    if not isinstance(raw, dict):
        return out
    for dim in BLOCK_DIMENSIONS:
        entry = raw.get(dim)
        if not isinstance(entry, dict):
            continue
        try:
            score = int(entry.get("score", 0) or 0)
        except (TypeError, ValueError):
            score = 0
        score = max(0, min(100, score))
        note = str(entry.get("notes") or entry.get("note") or "").strip()
        out[dim] = {"score": score, "notes": note}
    return out


def _hard_reject_pass(
    jobs: list[dict], profile: dict, preferences: dict | None = None
) -> tuple[list[dict], list[dict]]:
    """Drop jobs that are unworkable regardless of stack match.

    Hard rejects: clearance/citizenship/export-control requirements,
    region-locked onsite with no relocation, region-restricted remote roles
    (e.g. "US only" for a Bangladesh-based candidate), required languages the
    candidate doesn't speak, and junior/intern-only roles for a senior
    candidate. Each rejected job is recorded with a synthetic score of 0 so
    the dashboard can still surface what was filtered and why.

    Ghost-job signals (staffing agency, off-platform apply, etc.) are SOFT —
    attached to kept jobs so the LLM can weigh them in scoring, but never
    used to hard-reject (one false positive would cost a real opportunity).

    Returns ``(kept, rejected)``.
    """
    seniority = (profile.get("seniority") or "senior").lower()
    regions = profile.get("region_eligibility") or ["bangladesh", "worldwide"]
    languages = profile.get("languages") or {"english": "fluent"}
    kept, rejected = [], []
    for job in jobs:
        bad, hits = hard_reject(
            job, seniority,
            candidate_regions=regions,
            candidate_languages=languages,
        )
        preference = evaluate_preferences(job, preferences)
        if preference["hard_rejects"]:
            bad = True
            hits = [*hits, *preference["hard_rejects"]]
        job["preference_adjustment"] = preference["adjustment"]
        job["preference_reasons"] = preference["reasons"]
        if bad:
            job.update({
                "score": 0,
                "verdict": "skip",
                "match_reasons": [],
                "red_flags": [f"hard-rejected: {h}" for h in hits],
                "suggested_angle": "",
                "skill_overlap_score": 0,
                "disqualifier_hits": hits,
                "ghost_job_signals": [],
                "blocks": {},
            })
            rejected.append(job)
        else:
            # Attach code-computed signals so the analyzer prompt + the merged
            # output both carry them. Soft hits + ghost signals are kept here
            # so the LLM can weigh them in scoring.
            job["skill_overlap_score"] = skill_overlap_score(
                job,
                profile.get("must_have_skills") or [],
                profile.get("nice_to_have_skills") or [],
            )
            job["disqualifier_hits"] = disqualifier_hits(job)
            job["ghost_job_signals"] = detect_ghost_job_signals(job)
            kept.append(job)
    return kept, rejected


def analyze_jobs(jobs: list[dict], profile: dict | None = None) -> list[dict]:
    """Score each posting 0-100 against the resume; write output/jobs.json.

    Pipeline within this step:
      a. Hard-reject pass (code) drops clearance/citizenship/junior-only.
      b. Each surviving job gets a deterministic skill_overlap_score and any
         soft disqualifier_hits attached in code — these go into the prompt as
         ground truth so the LLM cannot hallucinate stack fit.
      c. The LLM scores 0-100 with its own judgement layered on top.
      d. Final score blends the two: 70% LLM (judgement on seniority, location,
         domain) + 30% skill_overlap (ground-truth stack coverage). A job the
         LLM rates 85 but with skill_overlap 30 ends up at ~68 — protecting
         against the model's tendency to be swayed by brand names.

    Jobs are passed inline as JSON context (not via a file tool) so this works
    identically across every CLI backend. The model returns scoring metadata
    keyed by url; we merge it back onto the full job records so the UI keeps
    company/location/date/source even if the model omits them.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    prompt = (PROMPTS_DIR / "analyze.md").read_text(encoding="utf-8")
    profile = profile or {}

    preferences_path = OUTPUT_DIR / "preferences.json"
    preferences = json.loads(preferences_path.read_text()) if preferences_path.exists() else {}
    kept, rejected = _hard_reject_pass(jobs, profile, preferences)
    if rejected:
        print(f"  Hard-rejected {len(rejected)} job(s) in code (disqualifiers)")
    if not kept:
        print("  No jobs survived the hard-reject pass")
        OUTPUT_DIR.mkdir(exist_ok=True)
        (OUTPUT_DIR / "jobs.json").write_text(json.dumps(rejected, indent=2))
        return rejected

    # Give the model only what it needs to judge — keep the payload small, but
    # include the derived categorization AND the code-computed skill_overlap /
    # disqualifier_hits so its scoring is anchored to ground truth.
    slim = [
        {
            "url": j["url"],
            "title": j["title"],
            "company": j["company"],
            "location": j["location"],
            "posted_date": j["posted_date"],
            "source": j["source"],
            "work_mode": j.get("work_mode", "unknown"),
            "locale": j.get("locale", "unknown"),
            "relocation": j.get("relocation", "unknown"),
            "employment_type": j.get("employment_type", "unknown"),
            "seniority": j.get("seniority", "unknown"),
            "salary": j.get("salary", ""),
            "location_restrictions": j.get("location_restrictions", []),
            "skill_overlap_score": j.get("skill_overlap_score", 0),
            "disqualifier_hits": j.get("disqualifier_hits", []),
            "ghost_job_signals": j.get("ghost_job_signals", []),
            "description": j["description"],
        }
        for j in kept
    ]
    context = (
        f"Today's date is {today}.\n"
        "---RESUME---\n" + _resume_text() + "\n"
        "---JOBS (JSON)---\n" + json.dumps(slim, indent=2)
    )
    scored = run_json(prompt, context=context, timeout=600)
    if not isinstance(scored, list):
        raise RuntimeError("Analyzer did not return a JSON array of jobs.")

    by_url = {j["url"]: j for j in kept}
    scored_by_url = {
        (entry.get("url") or "").strip(): entry
        for entry in scored
        if isinstance(entry, dict)
        and (entry.get("url") or "").strip() in by_url
    }
    if not scored_by_url:
        raise RuntimeError("Analyzer returned no known jobs; previous results preserved.")

    merged: list[dict] = []
    for url, base in by_url.items():
        entry = scored_by_url.get(url)
        analysis_missing = entry is None
        entry = entry or {}
        skill = int(base.get("skill_overlap_score", 0) or 0)
        llm_score = None if analysis_missing else int(entry.get("score", 0) or 0)
        # Blend: LLM judgement weighted higher, but skill_overlap anchors it.
        # Floor at 0 / cap at 100; never let a high LLM score paper over a
        # 20-skill_overlap posting.
        preference_adjustment = int(base.get("preference_adjustment", 0) or 0)
        final = max(0, min(100, skill + preference_adjustment)) if analysis_missing else \
            max(0, min(100, int(round(
                llm_score * 0.7 + skill * 0.3 + preference_adjustment
            ))))
        ghost_signals = base.get("ghost_job_signals", [])
        legitimacy_review = "staffing_agency_posting" in ghost_signals
        if legitimacy_review:
            final = min(final, 69)
        red_flags = ["analysis_missing"] if analysis_missing else list(entry.get("red_flags", []))
        red_flags.extend(signal for signal in ghost_signals if signal not in red_flags)
        merged.append({
            "title": entry.get("title") or base.get("title", ""),
            "company": entry.get("company") or base.get("company", ""),
            "location": base.get("location", "Remote"),
            "url": url,
            "posted_date": base.get("posted_date", ""),
            "source": base.get("source", ""),
            # Carry the derived categorization through to the UI.
            "work_mode": base.get("work_mode", "unknown"),
            "locale": base.get("locale", "unknown"),
            "relocation": base.get("relocation", "unknown"),
            "employment_type": base.get("employment_type", "unknown"),
            "seniority": base.get("seniority", "unknown"),
            "salary": base.get("salary", ""),
            "location_restrictions": base.get("location_restrictions", []),
            "fingerprint": base.get("fingerprint", ""),
            "is_repost": base.get("is_repost", False),
            "repost_count": base.get("repost_count", 1),
            "first_seen_at": base.get("first_seen_at", ""),
            "description_incomplete": base.get("description_incomplete", False),
            "role_relevance_score": base.get("role_relevance_score", 0),
            "role_relevance_reasons": base.get("role_relevance_reasons", []),
            # Scoring: surface both signals + the blended final.
            "skill_overlap_score": skill,
            "llm_score": llm_score,
            "disqualifier_hits": base.get("disqualifier_hits", []),
            "ghost_job_signals": ghost_signals,
            "preference_adjustment": preference_adjustment,
            "preference_reasons": base.get("preference_reasons", []),
            # Per-dimension breakdown (5-block structured eval). Defaults to
            # neutral when the LLM omits it — UI degrades gracefully to a
            # single overall score when blocks are empty.
            "blocks": _normalize_blocks(entry.get("blocks")),
            "score": final,
            "verdict": "review" if analysis_missing or legitimacy_review else entry.get("verdict", "review"),
            "match_reasons": entry.get("match_reasons", []),
            "red_flags": red_flags,
            "suggested_angle": entry.get("suggested_angle", ""),
        })

    # Append hard-rejected jobs (with score 0) so the dashboard can surface them
    # behind a filter; they sort to the bottom naturally.
    merged.extend(rejected)
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
    try:
        resolve_resume()
    except ResumeError as exc:
        raise RuntimeError(str(exc)) from exc

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
    all_jobs = analyze_jobs(jobs, profile=profile)
    for job in all_jobs:
        source = job.get("source", "unknown")
        stage = DISCOVERY_STAGES.setdefault(source, {})
        hard_rejected = any(
            str(flag).startswith("hard-rejected:") for flag in job.get("red_flags", [])
        )
        if not hard_rejected:
            stage["eligible"] = stage.get("eligible", 0) + 1
            if "analysis_missing" not in job.get("red_flags", []):
                stage["analyzed"] = stage.get("analyzed", 0) + 1
            else:
                stage["analysis_missing"] = stage.get("analysis_missing", 0) + 1
        if job.get("score", 0) >= THRESHOLD:
            stage["shortlisted"] = stage.get("shortlisted", 0) + 1
    report = {
        "sources": {
            name: {**sources.SOURCE_DIAGNOSTICS.get(name, {}), **counts}
            for name, counts in DISCOVERY_STAGES.items()
        },
        "totals": {
            key: sum(counts.get(key, 0) for counts in DISCOVERY_STAGES.values())
            for key in ("fetched", "fresh", "role_relevant", "eligible", "analyzed",
                        "analysis_missing", "shortlisted")
        },
    }
    (OUTPUT_DIR / "discovery_report.json").write_text(json.dumps(report, indent=2))
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
