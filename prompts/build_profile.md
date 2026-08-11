Extract a job-search profile from the candidate's resume (provided below under ---RESUME---).

Output ONLY a valid JSON object — no markdown fences, no explanation, no text before or after it — in this exact shape:

{
  "target_roles": [],
  "keywords": [],
  "must_have_skills": [],
  "nice_to_have_skills": [],
  "seniority": "",
  "seniority_years": 0,
  "region_eligibility": [],
  "languages": {}
}

Rules:

- target_roles: 4-6 job-title variants that match the candidate's actual experience and seniority (e.g. "Backend Engineer", "Python Developer", "Senior Software Engineer").

- keywords: 10-16 lowercase single-word or short technology/domain terms used for literal substring matching against job titles, tags, and descriptions. Prefer concrete, discriminating terms (languages, frameworks, databases, cloud, paradigms) over generic words like "developer", "software", "engineer", "remote", "team". Use the common spelling as it appears in postings (e.g. "python", "django", "fastapi", "postgres", "react", "typescript", "aws", "microservices"). A couple of distinctive role-level terms (e.g. "backend", "full-stack") are fine.

- must_have_skills: 6-12 lowercase concrete technologies the candidate is STRONG in and wants to keep working with. These drive the deterministic skill-coverage score in code, so every entry should be a real token a posting would mention (e.g. "python", "django", "fastapi", "postgresql", "redis", "aws", "docker", "microservices", "celery", "elasticsearch"). Do not include soft skills, frameworks the candidate only touched once, or generic words.

- nice_to_have_skills: 4-8 lowercase technologies the candidate has exposure to but are not core (e.g. "go", "gcp", "kubernetes", "graphql", "react"). Same token rules as must_have_skills.

- seniority: one of "junior", "mid", "senior", "lead" — the candidate's actual current level, drawn from years and titles in the resume.

- seniority_years: integer total years of professional software experience visible in the resume.

- region_eligibility: list of regions the candidate can legally / logistically work in, drawn from the resume's location and any explicit statements. Use lowercase tokens: "bangladesh" (local), "worldwide" (open to remote anywhere), or specific country names if the resume shows existing work authorization. If unclear, default to the candidate's stated country + "worldwide".

- languages: object mapping every language the candidate can professionally work in (read, write, converse) to their level. Use lowercase keys and one of these levels: "native", "fluent", "professional", "intermediate", "basic". Drawn from resume's stated languages and any countries worked in. Example: {"english": "fluent", "bengali": "native"}. Include English even if the resume doesn't say it explicitly — most software roles require it.

Your entire response must be only the raw JSON object.
