from datetime import datetime, timezone

import sources


def test_parse_date_handles_epoch_iso_and_rfc822():
    epoch = sources.parse_date(1784541602)
    assert epoch is not None and epoch.tzinfo is not None

    iso = sources.parse_date("2026-07-19T11:07:46+00:00")
    assert iso.year == 2026 and iso.month == 7 and iso.day == 19

    iso_z = sources.parse_date("2026-07-19T11:07:46Z")
    assert iso_z.tzinfo is not None

    rss = sources.parse_date("Sat, 19 Jul 2026 11:07:46 +0000")
    assert rss.year == 2026 and rss.day == 19


def test_parse_date_returns_none_for_junk():
    assert sources.parse_date("") is None
    assert sources.parse_date(None) is None
    assert sources.parse_date("not a date") is None


def test_strip_html_removes_tags_and_unescapes():
    out = sources.strip_html("<p>Hello&amp; <b>world</b></p>\n\n  spaced</p>")
    assert "<" not in out and ">" not in out
    assert "Hello&" in out
    assert "  " not in out  # whitespace collapsed


def test_filter_recent_keeps_only_dated_jobs_within_window():
    now = int(datetime(2026, 7, 20, tzinfo=timezone.utc).timestamp())
    day = 86400
    jobs = [
        {"url": "a", "posted_epoch": now - 5 * day},    # fresh
        {"url": "b", "posted_epoch": now - 29 * day},   # just inside
        {"url": "c", "posted_epoch": now - 31 * day},   # too old
        {"url": "d", "posted_epoch": 0},                # undated -> dropped
    ]
    kept = {j["url"] for j in sources.filter_recent(jobs, 30, now)}
    assert kept == {"a", "b"}


def test_filter_keywords_matches_title_tags_description():
    jobs = [
        {"title": "Senior Python Engineer", "tags": [], "description": ""},
        {"title": "Sales Rep", "tags": ["crm"], "description": "quota driven"},
        {"title": "Engineer", "tags": ["django"], "description": "build APIs"},
        {"title": "Designer", "tags": [], "description": "We use FastAPI heavily"},
    ]
    kept = sources.filter_keywords(jobs, ["python", "django", "fastapi"])
    titles = {j["title"] for j in kept}
    assert titles == {"Senior Python Engineer", "Engineer", "Designer"}


def test_filter_keywords_empty_list_keeps_all():
    jobs = [{"title": "x", "tags": [], "description": ""}]
    assert sources.filter_keywords(jobs, []) == jobs


def test_fetch_all_dedupes_by_url_and_isolates_failures(monkeypatch):
    def good():
        return [
            {"url": "https://x.com/1", "title": "a"},
            {"url": "https://x.com/2", "title": "b"},
        ]

    def dupe():
        return [{"url": "https://x.com/1", "title": "dup"}]

    def broken():
        raise RuntimeError("network down")

    monkeypatch.setattr(sources, "ALL_SOURCES", {
        "good": good, "dupe": dupe, "broken": broken,
    })
    jobs = sources.fetch_all(["good", "dupe", "broken"])
    urls = [j["url"] for j in jobs]
    assert urls == ["https://x.com/1", "https://x.com/2"]  # dup + failure survived
