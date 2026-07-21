You are scoring pre-fetched job postings against a specific candidate.

The candidate's resume is provided below under ---RESUME---.
A JSON array of job postings is provided below under ---JOBS---. Every posting already includes a real "posted_date" and has already been confirmed to be posted within the last 30 days — do NOT penalize freshness or ask for newer postings; treat all of them as current.

Each posting also carries pre-computed categorization fields you should factor into scoring:
- "work_mode": remote | onsite | hybrid | unknown
- "locale": bangladesh (local to the candidate) | international | unknown
- "relocation": yes | no | unknown — whether the posting offers relocation/visa help
- "employment_type": full-time | contract | part-time | internship | unknown
- "seniority": junior | mid | senior | lead | unknown
- "salary": human-readable range, or "" if none published
- "location_restrictions": regions the role is limited to (empty = open)

For each job, score it 0–100 for how well it matches THIS candidate:

Scoring factors:
- Stack match: award high points when the posting requires technologies the candidate is strong in. Read the "description" and "tags" fields, not just the title.
- Seniority fit: match the candidate's actual years of experience and level. Use the "seniority" field plus the description. Avoid pure-junior roles for a senior candidate and vice-versa.
- Location & work-mode fit: the candidate is based in Bangladesh. A "remote" role open worldwide is ideal. A "bangladesh" locale onsite/hybrid role is also a strong fit (no relocation needed). An "onsite"/"hybrid" role in another country is only viable if "relocation" is "yes" (relocation or visa support) — otherwise treat the location as a red flag. Reward international remote roles whose "location_restrictions" include the candidate's region or are empty; penalize roles region-locked to places the candidate cannot work.
- Relocation: if a role is attractive but abroad and onsite, an explicit "relocation": "yes" should raise the score; "no" on such a role should lower it.
- Company/domain fit: reward domains and company stages that match the candidate's background.
- Red flags: wrong primary language/stack, onsite/hybrid abroad with no relocation support, region lock the candidate fails, or a posting that is clearly not a real individual job (e.g. a generic listing).

Freshness is already guaranteed — spend your judgement on stack, seniority, and location/work-mode fit instead.

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
  "verdict": "apply|review|skip",
  "match_reasons": [],
  "red_flags": [],
  "suggested_angle": ""
}]

Field rules:
- Copy title, company, url, location, source, and posted_date verbatim from the input job so the dashboard can link back to it.
- verdict: "apply" for strong matches worth a tailored application, "review" for plausible-but-imperfect, "skip" for weak (only include a "skip" if it still scored >= 60).
- suggested_angle: one sentence on how the candidate should frame their application for this specific role, grounded in their resume.

Your entire response must be only the JSON array.
