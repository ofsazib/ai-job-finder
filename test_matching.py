import matching


def test_evaluate_preferences_rejects_avoided_contract():
    job = {"employment_type": "contract", "description": "rotation on call"}
    result = matching.evaluate_preferences(
        job, {"employment_types": ["full-time"]}
    )
    assert result == {
        "hard_rejects": ["employment_type:contract"],
        "adjustment": 0,
        "reasons": [],
    }


def test_evaluate_preferences_rewards_preferred_domain_and_mode():
    result = matching.evaluate_preferences(
        {"description": "healthtech platform", "work_mode": "remote"},
        {"preferred_domains": ["healthtech"], "work_modes": ["remote"]},
    )
    assert result["adjustment"] == 4
    assert len(result["reasons"]) == 2


def test_evaluate_preferences_defaults_are_neutral():
    assert matching.evaluate_preferences({}, {}) == {
        "hard_rejects": [], "adjustment": 0, "reasons": []
    }


# ── skill_overlap_score ───────────────────────────────────
def test_skill_overlap_full_match_must_have():
    job = {"title": "Backend Engineer", "tags": ["python", "django"],
           "description": "Build APIs with FastAPI and Postgres"}
    must = ["python", "django", "fastapi", "postgres"]
    score = matching.skill_overlap_score(job, must, [])
    # All 4 must-haves hit → 4/4 * 70 = 70 + baseline 10 = 80
    assert score == 80


def test_skill_overlap_partial_match():
    job = {"title": "Backend Engineer", "tags": ["python"],
           "description": "Build APIs"}
    must = ["python", "django", "fastapi", "postgres"]
    score = matching.skill_overlap_score(job, must, [])
    # 1/4 * 70 = 17.5 → 17, + baseline 10 = 27
    assert score == 27


def test_skill_overlap_zero_match():
    job = {"title": "Frontend Engineer", "tags": ["react"],
           "description": "React + CSS work"}
    must = ["python", "django"]
    score = matching.skill_overlap_score(job, must, [])
    # 0 must hits → coverage 0, baseline 0 → 0
    assert score == 0


def test_skill_overlap_with_nice_to_have():
    job = {"title": "Backend Engineer", "tags": ["python", "django", "go"],
           "description": "Postgres"}
    must = ["python", "django"]          # both hit → 2/2*70 = 70
    nice = ["go", "kubernetes", "graphql"]  # go hits → 1/3*20 ≈ 6.67 → 6
    score = matching.skill_overlap_score(job, must, nice)
    assert score == 70 + 6 + 10  # = 86


def test_skill_overlap_whole_token_not_substring():
    # "go" must not match inside "Portugal" or "goal"
    job = {"title": "Engineering Manager", "tags": [],
           "description": "Lead the team to achieve goals. Based in Portugal."}
    score = matching.skill_overlap_score(job, ["go"], [])
    assert score == 0


def test_skill_overlap_no_skills_returns_neutral():
    job = {"title": "Anything", "tags": [], "description": "stuff"}
    assert matching.skill_overlap_score(job, [], []) == 50


def test_skill_overlap_caps_at_100():
    # More hits than slots shouldn't overflow.
    job = {"title": "x", "tags": ["python"], "description": "python python python"}
    must = ["python"]
    nice = ["a", "b", "c", "d", "e", "f"]  # 6 nice, none hit
    score = matching.skill_overlap_score(job, must, nice)
    assert score <= 100
    # python hits: 1/1*70 + 0*20 + 10 = 80
    assert score == 80


# ── disqualifier_hits ─────────────────────────────────────
def test_disqualifier_detects_clearance():
    job = {"title": "Backend Engineer",
           "description": "Active security clearance required.",
           "location": "Remote"}
    hits = matching.disqualifier_hits(job)
    assert "security_clearance_required" in hits


def test_disqualifier_detects_us_citizen():
    job = {"title": "Engineer",
           "description": "Must be a U.S. citizen to apply.",
           "location": "Washington, DC"}
    hits = matching.disqualifier_hits(job)
    assert "us_citizen_only" in hits


def test_disqualifier_detects_itar():
    job = {"title": "Firmware Engineer",
           "description": "ITAR-controlled work, EAR compliance required.",
           "location": "Remote"}
    hits = matching.disqualifier_hits(job)
    assert "export_controlled" in hits


def test_disqualifier_detects_region_locked_no_relocation():
    job = {"title": "Engineer", "description": "great team",
           "location": "Berlin, Germany",
           "work_mode": "onsite", "locale": "international", "relocation": "no"}
    hits = matching.disqualifier_hits(job)
    assert "region_locked_no_relocation" in hits


def test_disqualifier_clean_job_has_no_hits():
    job = {"title": "Senior Python Engineer",
           "description": "Remote role, worldwide team, Python + Django.",
           "location": "Remote",
           "work_mode": "remote", "locale": "unknown", "relocation": "unknown"}
    assert matching.disqualifier_hits(job) == []


# ── hard_reject ───────────────────────────────────────────
def test_hard_reject_clearance():
    job = {"title": "Engineer", "description": "security clearance required",
           "location": "", "work_mode": "remote", "locale": "unknown",
           "relocation": "unknown"}
    rejected, hits = matching.hard_reject(job, "senior")
    assert rejected
    assert "security_clearance_required" in hits


def test_hard_reject_region_locked_onsite():
    job = {"title": "Engineer", "description": "great team",
           "location": "Berlin", "work_mode": "onsite",
           "locale": "international", "relocation": "no"}
    rejected, hits = matching.hard_reject(job, "senior")
    assert rejected
    assert "region_locked_no_relocation" in hits


def test_hard_reject_junior_role_for_senior_candidate():
    job = {"title": "Junior Backend Engineer", "description": "internship",
           "location": "Remote", "work_mode": "remote",
           "locale": "unknown", "relocation": "unknown"}
    rejected, hits = matching.hard_reject(job, "senior")
    assert rejected
    assert "junior_role_for_senior_candidate" in hits


def test_hard_reject_keeps_mid_role_for_senior():
    # Seniority gap is not extreme — junior flag would fire, mid does not.
    job = {"title": "Mid Backend Engineer", "description": "Python shop",
           "location": "Remote", "work_mode": "remote",
           "locale": "unknown", "relocation": "unknown"}
    rejected, hits = matching.hard_reject(job, "senior")
    assert not rejected
    assert hits == []


def test_hard_reject_keeps_junior_for_junior():
    job = {"title": "Junior Engineer", "description": "Python shop",
           "location": "Remote", "work_mode": "remote",
           "locale": "unknown", "relocation": "unknown"}
    rejected, hits = matching.hard_reject(job, "junior")
    assert not rejected


def test_hard_reject_remote_international_no_relocation_is_fine():
    # Remote role abroad without relocation is still viable for remote worker.
    job = {"title": "Backend Engineer", "description": "remote worldwide",
           "location": "Remote", "work_mode": "remote",
           "locale": "international", "relocation": "no"}
    rejected, _ = matching.hard_reject(job, "senior")
    assert not rejected


# ── region restriction detection ─────────────────────────
def test_region_detects_us_only_in_description():
    job = {"title": "Backend Engineer",
           "description": "Remote, but US only. Must be authorized to work in the US.",
           "location": "Remote", "location_restrictions": []}
    r = matching.detect_region_restrictions(job)
    assert any("us" in x or "united states" in x or "america" in x for x in r)


def test_region_detects_europe_only_in_description():
    job = {"title": "Backend Engineer",
           "description": "Europe only — must be based in EU.",
           "location": "Remote", "location_restrictions": []}
    r = matching.detect_region_restrictions(job)
    assert any("eu" in x or "europe" in x for x in r)


def test_region_detects_structured_location_restrictions_field():
    # Himalayas / Jobicy populate this. "United Kingdom" lock → restricted.
    job = {"title": "Backend Engineer", "description": "remote role",
           "location": "Remote",
           "location_restrictions": ["United Kingdom"]}
    r = matching.detect_region_restrictions(job)
    assert "united kingdom" in r


def test_region_worldwide_clears_restrictions():
    job = {"title": "Backend Engineer",
           "description": "Worldwide remote. Open to candidates anywhere.",
           "location": "Remote", "location_restrictions": ["United States"]}
    # Explicit worldwide override should clear the structured restriction.
    assert matching.detect_region_restrictions(job) == []


def test_region_no_restriction_when_open_remote():
    job = {"title": "Backend Engineer", "description": "fully remote role",
           "location": "Remote", "location_restrictions": []}
    assert matching.detect_region_restrictions(job) == []


def test_region_eligible_when_no_restrictions():
    job = {"title": "Backend Engineer", "description": "remote role",
           "location": "Remote", "location_restrictions": []}
    ok, reason = matching.is_region_eligible(job, ["bangladesh", "worldwide"])
    assert ok and reason == ""


def test_region_ineligible_for_us_only_remote_job():
    job = {"title": "Backend Engineer",
           "description": "Remote, US only. Must be authorized to work in the US.",
           "location": "Remote", "location_restrictions": []}
    ok, reason = matching.is_region_eligible(job, ["bangladesh", "worldwide"])
    assert not ok
    assert "restricted to" in reason


def test_region_ineligible_for_structured_uk_lock():
    job = {"title": "Backend Engineer", "description": "remote",
           "location": "Remote",
           "location_restrictions": ["United Kingdom"]}
    ok, reason = matching.is_region_eligible(job, ["bangladesh", "worldwide"])
    assert not ok


def test_region_eligible_when_candidate_in_restricted_region():
    # If candidate is in the UK, a UK-only role is fine.
    job = {"title": "Backend Engineer", "description": "remote",
           "location": "Remote",
           "location_restrictions": ["United Kingdom"]}
    ok, _ = matching.is_region_eligible(job, ["united kingdom"])
    assert ok


def test_hard_reject_drops_us_only_remote_for_bd_candidate():
    job = {"title": "Senior Backend Engineer",
           "description": "Remote, US only.",
           "location": "Remote", "work_mode": "remote",
           "locale": "international", "relocation": "unknown",
           "location_restrictions": []}
    rejected, hits = matching.hard_reject(
        job, "senior", candidate_regions=["bangladesh", "worldwide"])
    assert rejected
    assert any("region_restricted" in h for h in hits)


def test_hard_reject_keeps_worldwide_remote_for_bd_candidate():
    job = {"title": "Senior Backend Engineer",
           "description": "Worldwide remote role, hire anywhere.",
           "location": "Remote", "work_mode": "remote",
           "locale": "unknown", "relocation": "unknown",
           "location_restrictions": []}
    rejected, _ = matching.hard_reject(
        job, "senior", candidate_regions=["bangladesh", "worldwide"])
    assert not rejected


def test_hard_reject_keeps_bd_local_role_for_bd_candidate():
    job = {"title": "Backend Engineer", "description": "based in dhaka",
           "location": "Dhaka, Bangladesh", "work_mode": "onsite",
           "locale": "bangladesh", "relocation": "unknown",
           "location_restrictions": []}
    rejected, _ = matching.hard_reject(
        job, "senior", candidate_regions=["bangladesh", "worldwide"])
    assert not rejected


def test_region_must_be_based_in_germany():
    job = {"title": "Engineer",
           "description": "Must be based in Germany for this role.",
           "location": "Remote", "location_restrictions": []}
    r = matching.detect_region_restrictions(job)
    assert "germany" in r


# ── language requirement detection ───────────────────────
def test_language_detects_must_speak_french():
    job = {"title": "Engineer",
           "description": "Must speak French fluently for this client-facing role.",
           "location": "Montreal"}
    reqs = dict(matching.detect_language_requirements(job))
    assert reqs.get("french") == "required"


def test_language_detects_fluent_in_german():
    job = {"title": "Engineer",
           "description": "Fluent in German required for our Berlin office.",
           "location": "Berlin"}
    reqs = dict(matching.detect_language_requirements(job))
    assert reqs.get("german") == "required"


def test_language_detects_bilingual_in_title():
    # Title-level "Korean Bilingual" — current data showed this case.
    job = {"title": "Korean Bilingual Java Full-Stack Developer",
           "description": "Standard Java shop.",
           "location": "Santa Ana, CA"}
    reqs = dict(matching.detect_language_requirements(job))
    assert reqs.get("korean") == "required"


def test_language_no_requirement_for_english_only_shop():
    # English-mention without "fluent in" qualifier shouldn't fire hard reject.
    job = {"title": "Backend Engineer",
           "description": "We communicate in English across the team.",
           "location": "Remote"}
    assert matching.detect_language_requirements(job) == []


def test_language_eligible_when_no_requirements():
    job = {"title": "Backend Engineer", "description": "remote role",
           "location": "Remote"}
    ok, _ = matching.is_language_eligible(job, {"english": "fluent"})
    assert ok


def test_language_ineligible_for_french_required_when_candidate_lacks_it():
    job = {"title": "Backend Engineer",
           "description": "Must speak French fluently.",
           "location": "Montreal"}
    ok, reason = matching.is_language_eligible(job, {"english": "fluent"})
    assert not ok
    assert "french" in reason


def test_language_eligible_when_candidate_speaks_required_language():
    job = {"title": "Backend Engineer",
           "description": "Must speak French fluently.",
           "location": "Montreal"}
    ok, _ = matching.is_language_eligible(
        job, {"english": "fluent", "french": "professional"})
    assert ok


def test_language_accepts_list_input():
    job = {"title": "Backend Engineer", "description": "Must speak German.",
           "location": "Berlin"}
    ok, _ = matching.is_language_eligible(job, ["english", "german"])
    assert ok


def test_hard_reject_drops_french_required_for_english_only_candidate():
    job = {"title": "Backend Engineer",
           "description": "Must speak French fluently.",
           "location": "Montreal", "work_mode": "remote",
           "locale": "international", "relocation": "unknown",
           "location_restrictions": []}
    rejected, hits = matching.hard_reject(
        job, "senior",
        candidate_regions=["worldwide"],
        candidate_languages={"english": "fluent"})
    assert rejected
    assert any("language_required" in h for h in hits)


# ── ghost job / posting-quality signals ──────────────────
def test_ghost_detects_staffing_agency():
    job = {"title": "Backend Engineer",
           "description": "Staffing agency seeking candidates for our client."}
    assert "staffing_agency_posting" in matching.detect_ghost_job_signals(job)


def test_ghost_detects_apply_off_platform():
    job = {"title": "Engineer",
           "description": "DM me on LinkedIn to apply. Send your resume to recruiter@x.com"}
    signals = matching.detect_ghost_job_signals(job)
    assert "apply_off_platform" in signals


def test_ghost_detects_multiple_ongoing_openings():
    job = {"title": "Engineer",
           "description": "We have multiple openings on an ongoing basis."}
    assert "multiple_ongoing_openings" in matching.detect_ghost_job_signals(job)


def test_ghost_detects_commission_only():
    job = {"title": "Sales Engineer",
           "description": "This is a commission only 1099 contractor position."}
    signals = matching.detect_ghost_job_signals(job)
    assert "commission_or_unpaid" in signals


def test_ghost_clean_job_has_no_signals():
    job = {"title": "Senior Backend Engineer",
           "description": "We're hiring one engineer to own our FastAPI backend."}
    assert matching.detect_ghost_job_signals(job) == []


def test_ghost_signals_are_never_hard_rejects():
    # Even with all ghost signals firing, hard_reject must return False — the
    # LLM gets to weigh them; we don't auto-drop on suspicion.
    job = {"title": "Backend Engineer",
           "description": "Staffing agency. Multiple openings. DM me to apply.",
           "location": "Remote", "work_mode": "remote",
           "locale": "unknown", "relocation": "unknown",
           "location_restrictions": []}
    rejected, _ = matching.hard_reject(
        job, "senior",
        candidate_regions=["bangladesh", "worldwide"],
        candidate_languages={"english": "fluent"})
    assert not rejected
