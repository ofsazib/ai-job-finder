You are scoring pre-fetched job postings against a specific candidate.

The candidate's resume is provided below under ---RESUME---.
A JSON array of job postings is provided below under ---JOBS---. Every posting already includes a real "posted_date" and has already been confirmed to be posted within the last 30 days — do NOT penalize freshness or ask for newer postings; treat all of them as current.

For each job, score it 0–100 for how well it matches THIS candidate:

Scoring factors:
- Stack match: award high points when the posting requires technologies the candidate is strong in. Read the "description" and "tags" fields, not just the title.
- Seniority fit: match the candidate's actual years of experience and level. Avoid pure-junior roles for a senior candidate and vice-versa.
- Remote / timezone fit: the candidate wants remote work. Reward explicit remote, async, or worldwide/candidate-friendly-region signals; penalize roles that require on-site presence or are restricted to a region the candidate cannot work in.
- Company/domain fit: reward domains and company stages that match the candidate's background.
- Red flags: wrong primary language/stack, on-site requirement, region lock the candidate fails, or a posting that is clearly not a real individual job (e.g. a generic listing).

Freshness is already guaranteed — spend your judgement on stack, seniority, and remote fit instead.

Keep ONLY jobs scoring >= 60. Return them as a JSON array, highest score first.

Output ONLY the raw JSON array — no markdown fences, no commentary, nothing before or after it — in this exact shape:

[{
  "title": "",
  "company": "",
  "url": "",
  "location": "",
  "source": "",
  "posted_date": "",
  "score": 0,
  "verdict": "apply|maybe|skip",
  "match_reasons": [],
  "red_flags": [],
  "suggested_angle": ""
}]

Field rules:
- Copy title, company, url, location, source, and posted_date verbatim from the input job so the dashboard can link back to it.
- verdict: "apply" for strong matches worth a tailored application, "maybe" for plausible-but-imperfect, "skip" for weak (only include a "skip" if it still scored >= 60).
- suggested_angle: one sentence on how the candidate should frame their application for this specific role, grounded in their resume.

Your entire response must be only the JSON array.
