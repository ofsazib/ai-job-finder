# Career Intelligence Design

## Goal

Extend AI Job Finder from discovery and basic application tracking into a local career workflow covering duplicate suppression, ad-hoc analysis, configurable fit rules, application-quality review, artifact history, company and salary intelligence, outcome feedback, and interview preparation.

The implementation must preserve the project's current shape: Python, FastAPI, JSON files, the existing configurable AI CLI, and a single vanilla-JavaScript dashboard. No database, frontend build system, browser automation, OAuth integration, or new runtime dependency is required.

## Delivery phases

### Phase 1: discovery foundation

1. Fix the stale status test so the committed structured status schema has a clean baseline.
2. Assign each normalized posting a stable fingerprint derived from company and title, with conservative normalization only. Exact URL remains the primary identity; the fingerprint groups probable reposts.
3. Persist a compact discovery history in `output/job_history.json`. Each entry records fingerprint, known URLs, first and last seen timestamps, and observation count.
4. Mark jobs with `is_repost`, `repost_count`, and `first_seen_at`. Do not discard reposts automatically: the dashboard may hide them, while a materially changed posting remains inspectable.
5. Add a manual-analysis endpoint and dashboard form accepting pasted posting text plus optional title, company, URL, and location. It uses the same categorization, hard-reject, and scoring path as feed jobs and merges the result into `jobs.json`.
6. Add `preferences.json` for user-controlled deal-breakers and preferences: minimum salary, employment types, work modes, timezone overlap, on-call tolerance, relocation willingness, preferred domains, and avoided terms. Missing settings are neutral and existing behavior remains unchanged.

### Phase 2: application quality

1. Extend the generated search profile with concise writing-style guidance and reusable proof points derived from the resume. The existing profile file remains the canonical candidate context.
2. Store application artifacts under `output/applications/<job-id>/`: normalized posting JSON, generated cover letter, application metadata, and later research/interview files. The job ID is a filesystem-safe fingerprint plus a short URL hash to prevent collisions.
3. Keep a single-pass cover letter as the default. Add an optional reviewer pass controlled by `COVER_LETTER_REVIEW=1`. The reviewer returns actionable criticism and a revised final letter in one AI call; malformed review output falls back to the original draft without data loss.
4. Archive artifacts when a letter is generated and update application metadata when status or outcome changes.

### Phase 3: intelligence

1. Normalize published salary into structured minimum, maximum, currency, and period fields while preserving the source text. Annualization supports hourly, monthly, and yearly periods with explicit configurable assumptions; unknown currencies are not converted.
2. Add manually triggered company research for one shortlisted job. Research uses the configured AI CLI's knowledge and optional fetched source text supplied by the server. Every factual claim must include a source URL; unsourced output is labeled inference.
3. Save research to the job's artifact directory and expose it in the dashboard. Research never runs automatically during discovery.
4. Compute outcome insights from local applications: conversion by source, score band, work mode, seniority, and detected skill. Only dimensions with a minimum sample size are surfaced.
5. Turn statistically credible outcomes into transparent ranking adjustments capped at a small range. Every adjustment is stored with its reason; sparse data produces no adjustment. Original deterministic and LLM scores remain visible.

### Phase 4: interview preparation

1. Add a manual action for applications that reached an interview stage.
2. Generate a preparation pack from the archived posting, exact cover letter, resume/profile proof points, recorded stage, and any company research.
3. The pack contains role priorities, likely technical questions, behavioral questions mapped to genuine STAR evidence, questions for the interviewer, risks/gaps with honest bridge answers, and a short rehearsal checklist.
4. Save the pack in the application directory and render it read-only in the dashboard. The system does not invent experience or conduct voice/video mock interviews.

## Components and boundaries

- `sources.py`: posting normalization, salary parsing, and fingerprint inputs. It remains independent of persistence and HTTP.
- `matching.py`: preference evaluation and reusable deterministic fit signals. It does not read files.
- `finder.py`: shared analysis orchestration, manual-job entry point, score adjustment, letter review, and artifact creation.
- `server.py`: JSON persistence, validation, and API routes. Long AI operations use the existing streaming/progress pattern where useful.
- `ui/index.html`: forms, filters, and read-only artifact panels. It contains no business scoring rules.
- `prompts/`: separate focused prompts for review, company research, and interview preparation.

Helpers stay in existing modules unless reuse across at least two modules makes a small focused module clearly shorter. JSON writes use temporary-file replacement for files whose corruption would lose application history.

## Data flow

Feed and manual postings converge immediately after normalization. Both receive deterministic facets, eligibility checks, skill overlap, AI evaluation, outcome adjustment, and the same output schema. Discovery history is updated before analysis so failed AI runs do not lose observation data.

Application actions resolve the job's stable artifact directory, snapshot source material, then create or update one artifact. Status remains indexed by URL for backward compatibility, while artifact metadata records the stable job ID.

Outcome insights are derived from stored status and job data on demand. The derived report may be cached, but status and artifacts remain the source of truth.

## API surface

- `POST /api/jobs/manual`: validate and analyze pasted posting data.
- `GET /api/preferences` and `PUT /api/preferences`: read and replace validated settings.
- `POST /api/company-research`: generate or refresh research for one job.
- `GET /api/artifacts`: list available artifacts for one job.
- `POST /api/interview-prep`: generate a pack for one tracked application.
- Existing jobs, status, outcome, statistics, follow-up, cover-letter, and run endpoints remain compatible.

AI-backed endpoints reject concurrent duplicate work for the same job and return clear errors without replacing existing artifacts.

## Error handling and safety

- Manual posting input requires non-empty description text and enforces a size limit.
- URLs are data, never shell input. AI CLI execution continues to use argument arrays without a shell.
- Job descriptions remain untrusted prompt content and are explicitly delimited; embedded instructions must be ignored.
- Research fetches only HTTP(S) URLs selected by the user or returned by search, with timeouts and response-size limits. Private and loopback addresses are rejected.
- JSON schema migration is additive. Unknown fields are preserved where practical and missing new files mean default behavior.
- Artifact writes never destroy a previously valid draft when review, research, or interview generation fails.

## Testing

Each behavior follows red-green-refactor with focused tests:

- fingerprint stability, conservative grouping, repost history, and unchanged-posting behavior;
- manual-input validation and convergence with feed analysis;
- preference defaults, hard deal-breakers, and soft scoring effects;
- artifact paths, atomic persistence, and legacy status compatibility;
- reviewer success and fallback;
- salary parsing and annualization edge cases;
- research source validation and persistence;
- minimum-sample outcome adjustments and score caps;
- interview pack inputs, honest-evidence constraints, and API errors;
- dashboard smoke checks for new controls where practical.

Every phase ends with the complete test suite. The existing Starlette/httpx deprecation warning is recorded but dependency migration is outside this feature scope unless it becomes a test failure.

## Explicit exclusions

- Automatic job applications or messages.
- Gmail, Notion, LinkedIn account, or ATS integration.
- CV/PDF generation, browser automation, plugins, database migration, scheduled background jobs, currency-rate APIs, and voice mock interviews.
- Automatic web research for every discovered job.

These exclusions keep all ten requested capabilities inside the existing local product rather than turning it into a separate automation platform.
