Extract a job-search profile from the candidate's resume (provided below under ---RESUME---).

Output ONLY a valid JSON object — no markdown fences, no explanation, no text before or after it — in this exact shape:

{
  "target_roles": [],
  "keywords": []
}

Rules:
- target_roles: 4–6 job-title variants that match the candidate's actual experience and seniority (e.g. "Backend Engineer", "Python Developer", "Senior Software Engineer").
- keywords: 10–16 lowercase single-word or short technology/domain terms drawn from the resume's strongest skills and repeated tools. These are used for literal substring matching against job titles, tags, and descriptions, so:
    - prefer concrete, discriminating terms (languages, frameworks, databases, cloud, paradigms) over generic words like "developer", "software", "engineer", "remote", "team".
    - use the common spelling as it appears in postings (e.g. "python", "django", "fastapi", "postgres", "react", "typescript", "aws", "microservices").
    - include a couple of role-level terms only if they are distinctive (e.g. "backend", "full-stack").

Your entire response must be only the raw JSON object.
