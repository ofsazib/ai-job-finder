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
   │                      drop anything > 30 days old, keyword-prefilter
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

| Source | Type | Date field |
|---|---|---|
| [RemoteOK](https://remoteok.com/api) | JSON API | `epoch` / `date` |
| [Remotive](https://remotive.com/api/remote-jobs) | JSON API | `publication_date` |
| [Arbeitnow](https://www.arbeitnow.com/api/job-board-api) | JSON API | `created_at` |
| [We Work Remotely](https://weworkremotely.com) | RSS | `pubDate` |
| Hacker News "Who is hiring" | Algolia API | `created_at_i` | (opt-in) |

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
| `SOURCES` | all defaults | Comma-separated subset, e.g. `remoteok,remotive`. Add `hackernews` to include HN. |

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
