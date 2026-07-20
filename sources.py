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
    }

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


def _job(title, company, location, url, description, dt, source, tags):
    iso, epoch = _norm(dt)
    return {
        "title": (title or "").strip(),
        "company": (company or "").strip(),
        "location": (location or "Remote").strip() or "Remote",
        "url": (url or "").strip(),
        "description": strip_html(description or ""),
        "posted_date": iso,
        "posted_epoch": epoch,
        "source": source,
        "tags": [str(t).strip() for t in (tags or []) if str(t).strip()],
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


# ── registry ──────────────────────────────────────────────
ALL_SOURCES = {
    "remoteok": fetch_remoteok,
    "remotive": fetch_remotive,
    "arbeitnow": fetch_arbeitnow,
    "weworkremotely": fetch_weworkremotely,
    "hackernews": fetch_hackernews,
}

# Enabled by default. HN is opt-in (noisy, freeform) — add it via SOURCES env.
DEFAULT_SOURCES = ["remoteok", "remotive", "arbeitnow", "weworkremotely"]


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
