# AI Job Finder

An autonomous job-hunting pipeline that finds **recent** remote jobs and drafts tailored cover letters. It reads your resume, pulls fresh postings from structured job-board feeds, drops anything older than 30 days, scores each posting against your profile with your AI coding CLI (`claude`, `codex`, or `opencode`), and writes a cover letter for every role worth applying to — all reviewable in a local web dashboard.

## Why this exists

The predecessor (`ai-job-scraper`) used Firecrawl to *web-search* for jobs, then scraped result pages. Two problems made it miss recent jobs:

1. **Discovery** — generic search engines don't reliably surface freshly-posted roles.
2. **Freshness** — listing pages rarely expose a machine-readable post date, so "posted within 30 days" could only be *guessed* by the LLM from page text.

Swapping Firecrawl for another crawler (Crawl4AI, etc.) wouldn't fix either — they're the same category of tool. The real fix is the **source**: this project pulls from job-board **feeds/APIs that return a real posting date per job**, so freshness is enforced **deterministically in code** before any AI tokens are spent.

## How it works

```
resume.md
   │
   ▼
1. Build search profile   AI CLI extracts target roles + match keywords
   │                      from your resume
   ▼
2. Discover (structured)  Fetch dated postings from job-board feeds,
   │                      drop anything > 30 days old, title/role-filter,
   │                      and categorize each (work mode, locale, salary…)
   ▼
3. Analyze & score        AI CLI scores each posting 0–100 against your
   │                      profile and gives a verdict (apply/review/skip)
   ▼
4. Cover letters          For each "apply", the AI CLI drafts a tailored letter
   │
   ▼
output/jobs.json + output/cover_letters/*.md
```

## Sources

All free, no API key, each returns a per-posting date:

| Source | Type | Date field | Notes |
|---|---|---|---|
| [RemoteOK](https://remoteok.com/api) | JSON API | `epoch` / `date` | remote |
| [Remotive](https://remotive.com/api/remote-jobs) | JSON API | `publication_date` | remote |
| [Arbeitnow](https://www.arbeitnow.com/api/job-board-api) | JSON API | `created_at` | remote + EU onsite |
| [We Work Remotely](https://weworkremotely.com) | RSS | `pubDate` | remote |
| [Himalayas](https://himalayas.app/jobs/api) | JSON API | `pubDate` | remote; ships salary, seniority, location restrictions |
| [Jobicy](https://jobicy.com/api/v2/remote-jobs) | JSON API | `pubDate` | remote |
| [Working Nomads](https://www.workingnomads.com) | JSON API | `pub_date` | remote |
| [The Muse](https://www.themuse.com/developers/api/v2) | JSON API | `publication_date` | tech roles (Software Engineering / Data Science) |
| [Greenhouse](https://developers.greenhouse.io/job-board.html) | JSON API | `updated_at` | public company boards (Stripe, Databricks, GitLab, Figma, Vercel, …) |
| [Lever](https://github.com/lever/postings-api) | JSON API | `createdAt` | public company boards (Netflix, Spotify, Plaid, Notion, …) |
| [Ashby](https://developers.ashbyhq.com/docs/public-job-posting-api) | JSON API | `publishedAt` | OpenAI, Notion, Linear, Supabase, Ramp; full descriptions |
| Hacker News "Who is hiring" | Algolia API | `created_at_i` | freeform comments; strict role filtering |
| LinkedIn (guest jobs) | HTML | `datetime` | **onsite + Bangladesh-local** roles; cached, rate-limited, best effort |

All adapters are enabled by default. Greenhouse, Lever, and Ashby pull public company boards; add board slugs through environment variables without changing Python. LinkedIn uses its public guest pages because LinkedIn has no general public search API. It fetches only relevant cards, caches responses, delays uncached requests, and fails without stopping other sources.

## Matching and discovery diagnostics

Discovery is title-first. Target backend/Python/platform roles pass directly; generic engineering titles need at least two concrete profile skills. Unrelated sales, procurement, design, manual-QA, DevOps, frontend, Node, and JVM titles do not pass unless the generated profile explicitly targets that family.

Skill scoring measures positive evidence across language, web framework, database, cloud, containers, distributed systems, and search families. A posting is not penalized for omitting every technology on the resume. The AI must return an evaluation for every eligible URL; omitted evaluations remain visible as `analysis_missing` instead of disappearing.

The dashboard's discovery funnel shows, per source:

`fetched → fresh → role relevant → eligible → analyzed → shortlisted`

Use it to distinguish a source outage from irrelevant results, location rejection, incomplete AI output, or genuinely low scores.

## Categorization

Every discovered job is enriched with facets — pulled from structured fields where the feed provides them (Himalayas), inferred from the title/location/description otherwise — so the dashboard can filter and the analyzer can reason about fit:

| Facet | Values |
|---|---|
| `work_mode` | remote · onsite · hybrid · unknown |
| `locale` | bangladesh (local) · international · unknown |
| `relocation` | yes · no · unknown (relocation/visa support) |
| `employment_type` | full-time · contract · part-time · internship · unknown |
| `seniority` | junior · mid · senior · lead · unknown |
| `salary` | human-readable range when published |
| `location_restrictions` | regions the role is limited to |

The dashboard has sidebar filters for work mode and locale (e.g. show only **Local (BD)** or only **Remote**), and shows the facets as badges on each job card.

## Stack

- **Python + FastAPI** — pipeline orchestration and API
- **Structured feeds (stdlib `urllib`)** — dated job discovery, no crawler/API key
- **Configurable AI CLI** — `claude`, `codex`, or `opencode` for resume analysis, scoring, and cover letters (set via `AI_CLI`)
- **Vanilla JS + Tailwind** — zero-build single-file UI

## Quick start

You need Python 3.10+ and one authenticated AI CLI: `claude`, `codex`, or `opencode`.

```bash
git clone <repository-url>
cd ai-job-finder
make setup
```

Then:

1. Put your resume in `resume.md` at the project root.
2. Open `.env` and set `AI_CLI` to the CLI you installed.
3. Start the dashboard:

```bash
make run
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000), then click **Find Jobs**. The first run can take several minutes because it fetches public sources and asks the AI CLI to score matches.

To run without the dashboard:

```bash
make find
```

If `make` is unavailable, use the direct commands:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
.venv/bin/python server.py
```

Your resume, configuration, results, source cache, and application data stay local and are ignored by Git.

Results land in `output/` (gitignored): `jobs.json`, `raw_jobs.json`, `discovery_report.json`, cached public source pages, and `cover_letters/`.

## Configuration

All optional, via `.env` or environment:

| Var | Default | Meaning |
|---|---|---|
| `AI_CLI` | `claude` | Which CLI drives analysis: `claude`, `codex`, `opencode` |
| `MAX_JOB_AGE_DAYS` | `30` | Freshness cutoff — jobs older than this are dropped |
| `MAX_JOBS_TO_ANALYZE` | `100` | Round-robin cap across sources before AI scoring |
| `SOURCES` | all defaults | Comma-separated subset, e.g. `linkedin,ashby,greenhouse` |
| `LINKEDIN_MAX_PAGES` | `2` | Pages per LinkedIn query; keep conservative |
| `SOURCE_CACHE_HOURS` | `6` | Public-page cache lifetime |
| `GREENHOUSE_COMPANIES` | empty | Additional board slugs, comma-separated |
| `LEVER_COMPANIES` | empty | Additional company slugs, comma-separated |
| `ASHBY_COMPANIES` | empty | Additional board slugs, comma-separated |

## Tests

```bash
make test
```

## Project structure

```
finder.py         # 4-step pipeline (profile → discover → analyze → cover letters)
sources.py        # structured feeds, caching, dates, normalization, diagnostics
ai_cli.py         # configurable CLI runner (claude/codex/opencode) + JSON extraction
server.py         # FastAPI jobs, funnel report, tracking, cover letters, SSE run
ui/index.html     # single-file dashboard
prompts/          # AI prompt files for each step
CLAUDE.md         # agent context
test_*.py         # unit tests (feeds, CLI runner, pipeline, API mocked)
```
