# AI Job Finder

An autonomous job-hunting pipeline that finds **recent** remote jobs and drafts tailored cover letters on demand. It reads your resume, pulls fresh postings from structured job-board feeds, drops anything older than 30 days, and scores each posting against your profile with your AI coding CLI (`claude`, `codex`, or `opencode`) — all reviewable in a local web dashboard without spending tokens on unused cover letters.

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
1. Build search profile   AI CLI extracts target roles, skills, seniority,
   │                      languages, region eligibility from your resume
   ▼
2. Discover (structured)  Fetch dated postings from job-board feeds,
   │                      drop anything > 30 days old, role-filter,
   │                      hard-reject clearance/citizenship/language/region locks
   ▼
3. Semantic rank          Embedder sidecar vectorizes survivors;
   │                      cosine(resume, jd) shrinks the LLM analysis
   │                      cap from 100 → 30 (~70% token cut)
   ▼
4. Analyze & score        AI CLI scores each posting 0–100 across 5 blocks
   │                      (stack / seniority / location / compensation / culture)
   ▼
5. Cover letters          Click a job to generate once, then reuse the local copy
   │
   ▼
output/jobs.json + output/cover_letters/*.md
```

## Prerequisites

You need all four on the host machine:

| Requirement | Minimum | Why | Install |
|---|---|---|---|
| **Python** | **3.14** | Host pipeline + dashboard | [python.org](https://www.python.org/downloads/) · `pyenv install 3.14` |
| **Docker + Compose** | Docker 24+ · Compose v2 | Runs the embedder sidecar (keeps fastembed + ONNX deps off the host) | [Docker Desktop](https://docs.docker.com/get-docker/) |
| **AI coding CLI** | one of `claude` / `codex` / `opencode` | Scores postings + writes cover letters via your locally-installed CLI | [Claude Code](https://claude.com/claude-code) · [Codex](https://github.com/openai/codex) · [OpenCode](https://github.com/sst/opencode) |
| **`make`** | any | One-command lifecycle (`make up`, `make down`) | macOS: bundled · Linux: `apt install build-essential` |

**Verified on:** macOS 14+ (Apple Silicon + Intel), Ubuntu 22.04+. Windows users should run inside [WSL2](https://learn.microsoft.com/en-us/windows/wsl/).

**Verify your setup:**

```bash
python3 --version        # → 3.14.x
docker compose version   # → Docker Compose version v2.x
which claude             # → /usr/local/bin/claude (or codex / opencode)
make --version           # → GNU Make 3.81+
```

### Why a sidecar for embeddings

The host Python env stays tiny (~50 MB). The dockerized embedder (`embedder/`) loads `fastembed` + ONNX Runtime + the `MiniLM-L6-v2` model (~90 MB) and serves them over HTTP at `http://localhost:8787`. The host calls it via `embedding.py` and stores vectors in a local SQLite database using the `sqliteai-vector` extension for fast cosine search. If the sidecar is down, the pipeline gracefully falls back to keyword-only ranking with `MAX_JOBS_TO_ANALYZE=100` — nothing crashes.

## Quick start

```bash
git clone https://github.com/ofsazib/ai-job-finder.git
cd ai-job-finder
make setup       # creates .venv, installs host deps, copies .env.example → .env
```

Then:

1. Add your resume using any one of these options:
   - Put `resume.md` at the project root.
   - Put a text-based PDF named `resume.pdf` or `cv.pdf` at the project root. The first run creates `resume.md` automatically.
   - Start the dashboard and upload a PDF when prompted.
2. Open `.env` and set `AI_CLI` to the CLI you installed.
3. Start the whole stack (embedder sidecar + dashboard):

```bash
make up          # builds the docker image on first run (~90 MB model download),
                # waits for sidecar health, then starts the dashboard
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000), then click **Find Jobs**.

The first run takes several minutes: feed fetch + cold-start LLM scoring. Subsequent runs reuse the analysis cache (jobs unchanged → near-zero LLM tokens).

Cover letters are never generated during **Find Jobs**. Open a job's **Cover letter** dialog and select **Generate cover letter** when you want one. The result is stored in `output/cover_letters/`; later opens reuse it immediately, and **Regenerate** explicitly replaces it.

After pulling new code, restart the dashboard so backend and frontend changes load: press `Ctrl+C`, run `make up` again, then refresh the browser.

PDF text is cleaned into Markdown by the configured AI CLI without intentionally changing facts. If that cleanup fails, the extracted text is used directly. Scanned/image-only PDFs need OCR first.

### Other make targets

| Command | Action |
|---|---|
| `make up` | Build sidecar + start dashboard (recommended) |
| `make down` | Stop the embedder sidecar after stopping the dashboard with `Ctrl+C` |
| `make run` | Start dashboard only (assumes sidecar up or accepts fallback) |
| `make find` | Run pipeline headless (no dashboard) |
| `make embedder-build` | Build the docker image |
| `make embedder-up` | Start sidecar in background + wait for health |
| `make embedder-down` | Stop sidecar (model volume preserved) |
| `make embedder-logs` | Tail sidecar logs (model load progress, errors) |
| `make embedder-health` | `curl /health` on the sidecar |
| `make test` | Run the complete test suite |
| `make clean` | Remove Docker state, generated output/caches, and `.venv`; preserve resume and `.env` |

### Fallback (no docker)

If docker isn't available, `make run` still works — the pipeline detects the missing sidecar and falls back to round-robin source interleaving with the larger `MAX_JOBS_TO_ANALYZE=100` cap. You lose semantic ranking but keep everything else.

### No `make` available

```bash
python3.14 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
.venv/bin/python server.py
```

Your resume, configuration, results, source cache, embeddings, and application data stay local and are ignored by Git.

Results land in `output/` (gitignored): `jobs.json`, `raw_jobs.json`, `discovery_report.json`, `analysis_cache.json`, `embeddings.sqlite`, cached public source pages, and `cover_letters/`.

## Sources

All free, no API key, each returns a per-posting date:

| Source | Type | Date field | Notes |
|---|---|---|---|
| [RemoteOK](https://remoteok.com/api) | JSON API | `epoch` / `date` | remote |
| [Remotive](https://remotive.com/api/remote-jobs) | JSON API | `publication_date` | remote |
| [Arbeitnow](https://www.arbeitnow.com/api/job-board-api) | JSON API | `created_at` | remote + EU onsite |
| [We Work Remotely](https://weworkremotely.com) | RSS | `pubDate` | remote |
| [LaraJobs](https://larajobs.com/feed) | RSS + `job:` namespace | `pubDate` | rich structured fields (company/salary/tags) |
| [Jobspresso](https://jobspresso.co/jobs/feed/) | RSS | `pubDate` | general remote tech |
| [VueJobs](https://vuejobs.com/feed) | RSS | `pubDate` | Vue + full-stack |
| [Himalayas](https://himalayas.app/jobs/api) | JSON API | `pubDate` | remote; ships salary, seniority, location restrictions |
| [Jobicy](https://jobicy.com/api/v2/remote-jobs) | JSON API | `pubDate` | remote |
| [Working Nomads](https://www.workingnomads.com) | JSON API | `pub_date` | remote |
| [The Muse](https://www.themuse.com/developers/api/v2) | JSON API | `publication_date` | tech roles (Software Engineering / Data Science) |
| [Greenhouse](https://developers.greenhouse.io/job-board.html) | JSON API | `updated_at` | Anthropic, Stripe, Datadog, Vercel, Block, Brex, Coinbase, Mercury, Chime, +more |
| [Lever](https://github.com/lever/postings-api) | JSON API | `createdAt` | Spotify, Toptal, Wellfound |
| [Ashby](https://developers.ashbyhq.com/docs/public-job-posting-api) | JSON API | `publishedAt` | OpenAI, Notion, Linear, Supabase, Ramp; full descriptions |
| Hacker News "Who is hiring" | Algolia API | `created_at_i` | freeform comments; strict role filtering |
| LinkedIn (guest jobs) | HTML | `datetime` | **onsite + Bangladesh-local** roles; cached, rate-limited, best effort |

All adapters are enabled by default. Greenhouse, Lever, and Ashby pull public company boards; add board slugs through environment variables without changing Python. LinkedIn uses its public guest pages because LinkedIn has no general public search API. It fetches only relevant cards, caches responses, delays uncached requests, and fails without stopping other sources.

## Matching and discovery diagnostics

Discovery is title-first. Target backend/Python/platform roles pass directly; generic engineering titles need at least two concrete profile skills. Unrelated sales, procurement, design, manual-QA, DevOps, frontend, Node, and JVM titles do not pass unless the generated profile explicitly targets that family.

After role filtering, every survivor is scored on multiple dimensions:

- **Hard reject (code, free)** — clearance/citizenship/ITAR, region-locked onsite without relocation, region-restricted remote roles (e.g. "US only" for a non-US candidate), required languages the candidate doesn't speak, junior-only roles for a senior candidate.
- **Skill overlap (code, free)** — deterministic 0-100 score from literal token matching against the candidate's must-have + nice-to-have skills.
- **Semantic match (embedder, ~free after first run)** — cosine similarity between the resume vector and each JD vector, cached in `output/embeddings.sqlite`.
- **LLM 5-block eval (expensive)** — stack_fit, seniority_fit, location_fit, compensation, culture_fit, each with a 0-100 sub-score + a one-sentence evidence-backed note. Final score blends: `70% LLM + 30% skill_overlap`, capped at 69 when staffing-agency signal fires.

Ghost-job signals (staffing agency, off-platform apply, commission-only, vague future promise, multiple ongoing openings) are attached as soft flags — they never auto-reject but the LLM is instructed to lower the score and quote them by name in `red_flags`.

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

## Application tracking

The dashboard records outcomes beyond simple applied/skipped:

- **Stats cards** — total jobs, apply verdicts, tracked applications, recorded outcomes
- **Follow-up queue** — applied jobs older than 10 days with no outcome recorded surface automatically, each with a one-click **Record outcome** button
- **Outcome modal** — pick from `interviewing` · `offer` · `rejected` · `ghosted` · `withdrawn` · `hired`, add an optional note; outcomes persist in `output/status.json` with full stage history

## Token efficiency

The pipeline is designed to minimize LLM token spend without sacrificing match quality:

| Lever | How | Savings |
|---|---|---|
| **Semantic pre-ranking** | Embed cosine(resume, JD) for every survivor in the docker sidecar, only send the top 30 to the LLM (was 100) | ~70% input tokens |
| **Resume fingerprint** | Replace the 1.5k-token full resume in every LLM call with a 200-token structured fingerprint (roles + skills + seniority + years + languages + regions) | ~85% per-call |
| **Analysis cache** | Hash each JD by URL + content; skip the LLM entirely for jobs already scored in a prior run | ~100% on re-runs |
| **Skill pre-filter** | Drop postings with zero stack overlap before any tokens are spent | ~80% of fetched jobs dropped |

Re-runs with no changes to jobs or resume are effectively free.

## Stack

- **Python 3.14 + FastAPI** — pipeline orchestration and API (host)
- **Docker sidecar** — `fastembed` + ONNX Runtime + `MiniLM-L6-v2` (384-dim) embeddings
- **sqlite-vec** — vector storage + cosine search inside SQLite (`output/embeddings.sqlite`)
- **Structured feeds (stdlib `urllib`)** — dated job discovery, no crawler/API key
- **Configurable AI CLI** — `claude`, `codex`, or `opencode` for resume analysis, scoring, and cover letters (set via `AI_CLI`)
- **Vanilla JS + Tailwind** — zero-build single-file UI

## Configuration

All optional, via `.env` or environment:

| Var | Default | Meaning |
|---|---|---|
| `AI_CLI` | `claude` | Which CLI drives analysis: `claude`, `codex`, `opencode` |
| `MAX_JOB_AGE_DAYS` | `30` | Freshness cutoff — jobs older than this are dropped |
| `MAX_JOBS_TO_ANALYZE` | `100` | Round-robin cap when sidecar is down |
| `SEMANTIC_ANALYSIS_CAP` | `30` | Cap when sidecar is up (semantic-ranked top-N) |
| `SOURCES` | all defaults | Comma-separated subset, e.g. `linkedin,ashby,greenhouse` |
| `LINKEDIN_MAX_PAGES` | `2` | Pages per LinkedIn query; keep conservative |
| `SOURCE_CACHE_HOURS` | `6` | Public-page cache lifetime |
| `GREENHOUSE_COMPANIES` | empty | Additional board slugs, comma-separated |
| `LEVER_COMPANIES` | empty | Additional company slugs, comma-separated |
| `ASHBY_COMPANIES` | empty | Additional board slugs, comma-separated |
| `EMBEDDER_URL` | `http://localhost:8787` | Sidecar endpoint |
| `USE_SEMANTIC_RANKING` | `true` | Toggle semantic pre-ranking |
| `USE_ANALYSIS_CACHE` | `true` | Skip LLM for jobs already scored in a prior run |

## Tests

```bash
make test
```

## Project structure

```
finder.py         # discovery/scoring pipeline + on-demand cached cover letters
sources.py        # structured feeds, caching, dates, normalization, diagnostics
matching.py       # skill scoring, hard-reject, region + language + ghost-job detection
embedding.py      # sidecar HTTP client + sqlite-vec cache (semantic ranking)
ai_cli.py         # configurable CLI runner (claude/codex/opencode) + JSON extraction
resume.py         # PDF → Markdown resume extraction
server.py         # FastAPI jobs, funnel report, tracking, cover letters, SSE run
ui/index.html     # single-file dashboard
embedder/         # docker sidecar (FastAPI + fastembed + ONNX MiniLM)
├── app.py        # /health, /embed endpoints
├── Dockerfile    # python:3.14-slim base
└── requirements.txt
prompts/          # AI prompt files for each step
docker-compose.yml# sidecar lifecycle (model volume, health check)
CLAUDE.md         # agent context
test_*.py         # unit tests (feeds, CLI runner, pipeline, embeddings, API mocked)
```

## License

MIT — see [LICENSE](LICENSE).
