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
   │                      drop anything > 30 days old, keyword-prefilter,
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
| Hacker News "Who is hiring" | Algolia API | `created_at_i` | opt-in (freeform comments) |
| LinkedIn (guest jobs) | HTML | `datetime` | opt-in; **onsite + Bangladesh-local** roles, HTML-scraped & rate-limited |

Ten feeds are enabled by default. Greenhouse and Lever pull public, dated JSON from a curated list of well-known tech companies (edit `GREENHOUSE_COMPANIES` / `LEVER_COMPANIES` in `sources.py` to taste). `hackernews` and `linkedin` are opt-in via `SOURCES` — HN because its posts are freeform, LinkedIn because it's HTML-scraped and can rate-limit by IP. LinkedIn is the source of **onsite** and **Bangladesh-local** postings.

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

## Setup

Requirements: Python 3.10+ and at least one of the CLIs installed and authenticated: `claude`, `codex`, or `opencode`.

```bash
pip install -r requirements.txt
cp .env.example .env        # choose your AI_CLI and options
```

Then add your own `resume.md` in the project root (gitignored — it never leaves your machine).

## Run

```bash
# Web dashboard
python server.py            # → http://127.0.0.1:8000

# Or headless
python finder.py
```

Results land in `output/` (gitignored): `jobs.json` (scored jobs), `raw_jobs.json` (everything discovered after filtering), and `cover_letters/`.

## Configuration

All optional, via `.env` or environment:

| Var | Default | Meaning |
|---|---|---|
| `AI_CLI` | `claude` | Which CLI drives analysis: `claude`, `codex`, `opencode` |
| `MAX_JOB_AGE_DAYS` | `30` | Freshness cutoff — jobs older than this are dropped |
| `MAX_JOBS_TO_ANALYZE` | `40` | Cap on how many (freshest) jobs get AI scoring |
| `SOURCES` | 7 defaults | Comma-separated subset, e.g. `remoteok,himalayas`. Add `linkedin` (onsite + BD-local) or `hackernews` to include the opt-in sources. |

## Tests

```bash
pytest
```

## Project structure

```
finder.py         # 4-step pipeline (profile → discover → analyze → cover letters)
sources.py        # structured job feeds + deterministic date/keyword filtering
ai_cli.py         # configurable CLI runner (claude/codex/opencode) + JSON extraction
server.py         # FastAPI: /api/jobs, /api/status, /api/cover-letter, /api/run (SSE)
ui/index.html     # single-file dashboard
prompts/          # AI prompt files for each step
CLAUDE.md         # agent context
test_*.py         # unit tests (feeds, CLI runner, pipeline, API mocked)
```
