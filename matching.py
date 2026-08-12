# matching.py
"""Code-side skill matcher + disqualifier detector.

The LLM analyzer scores holistically; this module gives it ground truth so the
final score stays honest. Two pieces:

1. ``skill_overlap_score`` — count how many of the candidate's must-have and
   nice-to-have skills appear in the job's title / tags / description.
   Deterministic 0-100, computed in code so the LLM cannot hallucinate stack
   fit. Tuned to reward coverage (matching all must-haves) over breadth
   (matching many nice-to-haves).

2. ``disqualifier_hits`` / ``hard_reject`` — regex patterns that mark a job as
   unworkable for THIS candidate regardless of stack match (e.g. needs US
   security clearance, region-locked onsite with no relocation). Hard-rejected
   jobs are dropped before any AI tokens are spent on them; soft hits are
   passed into the analyzer as a red flag for the LLM to weigh.

Both signals flow into ``analyze.md`` alongside the LLM's own reasoning, and
the final score in ``finder.analyze_jobs`` blends the two.
"""
from __future__ import annotations

import re
from typing import Iterable

DEFAULT_PREFERENCES = {
    "employment_types": [],
    "work_modes": [],
    "preferred_domains": [],
    "avoided_terms": [],
    "minimum_salary": 0,
    "salary_currency": "USD",
    "allow_on_call": True,
    "willing_to_relocate": True,
}


def evaluate_preferences(job: dict, preferences: dict | None) -> dict:
    """Return explicit deal-breakers and a small, explainable fit adjustment."""
    prefs = {**DEFAULT_PREFERENCES, **(preferences or {})}
    hard_rejects: list[str] = []
    reasons: list[str] = []
    adjustment = 0

    allowed_types = prefs.get("employment_types") or []
    employment_type = job.get("employment_type", "unknown")
    if allowed_types and employment_type not in ("", "unknown") \
            and employment_type not in allowed_types:
        hard_rejects.append(f"employment_type:{employment_type}")

    blob = _norm_text([job.get("title", ""), job.get("description", "")])
    for term in prefs.get("avoided_terms") or []:
        if _whole_token(blob, term):
            hard_rejects.append(f"avoided_term:{term.lower()}")
    if not prefs.get("allow_on_call", True) and re.search(r"\bon[\s-]?call\b", blob):
        hard_rejects.append("on_call_required")
    if (not prefs.get("willing_to_relocate", True)
            and job.get("work_mode") in ("onsite", "hybrid")
            and job.get("locale") == "international"):
        hard_rejects.append("relocation_required")

    if job.get("work_mode") in (prefs.get("work_modes") or []):
        adjustment += 2
        reasons.append(f"preferred work mode: {job.get('work_mode')}")
    for domain in prefs.get("preferred_domains") or []:
        if _whole_token(blob, domain):
            adjustment += 2
            reasons.append(f"preferred domain: {domain.lower()}")

    return {
        "hard_rejects": list(dict.fromkeys(hard_rejects)),
        "adjustment": max(-10, min(10, adjustment)),
        "reasons": reasons,
    }

# ── disqualifier patterns ─────────────────────────────────
# Region/citizenship blockers — applies to a Bangladesh-based candidate who is
# neither a US citizen nor cleared. A posting that trips any of these is almost
# always unworkable no matter how good the stack match is, so it is dropped in
# code rather than wasted on LLM scoring.
DISQUALIFIER_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("security_clearance_required", re.compile(
        r"\b(security\s+clearance|top[\s-]?secret|ts/?sci|secret\s+clearance"
        r"|able\s+to\s+obtain\s+(a\s+)?clearance|eligibility\s+for\s+clearance"
        r"|requires?\s+clearance|dod\s+clearance"
        r"|department\s+of\s+defense\s+clearance)\b", re.I)),
    ("us_citizen_only", re.compile(
        r"\b(us\s+citizens?|u\.?s\.?\s+citizens?|united\s+states\s+citizens?"
        r"|must\s+be\s+a\s+u\.?s\.?\s+citizen"
        r"|citizen\s+or\s+(green\s+card|permanent\s+resident))\b", re.I)),
    ("export_controlled", re.compile(
        r"\b(itar|ear\s+compliance|export[\s-]?controlled|"
        r"export\s+control\s+regulations?)\b", re.I)),
]

# Seniority extreme gap — a clearly junior/intern-only posting for a senior
# candidate. Soft-flagged elsewhere; only used as a hard reject when combined
# with the candidate's seniority (see hard_reject).
JUNIOR_ONLY_RE = re.compile(
    r"\b(intern(ship)?|new\s+grad(uate)?|entry[\s-]?level|junior|jr\.?)\b", re.I)


# ── region restriction detection ──────────────────────────
# A remote job tagged "US only" / "Europe only" / "must be based in Germany" is
# unworkable for a Bangladesh-based candidate even though it's "remote", because
# the restriction usually reflects tax/legal/work-authorization limits. Two
# signals feed detection:
#   1. Structured location_restrictions field (Himalayas, Jobicy populate it)
#   2. Free-text "X only" / "must be based in X" phrasings in the description
# An explicit "worldwide"/"anywhere" override clears any restrictions.

# Tokens that mean "open to anyone" rather than a specific region lock.
_OPEN_TOKENS = {
    "worldwide", "anywhere", "global", "globally",
    "any country", "any location", "remote", "anywhere in the world",
}

# Known region names we look for after a qualifier ("must be based in X").
# Each is matched as a whole phrase so "us" doesn't hit "aus" or "use".
_KNOWN_REGIONS = [
    # Multi-word first so longer matches win.
    "united states of america", "united states", "usa", "us", "u.s.", "america",
    "european union", "europe", "eu",
    "latin america", "latam", "americas", "north america", "south america",
    "united kingdom", "uk", "u.k.", "britain", "great britain",
    "new zealand", "australia",
    "southeast asia", "south asia", "east asia", "asia",
    "south africa", "africa",
    # Single-country locks common in EU/EMEA remote roles.
    "germany", "france", "spain", "netherlands", "sweden", "ireland",
    "italy", "poland", "portugal", "switzerland", "austria", "belgium",
    "brazil", "argentina", "mexico", "colombia", "chile",
    "india", "pakistan", "bangladesh", "philippines", "indonesia",
    "vietnam", "singapore", "malaysia", "japan",
    "canada",
]

# Compile each region into a whole-phrase matcher (case-insensitive).
_REGION_PATTERNS = [
    (region, re.compile(rf"(?<![a-z]){re.escape(region)}(?![a-z])", re.I))
    for region in _KNOWN_REGIONS
]

# Phrasings that introduce a region lock. We capture the text *after* these
# and then search it for any known region name.
_REGION_QUALIFIER_RE = re.compile(
    r"\b("
    r"must\s+(?:be|reside|live|work|be\s+based|be\s+located)\s+(?:in|based\s+in)\s+"
    r"|only\s+(?:accepting|hiring|open\s+to|from|eligible)\s+(?:in|from|to)?\s*"
    r"|restricted\s+to\s+"
    r"|available\s+(?:only\s+)?in\s+"
    r"|eligib(?:le|ility)\s+(?:in|for|to)\s+"
    r"|work\s+authorization\s+(?:in|required\s+for)\s+"
    r")"
    r"([a-z][a-z\s,.\-]{2,80})",  # text after the qualifier, generous
    re.I,
)

# Explicit "X only" form — common in job postings.
_REGION_ONLY_RE = re.compile(
    r"\b(united\s+states|u\.?s\.?a?|america(?:n\s+states)?|europe|european\s+union|eu"
    r"|americas|latam|latin\s+america|united\s+kingdom|u\.?k\.?|britain|canada"
    r"|australia|new\s+zealand|asia|africa|germany|france|netherlands|spain"
    r"|brazil|argentina|mexico|india|singapore)"
    r"\s+only\b", re.I,
)

_OPEN_OVERRIDE_RE = re.compile(
    r"\b(worldwide|anywhere|any\s+country|any\s+location|open\s+(?:to|globally)"
    r"|globally\s+remote|remote\s+worldwide)\b", re.I)


def detect_region_restrictions(job: dict) -> list[str]:
    """Regions a job is geographically locked to. Empty list = open to anyone.

    Combines structured fields and free-text detection. Lowercased, deduped.
    """
    found: set[str] = set()

    # 1. Structured location_restrictions (highest-confidence signal).
    for r in (job.get("location_restrictions") or []):
        token = (r or "").lower().strip()
        if not token or token in _OPEN_TOKENS:
            continue
        found.add(token)

    blob = f"{job.get('title', '')} {job.get('description', '')} {job.get('location', '')}"

    # 2. "X only" phrasings in the text.
    for m in _REGION_ONLY_RE.finditer(blob):
        found.add(m.group(1).lower().rstrip("."))

    # 3. "must be based in X" / "restricted to X" qualifiers — search the
    # captured text after the qualifier for any known region name.
    for m in _REGION_QUALIFIER_RE.finditer(blob):
        region_text = m.group(2)
        for region, pat in _REGION_PATTERNS:
            if pat.search(region_text):
                found.add(region)
                break  # one region per qualifier is enough

    # 4. Explicit worldwide / anywhere override clears everything.
    if _OPEN_OVERRIDE_RE.search(blob):
        return []

    return sorted(found)


def is_region_eligible(
    job: dict,
    candidate_regions: list[str] | None,
) -> tuple[bool, str]:
    """Can the candidate work this job given its region restrictions?

    Returns ``(eligible, reason)``. ``reason`` is empty when eligible, else a
    short human-readable explanation. Empty restrictions = open to anyone.
    """
    restrictions = detect_region_restrictions(job)
    if not restrictions:
        return True, ""

    candidate = {(r or "").lower().strip() for r in (candidate_regions or [])}
    candidate.discard("")
    if not candidate:
        # Defensive fallback — assume worldwide if profile omitted the field.
        candidate = {"worldwide"}

    # "worldwide" in candidate's eligible set means they're open to remote
    # work anywhere; but a job RESTRICTED to a specific country still requires
    # legal work authorization there, so "worldwide" only matches an OPEN job
    # (handled above). For a restricted job, candidate must list that region
    # explicitly — except: worldwide candidate can take worldwide jobs, which
    # is already covered by the empty-restrictions path.
    overlap = set(restrictions) & candidate
    if overlap:
        return True, ""

    return False, f"restricted to {restrictions}, candidate eligible in {sorted(candidate)}"


# ── helpers ───────────────────────────────────────────────
def _norm_text(parts: Iterable[str]) -> str:
    return " ".join(p or "" for p in parts).lower()


def _whole_token(haystack: str, skill: str) -> bool:
    """Match `skill` as a whole token, not a substring.

    Same boundary trick as ``sources._keyword_pattern``: prevents "go" from
    matching "Portugal" and "aws" from matching "lawson". Tech tokens with
    dots/pluses (node.js, c++) still match because we escape them.
    """
    s = (skill or "").strip().lower()
    if not s:
        return False
    pat = re.compile(rf"(?<![a-z0-9]){re.escape(s)}(?![a-z0-9])", re.I)
    return bool(pat.search(haystack))


_ROLE_FAMILIES = {
    "backend": ("backend", "back-end"),
    "python": ("python",),
    "platform": ("platform", "infrastructure"),
    "full-stack": ("full-stack", "full stack", "fullstack"),
    "architect": ("architect",),
    "leadership": ("tech lead", "technical lead", "engineering lead"),
    "data": ("data engineer", "data platform"),
}
_NEGATIVE_ROLE_RE = re.compile(
    r"\b(procurement|sales|customer\s+(?:success|support)|graphic\s+design"
    r"|product\s+design|manual\s+qa|quality\s+assurance|recruiter|marketing)\b",
    re.I,
)
_ENGINEERING_TITLE_RE = re.compile(
    r"\b(engineer|developer|architect|tech(?:nical)?\s+lead)\b", re.I
)


def role_relevance(job: dict, profile: dict | None) -> dict:
    """Cheap title-first gate that broad description keywords cannot bypass."""
    profile = profile or {}
    title = (job.get("title") or "").lower()
    target = " ".join(profile.get("target_roles") or []).lower()
    negative = _NEGATIVE_ROLE_RE.search(title)
    if negative and negative.group(1).lower() not in target:
        return {"relevant": False, "score": 0,
                "reasons": [f"unrelated title: {negative.group(1).lower()}"]}

    for family, aliases in _ROLE_FAMILIES.items():
        if any(alias in title for alias in aliases) and any(alias in target for alias in aliases):
            return {"relevant": True, "score": 85,
                    "reasons": [f"target role family: {family}"]}

    skills = [
        s for s in [*(profile.get("must_have_skills") or []),
                    *(profile.get("nice_to_have_skills") or []),
                    *(profile.get("keywords") or [])]
        if s and s not in {"backend", "system design", "distributed systems"}
    ]
    haystack = _norm_text([
        job.get("title", ""), " ".join(job.get("tags", []) or []),
        job.get("description", ""),
    ])
    hits = list(dict.fromkeys(s.lower() for s in skills if _whole_token(haystack, s)))
    relevant = bool(_ENGINEERING_TITLE_RE.search(title)) and len(hits) >= 2
    return {
        "relevant": relevant,
        "score": min(79, 35 + len(hits) * 12) if relevant else 0,
        "reasons": [f"skill evidence: {', '.join(hits[:5])}"] if hits else [],
    }


# ── skill overlap score ───────────────────────────────────
def skill_overlap_score(
    job: dict,
    must_have: list[str] | None,
    nice_to_have: list[str] | None,
) -> int:
    """Score positive role evidence, not coverage of the entire résumé stack."""
    haystack = _norm_text([
        job.get("title", ""),
        " ".join(job.get("tags", [])),
        job.get("description", ""),
    ])

    profile_skills = [s for s in [*(must_have or []), *(nice_to_have or [])] if s]
    if not profile_skills:
        return 50

    families = (
        (30, ("python", "go", "java", "ruby", "typescript", "javascript", "c#")),
        (25, ("django", "fastapi", "flask", "rails", "spring", "node.js", "express")),
        (25, ("postgresql", "postgres", "mysql", "sql", "mongodb", "dynamodb")),
        (20, ("aws", "gcp", "azure")),
        (20, ("docker", "kubernetes", "k3s")),
        (15, ("celery", "redis", "kafka", "rabbitmq", "queues", "microservices")),
        (15, ("elasticsearch", "opensearch", "pgvector", "vector database")),
    )
    configured = {
        i for i, (_, members) in enumerate(families)
        if any(skill.lower() in members for skill in profile_skills)
    }
    matched = {
        i for i, (_, members) in enumerate(families)
        if i in configured and any(_whole_token(haystack, member) for member in members)
    }
    score = sum(families[i][0] for i in matched)

    # Exact configured tools outside known families still provide small evidence.
    known = {member for _, members in families for member in members}
    exact_other = sum(
        1 for skill in profile_skills
        if skill.lower() not in known and _whole_token(haystack, skill)
    )
    return min(100, score + min(20, exact_other * 5))


# ── disqualifier detection ────────────────────────────────
# ── language requirement detection ────────────────────────
# A job that requires a language the candidate doesn't speak is unworkable
# regardless of stack fit. Detection looks for both explicit "must speak X"
# phrasings and language names appearing next to "bilingual" / "fluent" markers.
_LANGUAGE_NAMES = {
    "english": ["english", "anglais", "englisch"],
    "german": ["german", "deutsch"],
    "french": ["french", "français", "francais"],
    "spanish": ["spanish", "español", "espanol"],
    "portuguese": ["portuguese", "português"],
    "italian": ["italian", "italiano"],
    "dutch": ["dutch", "nederlands"],
    "mandarin": ["mandarin", "chinese"],
    "japanese": ["japanese", "nihongo"],
    "korean": ["korean", "hangul"],
    "arabic": ["arabic"],
    "hindi": ["hindi"],
    "bengali": ["bengali", "bangla"],
    "russian": ["russian", "русский"],
    "polish": ["polish", "polski"],
    "turkish": ["turkish", "türkçe"],
}
# Phrasings that mark a language as REQUIRED (vs casually mentioned).
_LANGUAGE_REQ_RE = re.compile(
    r"\b("
    r"must\s+(?:speak|be\s+(?:fluent|proficient|able\s+to\s+communicate)\s+(?:in|with))\s+"
    r"|fluent\s+(?:in|with)\s+"
    r"|proficient\s+(?:in|with)\s+"
    r"|native\s+(?:speaker\s+of\s+|fluent|level)\s*"
    r"|bilingual\s+"
    r"|language\s+requirement\s*:?\s*"
    r"|working\s+knowledge\s+of\s+"
    r"|written\s+and\s+spoken\s+"
    r"|business\s+(?:proficiency|level)\s+in\s+"
    r"|requires?\s+(?:fluent|proficiency|knowledge\s+of)\s+"
    r")"
    r"([a-zà-ÿ][a-zà-ÿ\s,.\-]{2,40})",
    re.I,
)
# "Bilingual X/Y" or "X/Y bilingual" forms.
_BILINGUAL_RE = re.compile(
    r"\bbilingual\s+([a-zà-ÿ\-]+(?:\s*[/,\s+]\s*[a-zà-ÿ\-]+)+)", re.I,
)


def detect_language_requirements(job: dict) -> list[tuple[str, str]]:
    """Languages the job explicitly requires. Returns ``[(language, level)]``.

    ``level`` is the strictness: "required" when an explicit qualifier like
    "must speak" or "fluent in" precedes the language, "preferred" when only
    the language name appears without a qualifier (treated as a soft signal).
    """
    blob = f"{job.get('title', '')} {job.get('description', '')} {job.get('location', '')}"
    found: dict[str, str] = {}

    # 1. Explicit qualifier phrasings → "required".
    for m in _LANGUAGE_REQ_RE.finditer(blob):
        snippet = m.group(2).lower()
        for lang, aliases in _LANGUAGE_NAMES.items():
            if any(re.search(rf"\b{re.escape(a)}\b", snippet) for a in aliases):
                found[lang] = "required"
                break

    # 2. "Bilingual X/Y" form → both are required.
    for m in _BILINGUAL_RE.finditer(blob):
        snippet = m.group(1).lower()
        for lang, aliases in _LANGUAGE_NAMES.items():
            if any(re.search(rf"\b{re.escape(a)}\b", snippet) for a in aliases):
                found[lang] = "required"
                break

    # 3. Bare language mention in title → "required" (e.g. "Korean Bilingual Dev").
    title = (job.get("title", "") or "").lower()
    for lang, aliases in _LANGUAGE_NAMES.items():
        for alias in aliases:
            if re.search(rf"\b{re.escape(alias)}\b", title):
                found[lang] = "required"
                break

    return sorted(found.items())


def is_language_eligible(
    job: dict,
    candidate_languages: dict | list | None,
) -> tuple[bool, str]:
    """Can the candidate satisfy the job's language requirements?

    ``candidate_languages`` may be a dict ``{"english": "fluent"}`` or a list
    ``["english", "bengali"]``. Returns ``(eligible, reason)``.
    """
    required = detect_language_requirements(job)
    if not required:
        return True, ""

    # Normalize candidate_languages to a set of language names.
    if candidate_languages is None:
        candidate = set()
    elif isinstance(candidate_languages, dict):
        candidate = {k.lower() for k in candidate_languages}
    else:
        candidate = {str(k).lower() for k in candidate_languages}

    missing = [lang for lang, _ in required if lang not in candidate]
    if missing:
        return False, f"requires {missing}, candidate speaks {sorted(candidate) or 'none'}"
    return True, ""


# ── ghost-job / posting-quality signals ──────────────────
# These do NOT hard-reject (one false positive would cost a real opportunity),
# but they are attached to the job and surfaced to the LLM as red flags so the
# score reflects legitimacy concerns. Patterns inspired by career-ops Block G.
_GHOST_SIGNALS = [
    ("staffing_agency_posting", re.compile(
        r"\b(staffing\s+agency|recruiting\s+firm|talent\s+partner|talent\s+agency"
        r"|third[\s-]?party\s+recruiter|consulting\s+firm\s+seeking|c2c\s+requirements?)\b",
        re.I,
    )),
    ("multiple_ongoing_openings", re.compile(
        r"\b(multiple\s+(?:openings|positions|roles|seats)"
        r"|several\s+positions|ongoing\s+basis|always\s+hiring"
        r"|rolling\s+basis|continuous\s+(?:recruiting|hiring))\b",
        re.I,
    )),
    ("apply_off_platform", re.compile(
        r"\b(send\s+your\s+resume\s+to|apply\s+via\s+email|dm\s+me\b"
        r"|contact\s+me\s+(?:directly|on\s+linkedin|via\s+whatsapp)"
        r"|reach\s+out\s+to\s+[\w.\-]+@|message\s+me\s+(?:directly|on\s+linkedin))\b",
        re.I,
    )),
    ("commission_or_unpaid", re.compile(
        r"\b(commission\s+only|1099\s+(?:contractor|position)"
        r"|unpaid\s+(?:internship|position|role)|equity[\s-]?only|deferred\s+compensation)\b",
        re.I,
    )),
    ("vague_future_promise", re.compile(
        r"\b(submit\s+your\s+resume\s+for\s+future|building\s+our\s+talent\s+pool"
        r"|we\s+are\s+always\s+looking\s+for|keep\s+your\s+resume\s+on\s+file)\b",
        re.I,
    )),
    ("salary_too_good_to_be_true", re.compile(
        r"\$\s?\d{2,3}\s?0{3}\s?[/–-]\s?\$?\s?\d{3}\s?0{3}\s?(?:/?(?:month|week|day))\b",
        re.I,
    )),
]


def detect_ghost_job_signals(job: dict) -> list[str]:
    """Soft legitimacy concerns. Non-empty list = posting looks suspicious.

    Returned as a list of signal names (e.g. ``["staffing_agency_posting"]``).
    Never hard-rejects on these alone — one false positive would cost a real
    opportunity. Instead the LLM sees them via ``analyze.md`` and weighs them.
    """
    blob = f"{job.get('title', '')} {job.get('description', '')}"
    return [name for name, pat in _GHOST_SIGNALS if pat.search(blob)]


def disqualifier_hits(job: dict) -> list[str]:
    """List of disqualifiers that fire for this job.

    Includes both explicit (regex match on text) and structural flags
    (region-locked onsite with no relocation).
    """
    blob = _norm_text([
        job.get("title", ""),
        job.get("description", ""),
        job.get("location", ""),
        " ".join(job.get("location_restrictions", []) or []),
    ])
    hits = [name for name, pat in DISQUALIFIER_PATTERNS if pat.search(blob)]

    # Region-locked: onsite/hybrid abroad with no relocation support.
    # For a candidate who cannot legally work there and gets no help moving,
    # the role is unworkable regardless of stack.
    if (job.get("work_mode") in ("onsite", "hybrid")
            and job.get("locale") == "international"
            and job.get("relocation") == "no"):
        hits.append("region_locked_no_relocation")

    return hits


def hard_reject(
    job: dict,
    candidate_seniority: str = "senior",
    candidate_regions: list[str] | None = None,
    candidate_languages: dict | list | None = None,
) -> tuple[bool, list[str]]:
    """Should this job be dropped before LLM scoring, and why?

    Hard rejects (drop in code, no tokens spent):
      - any clearance / citizenship / export-control regex hit
      - region-locked onsite abroad with no relocation
      - region-restricted to a country the candidate isn't eligible for
        (e.g. "US only" remote role for a Bangladesh-based candidate)
      - requires a language the candidate doesn't speak
      - junior/intern-only role for a senior+ candidate

    Returns ``(rejected, hits)``. Soft hits (region_restricted, ghost_job_*
    signals) are surfaced via ``disqualifier_hits`` / ``detect_ghost_job_signals``
    so the LLM can weigh them in scoring.
    """
    hits = disqualifier_hits(job)
    # region_locked_no_relocation is a hard reject: zero viable path.
    if "region_locked_no_relocation" in hits:
        return True, hits
    # Regex pattern hits (clearance / citizenship / export) are hard rejects.
    pattern_hits = [h for h in hits if h != "region_locked_no_relocation"]
    if pattern_hits:
        return True, pattern_hits

    # Region eligibility — job restricted to countries candidate can't work in.
    eligible, reason = is_region_eligible(job, candidate_regions)
    if not eligible:
        return True, ["region_restricted:" + reason]

    # Language eligibility — job requires a language candidate doesn't speak.
    lang_ok, lang_reason = is_language_eligible(job, candidate_languages)
    if not lang_ok:
        return True, ["language_required:" + lang_reason]

    # Junior/intern-only role for a senior+ candidate.
    if candidate_seniority in ("senior", "lead"):
        if JUNIOR_ONLY_RE.search(_norm_text([job.get("title", "")])):
            return True, ["junior_role_for_senior_candidate"]
    return False, hits
