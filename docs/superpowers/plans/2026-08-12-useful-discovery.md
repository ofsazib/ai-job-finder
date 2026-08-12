# Useful Job Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve every eligible job through scoring, rank realistic Python/backend roles correctly, add durable popular sources, and expose the full discovery funnel.

**Architecture:** Keep adapters and normalization in `sources.py`, deterministic relevance/matching in `matching.py`, orchestration/integrity in `finder.py`, and diagnostics/UI delivery in `server.py` plus `ui/index.html`. Reuse stdlib HTTP, JSON, hashing, and the existing AI CLI; add no dependency.

**Tech Stack:** Python 3.10+, FastAPI, stdlib urllib/filesystem, vanilla JavaScript, pytest.

---

### Task 1: Analyzer completeness and safe persistence

**Files:** Modify `prompts/analyze.md`, `finder.py`, `test_pipeline.py`.

- [ ] Add failing tests where the AI omits one of two URLs and where it returns an empty list. Assert the omitted job becomes `review` with `analysis_missing`, while an entirely empty result raises without overwriting an existing `jobs.json`.
- [ ] Run `uv run pytest test_pipeline.py -k 'analysis_missing or empty_analysis' -q`; expect failures for missing behavior.
- [ ] Change the prompt from “Keep ONLY jobs scoring >= 60” to “Return exactly one entry for every input URL, including weak jobs.”
- [ ] Implement URL reconciliation after `run_json`: ignore unknown URLs; synthesize missing records from base data with deterministic score and `analysis_missing`; raise when no submitted URL was returned at all.
- [ ] Write `jobs.json` only after reconciliation succeeds.
- [ ] Run targeted tests and `make test`; expect PASS.
- [ ] Commit `fix: preserve every eligible analyzed job`.

### Task 2: Role-aware relevance filter

**Files:** Modify `matching.py`, `finder.py`, `test_matching.py`, `test_pipeline.py`.

- [ ] Add failing fixtures proving senior Python/backend/platform titles pass, procurement/customer-success/design/manual-QA titles fail, and a generic engineering title needs at least two concrete skills.
- [ ] Implement `role_relevance(job, profile) -> {relevant, score, reasons}` using target-role/title families first and whole-token skill evidence second. Candidate-targeted families override negative defaults.
- [ ] Replace the any-keyword filter in `discover_jobs` with role relevance after freshness. Retain `sources.filter_keywords` for compatibility but stop using it in the main pipeline.
- [ ] Persist `role_relevance_score` and reasons on jobs.
- [ ] Run targeted tests and `make test`; expect PASS.
- [ ] Commit `fix: filter jobs by target role relevance`.

### Task 3: Skill-family scoring

**Files:** Modify `matching.py`, `test_matching.py`, `prompts/analyze.md`.

- [ ] Add failing tests for Python+Django+PostgreSQL strong fit, Python+FastAPI equivalence, AWS/GCP adjacency, a lone generic AWS mention remaining weak, and unrelated stacks scoring low.
- [ ] Implement fixed skill families and score evidence across core language, framework/data, and infrastructure/domain groups. Keep `50` neutral only when the profile has no configured skills.
- [ ] Return a score based on matched evidence rather than total résumé skill coverage; cap a single-family match below 50 and allow three relevant families to exceed 70.
- [ ] Update prompt guidance so deterministic score is evidence, not an absolute ceiling.
- [ ] Run targeted tests and `make test`; expect PASS.
- [ ] Commit `fix: score job skill evidence by families`.

### Task 4: Source diagnostics and funnel accounting

**Files:** Modify `sources.py`, `finder.py`, `server.py`, `test_sources.py`, `test_pipeline.py`, `test_server.py`.

- [ ] Add failing tests for successful/failed adapter diagnostics and per-source counts across `fetched`, `fresh`, `role_relevant`, `eligible`, `analyzed`, and `shortlisted`.
- [ ] Add a module-level diagnostic collector reset at `fetch_all` start. Record status, count, duration, and bounded error text without changing adapter return types.
- [ ] Build `output/discovery_report.json` from source diagnostics and pipeline stages.
- [ ] Add `GET /api/discovery-report`, returning an empty report before the first run.
- [ ] Run targeted tests and `make test`; expect PASS.
- [ ] Commit `feat: report the job discovery funnel`.

### Task 5: LinkedIn pagination, deduplication, detail, and cache

**Files:** Modify `sources.py`, `.env.example`, `test_sources.py`.

- [ ] Add HTTP-fixture tests for two pages, duplicate LinkedIn IDs, detail-page extraction, cache reuse, incomplete details, and blocked responses.
- [ ] Expand role/location queries and page `start` offsets using `LINKEDIN_MAX_PAGES` with a conservative default of 2.
- [ ] Extract the numeric job ID, canonicalize URLs, deduplicate by ID, and title-filter cards before detail requests.
- [ ] Cache public HTML under `output/source_cache/linkedin/` using hashed URLs and `SOURCE_CACHE_HOURS`; use `time.sleep(1)` between uncached LinkedIn requests.
- [ ] Parse full description from the public detail endpoint. Set `description_incomplete` when unavailable.
- [ ] Enable LinkedIn in defaults and document its best-effort behavior.
- [ ] Run targeted tests, `make test`, and one bounded live adapter check.
- [ ] Commit `feat: improve Bangladesh LinkedIn discovery`.

### Task 6: Ashby public boards

**Files:** Modify `sources.py`, `.env.example`, `README.md`, `test_sources.py`.

- [ ] Add a failing Ashby fixture covering description, location, secondary locations, remote flag, employment type, compensation, and publication date.
- [ ] Implement `fetch_ashby()` against `https://api.ashbyhq.com/posting-api/job-board/{board}` with curated defaults and additive `ASHBY_COMPANIES` configuration.
- [ ] Isolate each board failure, normalize through `_job`, register the adapter, and enable it by default.
- [ ] Run targeted tests and `make test`; expect PASS.
- [ ] Commit `feat: add Ashby job boards`.

### Task 7: Expand configurable Greenhouse, Lever, and Hacker News

**Files:** Modify `sources.py`, `.env.example`, `README.md`, `test_sources.py`.

- [ ] Add failing tests that environment company lists merge with defaults without duplicates and invalid names do not stop valid boards.
- [ ] Implement one comma-list helper and use it for Greenhouse, Lever, and Ashby boards.
- [ ] Expand defaults only with live-verified remote-friendly technology boards.
- [ ] Enable Hacker News by default, but require role relevance before its records enter analysis.
- [ ] Run targeted tests, `make test`, and bounded live yield checks per structured adapter.
- [ ] Commit `feat: expand configurable technology job boards`.

### Task 8: Dashboard diagnostics and useful defaults

**Files:** Modify `ui/index.html`, `server.py`, `test_server.py`.

- [ ] Add API smoke tests for legacy jobs and empty/populated discovery reports.
- [ ] Default verdict view to apply/review while retaining an explicit hard-reject option.
- [ ] Render a compact source funnel table and distinguish empty fetch, irrelevant results, incomplete AI analysis, and below-threshold results.
- [ ] Show deterministic, AI, and final scores plus `analysis_missing`/incomplete-description badges only when applicable.
- [ ] Escape every source-provided value with existing `esc()` and provide accessible labels.
- [ ] Run `make test` and `git diff --check`; expect PASS/clean.
- [ ] Commit `feat: expose discovery quality diagnostics`.

### Task 9: Live usefulness verification and documentation

**Files:** Modify `README.md`; generated `output/` remains ignored.

- [ ] Run `make test`; require zero failures.
- [ ] Run one live pipeline with the configured AI CLI and bounded source settings.
- [ ] Inspect `discovery_report.json`; require nonzero eligible jobs and zero unexplained eligible-to-analyzed loss.
- [ ] Inspect the top ten: require plausible senior backend/Python/platform roles and record any residual false positives.
- [ ] Update README configuration, source reliability notes, matching semantics, and funnel troubleshooting.
- [ ] Run final `make test`, `git diff --check`, and `git status --short`.
- [ ] Commit `docs: document useful discovery and source tuning`.

## Constraints

- Execute directly on the current branch per user instruction.
- No new dependency, login/session scraping, CAPTCHA bypass, proxy rotation, browser automation, or automatic applications.
- Never trade shortlist relevance for raw count.
- Do not claim completion without a fresh full-suite run and live funnel evidence.
