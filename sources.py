# sources.py
"""Structured job feeds with real, per-posting dates.

Unlike generic web-search + scrape (Firecrawl/Crawl4AI), every source here
returns jobs with a machine-readable posting date, so freshness can be
filtered *deterministically* in code — before spending any AI tokens — rather
than guessed by an LLM from page text. That is the whole reason recent-job
discovery works reliably here.

Each source returns a list of normalized job dicts:

    {
        "title":        str,
        "company":      str,
        "location":     str,
        "url":          str,
        "description":  str,   # plain text, truncated
        "posted_date":  str,   # ISO 8601, UTC
        "posted_epoch": int,   # unix seconds (0 if unknown)
        "source":       str,   # e.g. "remoteok"
        "tags":         list[str],

        # ── categorization (derived in code, see categorize()) ──
        "work_mode":    str,   # "remote" | "onsite" | "hybrid" | "unknown"
        "locale":       str,   # "bangladesh" | "international" | "unknown"
        "relocation":   str,   # "yes" | "no" | "unknown"
        "employment_type": str,# "full-time" | "contract" | "part-time" | "internship" | "unknown"
        "seniority":    str,   # "junior" | "mid" | "senior" | "lead" | "unknown"
        "salary":       str,   # human-readable range, or "" if none published
        "location_restrictions": list[str],  # regions the role is limited to
    }

Most feeds are remote-only. LinkedIn (opt-in) is the source of *onsite* and
Bangladesh-local postings, which is why the categorization below distinguishes
work mode and locale rather than assuming everything is remote.

All sources are free and require no API key. A source that fails (network,
schema drift) logs a warning and returns [] — one bad feed never sinks a run.
"""
from __future__ import annotations

import html
import json
import re
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

USER_AGENT = "ai-job-finder/1.0 (+https://github.com/ofsazib)"
FETCH_TIMEOUT = 30
DESCRIPTION_MAX = 1500


# ── http ──────────────────────────────────────────────────
def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
        return resp.read()


def _get_json(url: str):
    return json.loads(_get(url).decode("utf-8", errors="replace"))


# ── text / date helpers ───────────────────────────────────
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def strip_html(text: str) -> str:
    """Turn an HTML fragment into a compact plain-text snippet."""
    if not text:
        return ""
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text).strip()
    return text[:DESCRIPTION_MAX]


def parse_date(value) -> datetime | None:
    """Parse epoch ints, ISO 8601, or RFC-822 (RSS) into a UTC datetime."""
    if value is None or value == "":
        return None
    # epoch seconds (int or numeric string)
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
        try:
            return datetime.fromtimestamp(int(value), tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    # ISO 8601 (tolerate trailing Z)
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    # RFC 822 (RSS pubDate)
    try:
        dt = parsedate_to_datetime(text)
        if dt is not None:
            return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        pass
    return None


def _norm(dt: datetime | None):
    """(iso_string, epoch_int) for a datetime, or ("", 0)."""
    if dt is None:
        return "", 0
    return dt.isoformat(), int(dt.timestamp())


# ── categorization ────────────────────────────────────────
# These derive structured facets from free-text so the UI can filter and the
# analyzer can reason about them. Everything is best-effort with an explicit
# "unknown" fallback — we never guess a value we can't support from the text.

BD_TERMS = ("bangladesh", "dhaka", "chittagong", "chattogram", "sylhet",
            "khulna", "rajshahi", " bd ", "bd,", ",bd")

_HYBRID_RE = re.compile(r"\bhybrid\b", re.I)
_ONSITE_RE = re.compile(r"\b(on[\s-]?site|in[\s-]?office|in[\s-]?person)\b", re.I)
_REMOTE_RE = re.compile(r"\b(remote|work from home|wfh|distributed|anywhere)\b", re.I)

_RELOCATION_YES_RE = re.compile(
    r"(relocation (assistance|support|package|available|provided|offered)"
    r"|will relocate|visa sponsor|sponsorship (available|provided|offered)"
    r"|we sponsor|help you relocate)", re.I)
_RELOCATION_NO_RE = re.compile(
    r"(no relocation|no visa sponsor|without sponsorship"
    r"|relocation is not|cannot sponsor|no sponsorship)", re.I)

_SENIORITY_PATTERNS = [
    ("lead", re.compile(r"\b(lead|principal|staff|head of|director|architect)\b", re.I)),
    ("senior", re.compile(r"\b(senior|sr\.?|lead)\b", re.I)),
    ("junior", re.compile(r"\b(junior|jr\.?|entry[\s-]?level|graduate|intern)\b", re.I)),
]

_EMPLOYMENT_PATTERNS = [
    ("internship", re.compile(r"\bintern(ship)?\b", re.I)),
    ("contract", re.compile(r"\b(contract|freelance|contractor|b2b)\b", re.I)),
    ("part-time", re.compile(r"\bpart[\s-]?time\b", re.I)),
    ("full-time", re.compile(r"\bfull[\s-]?time\b", re.I)),
]


def detect_work_mode(location: str, description: str, explicit: str = "") -> str:
    """remote / onsite / hybrid / unknown from an explicit hint + free text."""
    if explicit:
        e = explicit.lower()
        if "hybrid" in e:
            return "hybrid"
        if "remote" in e:
            return "remote"
        if any(k in e for k in ("onsite", "on-site", "on site", "office")):
            return "onsite"
    blob = f"{location} {description}"
    if _HYBRID_RE.search(blob):
        return "hybrid"
    if _REMOTE_RE.search(blob) and not _ONSITE_RE.search(blob):
        return "remote"
    if _ONSITE_RE.search(blob):
        return "onsite"
    # A concrete city/country in location with no remote signal reads as onsite.
    if location and not _REMOTE_RE.search(location) and location.lower() != "remote":
        return "onsite"
    return "unknown"


def detect_locale(location: str, description: str, restrictions: list[str]) -> str:
    """bangladesh vs international vs unknown."""
    blob = f" {location} {' '.join(restrictions or [])} ".lower()
    if any(term in blob for term in BD_TERMS):
        return "bangladesh"
    if location and location.strip().lower() not in ("", "remote", "anywhere", "worldwide"):
        return "international"
    if restrictions:
        return "international"
    return "unknown"


def detect_relocation(description: str) -> str:
    """yes / no / unknown — does the posting mention relocation or visa help?"""
    if _RELOCATION_NO_RE.search(description):
        return "no"
    if _RELOCATION_YES_RE.search(description):
        return "yes"
    return "unknown"


def detect_seniority(title: str, description: str, explicit: str = "") -> str:
    if explicit:
        e = explicit.lower()
        for level in ("lead", "principal", "staff"):
            if level in e:
                return "lead"
        if "senior" in e:
            return "senior"
        if any(k in e for k in ("junior", "entry", "intern", "graduate")):
            return "junior"
        if "mid" in e:
            return "mid"
    text = f"{title} {description[:400]}"
    for level, pat in _SENIORITY_PATTERNS:
        if pat.search(text):
            return level
    return "unknown"


def detect_employment_type(description: str, explicit: str = "") -> str:
    if explicit:
        e = explicit.lower().replace("_", " ")
        for kind, _ in _EMPLOYMENT_PATTERNS:
            if kind.replace("-", " ") in e or kind.replace("-", "") in e:
                return kind
    for kind, pat in _EMPLOYMENT_PATTERNS:
        if pat.search(description):
            return kind
    return "unknown"


def format_salary(min_s=None, max_s=None, currency="", period="") -> str:
    """Human-readable salary range, or "" if nothing usable."""
    if not min_s and not max_s:
        return ""
    cur = (currency or "").upper()
    per = f"/{period}" if period else ""

    def fmt(n):
        try:
            n = int(n)
        except (TypeError, ValueError):
            return None
        return f"{n // 1000}k" if n >= 10000 else str(n)

    lo, hi = fmt(min_s), fmt(max_s)
    if lo and hi and lo != hi:
        body = f"{lo}–{hi}"
    else:
        body = lo or hi
    if not body:
        return ""
    return f"{cur} {body}{per}".strip()


def _job(title, company, location, url, description, dt, source, tags,
         *, work_mode="", locale="", relocation="", employment_type="",
         seniority="", salary="", location_restrictions=None):
    """Build a normalized job dict, deriving any categorization not supplied.

    Sources pass whatever structured fields they have (e.g. Himalayas gives
    seniority + salary + locationRestrictions directly); everything else is
    inferred from the title/location/description so all jobs share one shape.
    """
    iso, epoch = _norm(dt)
    location = (location or "Remote").strip() or "Remote"
    description = strip_html(description or "")
    restrictions = [str(r).strip() for r in (location_restrictions or []) if str(r).strip()]

    return {
        "title": (title or "").strip(),
        "company": (company or "").strip(),
        "location": location,
        "url": (url or "").strip(),
        "description": description,
        "posted_date": iso,
        "posted_epoch": epoch,
        "source": source,
        "tags": [str(t).strip() for t in (tags or []) if str(t).strip()],
        "work_mode": work_mode or detect_work_mode(location, description),
        "locale": locale or detect_locale(location, description, restrictions),
        "relocation": relocation or detect_relocation(description),
        "employment_type": employment_type or detect_employment_type(description),
        "seniority": seniority or detect_seniority(title or "", description),
        "salary": salary or "",
        "location_restrictions": restrictions,
    }


# ── individual sources ────────────────────────────────────
def fetch_remoteok() -> list[dict]:
    """RemoteOK — https://remoteok.com/api (first array item is a legal notice)."""
    data = _get_json("https://remoteok.com/api")
    jobs = []
    for item in data:
        if not isinstance(item, dict) or "position" not in item:
            continue  # legal-notice header
        jobs.append(_job(
            title=item.get("position"),
            company=item.get("company"),
            location=item.get("location") or "Remote",
            url=item.get("url"),
            description=item.get("description"),
            dt=parse_date(item.get("epoch") or item.get("date")),
            source="remoteok",
            tags=item.get("tags"),
        ))
    return jobs


def fetch_remotive() -> list[dict]:
    """Remotive — https://remotive.com/api/remote-jobs."""
    data = _get_json("https://remotive.com/api/remote-jobs")
    jobs = []
    for item in data.get("jobs", []):
        jobs.append(_job(
            title=item.get("title"),
            company=item.get("company_name"),
            location=item.get("candidate_required_location") or "Remote",
            url=item.get("url"),
            description=item.get("description"),
            dt=parse_date(item.get("publication_date")),
            source="remotive",
            tags=item.get("tags"),
        ))
    return jobs


def fetch_arbeitnow() -> list[dict]:
    """Arbeitnow — https://www.arbeitnow.com/api/job-board-api."""
    data = _get_json("https://www.arbeitnow.com/api/job-board-api")
    jobs = []
    for item in data.get("data", []):
        loc = item.get("location") or ("Remote" if item.get("remote") else "")
        jobs.append(_job(
            title=item.get("title"),
            company=item.get("company_name"),
            location=loc,
            url=item.get("url"),
            description=item.get("description"),
            dt=parse_date(item.get("created_at")),
            source="arbeitnow",
            tags=item.get("tags"),
        ))
    return jobs


# We Work Remotely publishes one RSS feed per category.
WWR_FEEDS = {
    "backend": "https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss",
    "fullstack": "https://weworkremotely.com/categories/remote-full-stack-programming-jobs.rss",
    "frontend": "https://weworkremotely.com/categories/remote-front-end-programming-jobs.rss",
    "devops": "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
}


def fetch_weworkremotely() -> list[dict]:
    """We Work Remotely — RSS feeds (pubDate per item)."""
    jobs = []
    seen = set()
    for feed_url in WWR_FEEDS.values():
        try:
            root = ElementTree.fromstring(_get(feed_url))
        except (ElementTree.ParseError, OSError) as e:
            print(f"  [sources] weworkremotely feed failed: {e}")
            continue
        for item in root.iter("item"):
            link = (item.findtext("link") or "").strip()
            if not link or link in seen:
                continue
            seen.add(link)
            # WWR titles are "Company: Job Title"
            raw_title = (item.findtext("title") or "").strip()
            company, _, title = raw_title.partition(":")
            if not title:
                company, title = "", raw_title
            region = item.findtext("{https://weworkremotely.com/}region") or "Remote"
            jobs.append(_job(
                title=title.strip() or raw_title,
                company=company.strip(),
                location=region,
                url=link,
                description=item.findtext("description"),
                dt=parse_date(item.findtext("pubDate")),
                source="weworkremotely",
                tags=[],
            ))
    return jobs


def fetch_hackernews() -> list[dict]:
    """HN 'Who is hiring' comments via the Algolia API (created_at_i per comment).

    Comments are freeform, so title/company are left blank and the whole
    comment becomes the description for the analyzer to read. Only the current
    month's threads matter for freshness, and the date filter handles that.
    """
    url = (
        "https://hn.algolia.com/api/v1/search_by_date"
        "?tags=comment&query=remote&hitsPerPage=100"
    )
    try:
        data = _get_json(url)
    except (OSError, ValueError) as e:
        print(f"  [sources] hackernews failed: {e}")
        return []
    jobs = []
    for hit in data.get("hits", []):
        text = strip_html(hit.get("comment_text") or "")
        if len(text) < 80:  # skip one-liners / meta chatter
            continue
        jobs.append(_job(
            title="(HN Who-is-hiring post)",
            company="",
            location="",
            url=f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
            description=text,
            dt=parse_date(hit.get("created_at_i") or hit.get("created_at")),
            source="hackernews",
            tags=[],
        ))
    return jobs


def fetch_himalayas() -> list[dict]:
    """Himalayas — https://himalayas.app/jobs/api.

    The richest feed: ships seniority, salary, employmentType, and
    locationRestrictions directly, so most categorization is exact rather than
    inferred. Returns the most recent postings first.
    """
    data = _get_json("https://himalayas.app/jobs/api?limit=100")
    jobs = []
    for item in data.get("jobs", []):
        restrictions = item.get("locationRestrictions") or []
        location = ", ".join(restrictions) if restrictions else "Remote"
        salary = format_salary(
            item.get("minSalary"), item.get("maxSalary"),
            item.get("currency"), item.get("salaryPeriod"),
        )
        sen = item.get("seniority") or []
        jobs.append(_job(
            title=item.get("title"),
            company=item.get("companyName"),
            location=location,
            url=item.get("applicationLink") or item.get("guid") or item.get("url"),
            description=item.get("description") or item.get("excerpt"),
            dt=parse_date(item.get("pubDate") or item.get("publishedDate") or item.get("updatedAt")),
            source="himalayas",
            tags=item.get("categories"),
            # Himalayas is remote-first; every listing here is a remote role.
            work_mode="remote",
            seniority=detect_seniority("", "", sen[0] if sen else ""),
            employment_type=detect_employment_type("", item.get("employmentType") or ""),
            salary=salary,
            location_restrictions=restrictions,
        ))
    return jobs


def fetch_jobicy() -> list[dict]:
    """Jobicy — https://jobicy.com/api/v2/remote-jobs (remote-first)."""
    data = _get_json("https://jobicy.com/api/v2/remote-jobs?count=100")
    jobs = []
    for item in data.get("jobs", []):
        geo = item.get("jobGeo") or "Remote"
        jtype = item.get("jobType") or []
        level = item.get("jobLevel") or ""
        jobs.append(_job(
            title=item.get("jobTitle"),
            company=item.get("companyName"),
            location=geo,
            url=item.get("url"),
            description=item.get("jobExcerpt") or item.get("jobDescription"),
            dt=parse_date(item.get("pubDate")),
            source="jobicy",
            tags=item.get("jobIndustry"),
            work_mode="remote",
            seniority=detect_seniority("", "", level),
            employment_type=detect_employment_type("", jtype[0] if jtype else ""),
            location_restrictions=[geo] if geo and geo.lower() != "anywhere" else [],
        ))
    return jobs


def fetch_workingnomads() -> list[dict]:
    """Working Nomads — https://www.workingnomads.com/api/exposed_jobs/ (remote)."""
    data = _get_json("https://www.workingnomads.com/api/exposed_jobs/")
    jobs = []
    for item in data if isinstance(data, list) else data.get("jobs", []):
        jobs.append(_job(
            title=item.get("title"),
            company=item.get("company_name"),
            location=item.get("location") or "Remote",
            url=item.get("url"),
            description=item.get("description"),
            dt=parse_date(item.get("pub_date") or item.get("created")),
            source="workingnomads",
            tags=[t.strip() for t in (item.get("category_name") or "").split(",") if t.strip()],
            work_mode="remote",
        ))
    return jobs


# LinkedIn: public guest jobs endpoint returns HTML job cards (no auth, no
# key). This is the ONLY source of onsite + Bangladesh-local postings, which is
# why it's worth the fragility. Opt-in via SOURCES because LinkedIn rate-limits
# by IP and can block — a failure here must never sink a normal run.
LINKEDIN_GUEST_URL = (
    "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
)
# (keywords, location) pairs. Bangladesh pulls local onsite/hybrid roles;
# the "Remote" pass pulls international remote roles LinkedIn indexes.
LINKEDIN_SEARCHES = [
    ("software engineer python", "Bangladesh"),
    ("backend developer", "Bangladesh"),
    ("software engineer python remote", "Worldwide"),
]
_LI_CARD_RE = re.compile(r'<li>.*?</li>', re.S)


def _linkedin_field(card: str, pattern: str) -> str:
    m = re.search(pattern, card, re.S)
    return strip_html(m.group(1)).strip() if m else ""


def fetch_linkedin() -> list[dict]:
    """LinkedIn guest jobs — onsite + Bangladesh-local roles (opt-in, fragile).

    Parses the public HTML job-card fragment. Each search pass targets a
    (keywords, location) pair; results are deduped by posting URL upstream in
    fetch_all(). Any pass that fails is logged and skipped.
    """
    from urllib.parse import quote

    jobs: list[dict] = []
    for keywords, location in LINKEDIN_SEARCHES:
        url = f"{LINKEDIN_GUEST_URL}?keywords={quote(keywords)}&location={quote(location)}&f_TPR=r2592000&start=0"
        try:
            html_text = _get(url).decode("utf-8", errors="replace")
        except OSError as e:
            print(f"  [sources] linkedin '{keywords}/{location}' failed: {e}")
            continue
        for card in _LI_CARD_RE.findall(html_text):
            link = _linkedin_field(card, r'base-card__full-link[^>]*href="([^"?]+)')
            title = _linkedin_field(card, r'base-search-card__title">(.*?)</')
            company = _linkedin_field(card, r'base-search-card__subtitle">(.*?)</')
            loc = _linkedin_field(card, r'job-search-card__location">(.*?)</') or location
            dt_raw = _linkedin_field(card, r'datetime="([^"]+)"')
            if not (link and title):
                continue
            jobs.append(_job(
                title=title,
                company=company,
                location=loc,
                url=link,
                description=f"{title} at {company}. Location: {loc}. (LinkedIn listing — open for full details.)",
                dt=parse_date(dt_raw),
                source="linkedin",
                tags=[],
            ))
    return jobs


# ── registry ──────────────────────────────────────────────
ALL_SOURCES = {
    "remoteok": fetch_remoteok,
    "remotive": fetch_remotive,
    "arbeitnow": fetch_arbeitnow,
    "weworkremotely": fetch_weworkremotely,
    "himalayas": fetch_himalayas,
    "jobicy": fetch_jobicy,
    "workingnomads": fetch_workingnomads,
    "hackernews": fetch_hackernews,
    "linkedin": fetch_linkedin,
}

# Enabled by default: the free, reliable, structured remote feeds.
# Opt-in via SOURCES env (noisy or fragile):
#   hackernews — freeform "Who is hiring" comments
#   linkedin   — onsite + Bangladesh-local, but HTML-scraped and rate-limited
DEFAULT_SOURCES = [
    "remoteok", "remotive", "arbeitnow", "weworkremotely",
    "himalayas", "jobicy", "workingnomads",
]


def fetch_all(source_names: list[str] | None = None) -> list[dict]:
    """Fetch and merge jobs from the named sources (deduped by URL).

    Each source is isolated: a failure logs and yields nothing rather than
    aborting the run.
    """
    names = source_names or DEFAULT_SOURCES
    jobs: list[dict] = []
    seen: set[str] = set()
    for name in names:
        fetcher = ALL_SOURCES.get(name)
        if not fetcher:
            print(f"  [sources] unknown source '{name}' — skipping")
            continue
        try:
            fetched = fetcher()
        except Exception as e:  # noqa: BLE001 — never let one feed sink the run
            print(f"  [sources] {name} failed: {e}")
            continue
        added = 0
        for job in fetched:
            url = job["url"]
            if not url or url in seen:
                continue
            seen.add(url)
            jobs.append(job)
            added += 1
        print(f"  [sources] {name}: {added} job(s)")
    return jobs


# ── filtering ─────────────────────────────────────────────
# This is the core fix over generic search+scrape: freshness is decided in
# code from a real per-posting date, not guessed by an LLM from page text.

def filter_recent(jobs: list[dict], max_age_days: int, now_epoch: int | None = None) -> list[dict]:
    """Keep only jobs posted within `max_age_days`.

    Jobs with no usable date (posted_epoch == 0) are DROPPED — the whole point
    is that we only trust dated postings. `now_epoch` defaults to the current
    time; tests pass it explicitly so freshness checks stay deterministic.
    """
    if now_epoch is None:
        now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    cutoff = now_epoch - max_age_days * 86400
    return [j for j in jobs if j.get("posted_epoch", 0) >= cutoff]


def _keyword_pattern(keywords: list[str]):
    """Compile keywords into one whole-word regex.

    Word boundaries matter: a substring match makes "go" hit "goal"/"Portugal"
    and "aws" hit unrelated words, which floods the analyzer with junk. We match
    each keyword as a whole token instead. Boundaries are built from non-word
    edges so tech tokens with dots/pluses (e.g. "node.js", "c++") still match.
    """
    parts = []
    for kw in keywords:
        kw = kw.strip().lower()
        if not kw:
            continue
        esc = re.escape(kw)
        parts.append(rf"(?<![a-z0-9]){esc}(?![a-z0-9])")
    if not parts:
        return None
    return re.compile("|".join(parts), re.IGNORECASE)


def filter_keywords(jobs: list[dict], keywords: list[str]) -> list[dict]:
    """Cheap relevance pre-filter before the AI scoring step.

    Keeps a job if any keyword appears — as a whole word — in its title, tags,
    or description. This trims obviously-irrelevant postings (sales, design,
    non-tech) so the AI only scores plausible matches, saving tokens and time.
    An empty keyword list keeps everything.
    """
    pattern = _keyword_pattern(keywords)
    if pattern is None:
        return jobs
    kept = []
    for job in jobs:
        haystack = " ".join([
            job.get("title", ""),
            " ".join(job.get("tags", [])),
            job.get("description", ""),
        ])
        if pattern.search(haystack):
            kept.append(job)
    return kept


def facet_counts(jobs: list[dict]) -> dict:
    """Tally the categorization facets across a job list.

    Returns a dict of {facet: {value: count}} for work_mode, locale,
    relocation, employment_type, and seniority. Used to give the pipeline (and
    the run summary) a quick breakdown of what was discovered.
    """
    facets = ("work_mode", "locale", "relocation", "employment_type", "seniority")
    counts: dict = {f: {} for f in facets}
    for job in jobs:
        for f in facets:
            v = job.get(f, "unknown") or "unknown"
            counts[f][v] = counts[f].get(v, 0) + 1
    return counts
