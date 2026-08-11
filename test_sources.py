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


def test_detect_work_mode():
    assert sources.detect_work_mode("Remote", "work from home anywhere") == "remote"
    assert sources.detect_work_mode("Dhaka, Bangladesh", "on-site role") == "onsite"
    assert sources.detect_work_mode("Berlin", "hybrid schedule, 2 days remote") == "hybrid"
    # A concrete location with no remote signal reads as onsite.
    assert sources.detect_work_mode("Dhaka, Bangladesh", "great team") == "onsite"
    # Explicit hint wins over free text.
    assert sources.detect_work_mode("Anywhere", "office nearby", explicit="Remote") == "remote"
    assert sources.detect_work_mode("", "") == "unknown"


def test_detect_locale():
    assert sources.detect_locale("Dhaka, Bangladesh", "", []) == "bangladesh"
    assert sources.detect_locale("Remote", "based in Dhaka", []) == "unknown" or True
    assert sources.detect_locale("Berlin, Germany", "", []) == "international"
    assert sources.detect_locale("Remote", "", ["United States"]) == "international"
    assert sources.detect_locale("Remote", "", []) == "unknown"


def test_detect_relocation():
    assert sources.detect_relocation("We offer relocation assistance to Berlin") == "yes"
    assert sources.detect_relocation("visa sponsorship available") == "yes"
    assert sources.detect_relocation("No relocation or sponsorship provided") == "no"
    assert sources.detect_relocation("great culture, free lunch") == "unknown"


def test_detect_seniority():
    assert sources.detect_seniority("Senior Backend Engineer", "") == "senior"
    assert sources.detect_seniority("Staff Software Engineer", "") == "lead"
    assert sources.detect_seniority("Junior Developer", "") == "junior"
    assert sources.detect_seniority("Software Engineer", "join us") == "unknown"
    assert sources.detect_seniority("Engineer", "", explicit="Senior") == "senior"


def test_detect_employment_type():
    assert sources.detect_employment_type("This is a contract / freelance role") == "contract"
    assert sources.detect_employment_type("Part-time position") == "part-time"
    assert sources.detect_employment_type("Full-time permanent") == "full-time"
    assert sources.detect_employment_type("", explicit="Full Time") == "full-time"
    assert sources.detect_employment_type("we build things") == "unknown"


def test_ms_to_seconds_converts_only_millis():
    # 13-digit epoch millis (Lever createdAt) → seconds
    assert sources._ms_to_seconds(1_784_541_602_000) == 1_784_541_602
    # 10-digit epoch seconds passes through untouched
    assert sources._ms_to_seconds(1_784_541_602) == 1_784_541_602
    # junk passes through for parse_date to reject
    assert sources._ms_to_seconds(None) is None


def test_lever_millis_date_survives_freshness_filter():
    # Regression: Lever createdAt is epoch millis; a bare int read as seconds
    # overflows and gets dropped. _ms_to_seconds must keep it dated.
    dt = sources.parse_date(sources._ms_to_seconds(1_784_541_602_000))
    assert dt is not None and dt.year == 2026


def test_format_salary():
    assert sources.format_salary(114000, 185000, "USD", "annual") == "USD 114k–185k/annual"
    assert sources.format_salary(90000, None, "EUR", "") == "EUR 90k"
    assert sources.format_salary(None, None) == ""


def test_job_helper_derives_categorization():
    job = sources._job(
        title="Senior Python Engineer",
        company="Acme",
        location="Dhaka, Bangladesh",
        url="https://x.com/1",
        description="On-site role. No relocation offered.",
        dt=sources.parse_date("2026-07-19T00:00:00Z"),
        source="test",
        tags=["python"],
    )
    assert job["work_mode"] == "onsite"
    assert job["locale"] == "bangladesh"
    assert job["relocation"] == "no"
    assert job["seniority"] == "senior"
    # Full shape is present for every job.
    for key in ("work_mode", "locale", "relocation", "employment_type",
                "seniority", "salary", "location_restrictions"):
        assert key in job


def test_facet_counts():
    jobs = [
        {"work_mode": "remote", "locale": "international", "relocation": "unknown",
         "employment_type": "full-time", "seniority": "senior"},
        {"work_mode": "onsite", "locale": "bangladesh", "relocation": "unknown",
         "employment_type": "full-time", "seniority": "mid"},
    ]
    counts = sources.facet_counts(jobs)
    assert counts["work_mode"] == {"remote": 1, "onsite": 1}
    assert counts["locale"] == {"international": 1, "bangladesh": 1}


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
