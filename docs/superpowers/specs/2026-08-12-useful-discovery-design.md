# Useful Job Discovery Design

## Goal

Turn the current high-volume/empty-result pipeline into a reliable shortlist for a Bangladesh-based senior Python/backend engineer. Success means preserving every eligible job through scoring, preferring genuinely relevant roles, adding durable high-value sources, and explaining where jobs leave the funnel.

Raw source count is not the target. A source is useful only when it supplies dated postings with enough content to evaluate title, stack, location, and work authorization.

## Current failure

The observed run fetched and keyword-matched 100 jobs. Code rejected five for real eligibility constraints and sent 95 to the AI. The AI returned none because the prompt requires it to omit scores below 60 and the deterministic skill score treats all résumé strengths as requirements. `jobs.json` therefore contained only the five rejected jobs.

LinkedIn is implemented but disabled. Its live guest endpoint returned 30 current cards, mostly Bangladesh-local, but cards lack full descriptions and duplicate across queries.

## Design

### Complete scoring contract

The analyzer returns exactly one evaluation for every eligible input URL, including weak matches. Python owns filtering and display thresholds.

After parsing AI output, `finder.py` compares submitted and returned URLs. A missing result becomes a visible fallback record with `verdict: review`, the deterministic score, and `analysis_missing` in `red_flags`. Extra or unknown URLs are ignored. An empty or malformed response cannot replace a previous valid `jobs.json`; the run fails clearly when every result is missing.

Hard-rejected jobs remain visible with score zero. The dashboard can hide them by default without deleting them.

### Role-aware matching

Profile generation adds normalized role families such as `backend`, `python`, `platform`, `full-stack`, `data`, and `leadership`. Each job receives a title relevance signal before description keyword matching.

Discovery keeps jobs when either:

- the title matches a target role family; or
- the title is a recognized engineering title and the description contains at least two concrete profile skills.

Generic keywords such as `aws`, `backend`, and `llm` cannot admit an unrelated title by themselves. Explicit negative title families—sales, procurement, design, customer success, and unrelated QA/manual testing—are rejected unless the candidate profile targets them.

Skill scoring groups equivalents and adjacent tools:

- Python web: Django, FastAPI, Flask;
- relational data: PostgreSQL, MySQL, SQL;
- cloud: AWS, GCP, Azure;
- containers/orchestration: Docker, Kubernetes, k3s;
- async/distributed: Celery, queues, Kafka, Redis;
- search/vector: Elasticsearch, OpenSearch, pgvector.

The score measures evidence of role fit, not coverage of every skill in the résumé. One core-language hit, one framework/data hit, and one infrastructure/domain hit can produce a strong score. Missing optional résumé skills is neutral; explicit required skills outside the candidate profile are AI-evaluated gaps.

### Sources

#### LinkedIn

Enable LinkedIn by default but isolate it as a best-effort source. Searches cover senior Python, backend, platform, Django/FastAPI, tech-lead, and software-architect roles across Bangladesh, Dhaka, South Asia, Remote, and Worldwide.

Fetch multiple result pages with a conservative cap and delay. Extract the LinkedIn job ID and deduplicate by ID before URL. Cache card and detail responses under `output/source_cache/linkedin/` with a short TTL. Fetch full job detail only for fresh title-relevant cards. If detail fetch fails, retain the card with `description_incomplete: true`; matching must not pretend missing text is a mismatch.

LinkedIn guest HTML is unofficial and fragile. Failures are reported in source diagnostics and never stop other sources.

#### Ashby

Add the public job-board endpoint with configurable board names. Normalize full descriptions, publication timestamps where supplied, compensation, employment type, locations, and secondary locations. Start with a curated remote-friendly technology-company list verified during implementation.

#### Greenhouse and Lever

Move curated company slugs from Python-only constants to environment-configurable additive lists while retaining sensible defaults. Expand verified remote-friendly technology employers. Each board remains isolated so dead slugs become diagnostics rather than run failures.

#### Hacker News

Enable the existing source by default only after applying strict title/role relevance. Free-form comments without a recognizable role and remote/location signal are discarded before AI scoring.

#### Optional sources

API-key aggregators may be added behind environment variables when they provide a documented search API, dates, descriptions, and location restrictions. Missing credentials disable them silently with a diagnostic status. Indeed, Glassdoor, and other hostile HTML surfaces are not foundational sources; no scraping dependency or browser automation is added.

Bangladesh-specific sources are added only when a stable dated endpoint or feed can be verified. LinkedIn remains the initial Bangladesh-local channel.

### Source configuration

`.env` supports:

- `SOURCES`: enabled adapters;
- `GREENHOUSE_COMPANIES`, `LEVER_COMPANIES`, `ASHBY_COMPANIES`: additive comma-separated board slugs;
- `LINKEDIN_MAX_PAGES`: conservative page cap;
- `SOURCE_CACHE_HOURS`: cache TTL.

Defaults work without configuration. Invalid source or company names appear in diagnostics.

### Funnel diagnostics

Each adapter returns jobs plus a diagnostic record containing status, fetched count, error text, and duration. Pipeline stages record per-source counts:

`fetched → fresh → role_relevant → eligible → analyzed → shortlisted`

Diagnostics are persisted in `output/discovery_report.json`, returned by a new read-only API endpoint, and rendered as a compact dashboard table. The report includes overall missing-analysis count and rejection reasons.

### Dashboard behavior

The default view shows `apply` and `review` jobs. Hard rejects are available through an explicit filter. Cards show source, analysis completeness, repost status, deterministic score, AI score, and final score without overwhelming the primary title/company/location view.

A discovery summary distinguishes:

- no jobs fetched;
- jobs fetched but irrelevant;
- jobs eligible but AI analysis incomplete;
- jobs scored but below threshold.

### Safety and reliability

- Network calls use timeouts, bounded responses, a descriptive user agent, and no credentials in URLs or logs.
- Source failures never abort the complete run.
- Job descriptions are untrusted prompt data and cannot instruct the AI CLI.
- Cached source responses contain public job data only and remain gitignored under `output/`.
- LinkedIn request volume remains low; no authentication, session cookies, proxy rotation, CAPTCHA bypass, or anti-bot evasion is implemented.

## Testing and verification

Test-first coverage includes:

- one output record per submitted URL;
- empty/missing AI responses cannot silently erase jobs;
- realistic Python/backend fixtures rank above unrelated jobs;
- skill-family equivalents score consistently;
- generic description keywords cannot rescue unrelated titles;
- LinkedIn pagination, job-ID deduplication, cache behavior, incomplete descriptions, and graceful blocking;
- Ashby normalization;
- configurable company-board merging;
- per-source funnel accounting;
- legacy output/config compatibility.

Final verification runs the full test suite and one live discovery pass. The live report must show nonzero eligible jobs and no unexplained loss between `eligible` and `analyzed`. Shortlist quality is manually sampled: the top ten must be plausible senior backend/Python/platform roles, not merely jobs containing a technology keyword.

## Explicit exclusions

- Automated applications, login-based scraping, CAPTCHA bypass, rotating proxies, browser automation, paid aggregation services without user credentials, and unsupported claims of complete LinkedIn coverage.
- A database, job queue, plugin system, or new frontend framework.

These exclusions keep the project useful and locally maintainable rather than maximizing fragile adapters.
