import json
import queue
import threading
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

app = FastAPI(title="AI Job Finder")

STATUS_FILE = Path("output/status.json")
JOBS_FILE = Path("output/jobs.json")
COVER_LETTERS_DIR = Path("output/cover_letters")

run_lock = threading.Lock()

# Days before an applied job is considered "going quiet" and worth a follow-up.
FOLLOWUP_THRESHOLD_DAYS = 10


# ── status schema ─────────────────────────────────────────
# Status entries are dicts: {"status": "applied", "applied_at": "...",
# "updated_at": "...", "stages": [...], "outcome": "..."}.
# Legacy string values ("applied" / "skipped") are migrated to this shape on read.
def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _migrate_status_entry(url: str, value) -> dict:
    """Coerce a status entry (possibly legacy string) into the canonical dict."""
    if isinstance(value, dict):
        # Already in new shape — ensure required keys exist.
        value.setdefault("status", "none")
        value.setdefault("applied_at", "")
        value.setdefault("updated_at", "")
        if not isinstance(value.get("stages"), list):
            value["stages"] = []
        value.setdefault("outcome", "")
        return value
    # Legacy string: "applied" | "skipped" | anything else → "none".
    status = value if value in ("applied", "skipped") else "none"
    now = _now_iso()
    return {
        "status": status,
        "applied_at": now if status in ("applied", "skipped") else "",
        "updated_at": now if status in ("applied", "skipped") else "",
        "stages": [],
        "outcome": "",
    }


def _read_status() -> dict:
    """Read + migrate status.json. Every value is normalized to the new shape."""
    if not STATUS_FILE.exists():
        return {}
    raw = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return {}
    migrated = {url: _migrate_status_entry(url, v) for url, v in raw.items()}
    # Persist migration so the on-disk format converges to the new schema.
    if any(isinstance(v, str) for v in raw.values()):
        _write_status(migrated)
    return migrated


def _write_status(data: dict) -> None:
    STATUS_FILE.parent.mkdir(exist_ok=True)
    STATUS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


@app.get("/")
async def index():
    return FileResponse("ui/index.html")


@app.get("/api/jobs")
async def get_jobs():
    if not JOBS_FILE.exists():
        return JSONResponse([])
    jobs = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
    status = _read_status()
    for job in jobs:
        url = job.get("url", "")
        entry = status.get(url, {})
        # Expose both the legacy flat string (for backward compat) and the
        # full entry (for the new UI). Older UI code reads job["status"]; new
        # UI code reads job["status_entry"].
        job["status"] = entry.get("status", "none") if isinstance(entry, dict) else "none"
        job["status_entry"] = entry if isinstance(entry, dict) else {}
    return JSONResponse(jobs)


class StatusUpdate(BaseModel):
    url: str
    status: str


@app.post("/api/status")
async def update_status(body: StatusUpdate):
    if body.status not in ("applied", "skipped", "none"):
        raise HTTPException(status_code=400, detail="status must be applied, skipped, or none")
    data = _read_status()
    now = _now_iso()
    if body.status == "none":
        data.pop(body.url, None)
    else:
        existing = data.get(body.url, {})
        applied_at = existing.get("applied_at", "") if isinstance(existing, dict) else ""
        if not applied_at:
            applied_at = now
        data[body.url] = {
            "status": body.status,
            "applied_at": applied_at,
            "updated_at": now,
            "stages": existing.get("stages", []) if isinstance(existing, dict) else [],
            "outcome": existing.get("outcome", "") if isinstance(existing, dict) else "",
        }
    _write_status(data)
    return {"ok": True}


class OutcomeUpdate(BaseModel):
    url: str
    outcome: str  # "interviewing" | "offer" | "rejected" | "ghosted" | "withdrawn" | "hired" | free text
    note: str = ""


@app.post("/api/outcome")
async def update_outcome(body: OutcomeUpdate):
    """Record what happened to an application."""
    data = _read_status()
    entry = data.get(body.url)
    if not entry:
        raise HTTPException(status_code=404, detail="no tracked application for this url")
    now = _now_iso()
    entry["outcome"] = body.outcome
    entry["updated_at"] = now
    if body.note:
        stages = entry.get("stages", [])
        stages.append({"at": now, "note": body.note})
        entry["stages"] = stages
    _write_status(data)
    return {"ok": True}


@app.get("/api/stats")
async def get_stats():
    """Funnel + breakdown stats across all jobs and their tracked outcomes."""
    if not JOBS_FILE.exists():
        return JSONResponse({})
    jobs = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
    status = _read_status()

    total = len(jobs)
    by_verdict = {}
    by_source = {}
    by_score_band = {"<60": 0, "60-69": 0, "70-79": 0, "80-89": 0, "90-100": 0}
    tracked = 0
    applied = 0
    skipped = 0
    by_outcome = {}

    for job in jobs:
        v = job.get("verdict", "unknown")
        by_verdict[v] = by_verdict.get(v, 0) + 1

        s = job.get("source", "unknown")
        by_source[s] = by_source.get(s, 0) + 1

        score = job.get("score", 0) or 0
        if score >= 90:
            by_score_band["90-100"] += 1
        elif score >= 80:
            by_score_band["80-89"] += 1
        elif score >= 70:
            by_score_band["70-79"] += 1
        elif score >= 60:
            by_score_band["60-69"] += 1
        else:
            by_score_band["<60"] += 1

        entry = status.get(job.get("url", {}), {})
        st = entry.get("status", "none") if isinstance(entry, dict) else "none"
        if st in ("applied", "skipped"):
            tracked += 1
        if st == "applied":
            applied += 1
        elif st == "skipped":
            skipped += 1
        outcome = entry.get("outcome", "") if isinstance(entry, dict) else ""
        if outcome:
            by_outcome[outcome] = by_outcome.get(outcome, 0) + 1

    return JSONResponse({
        "total": total,
        "tracked": tracked,
        "applied": applied,
        "skipped": skipped,
        "by_verdict": by_verdict,
        "by_source": by_source,
        "by_score_band": by_score_band,
        "by_outcome": by_outcome,
    })


@app.get("/api/followups")
async def get_followups(days: int = 10):
    """Applied jobs older than `days` with no outcome — candidates for a follow-up."""
    if not JOBS_FILE.exists():
        return JSONResponse([])
    jobs = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
    status = _read_status()
    now = datetime.now(tz=timezone.utc)
    threshold = days * 86400

    followups = []
    for job in jobs:
        entry = status.get(job.get("url", {}), {})
        if not isinstance(entry, dict):
            continue
        if entry.get("status") != "applied":
            continue
        if entry.get("outcome", ""):
            continue  # already resolved
        applied_at = entry.get("applied_at", "")
        if not applied_at:
            continue
        try:
            applied_dt = datetime.fromisoformat(applied_at)
        except ValueError:
            continue
        elapsed = (now - applied_dt).total_seconds()
        if elapsed >= threshold:
            followups.append({
                "url": job.get("url", ""),
                "title": job.get("title", ""),
                "company": job.get("company", ""),
                "applied_at": applied_at,
                "days_since": int(elapsed // 86400),
                "suggested_angle": job.get("suggested_angle", ""),
            })
    followups.sort(key=lambda x: x["days_since"], reverse=True)
    return JSONResponse(followups)


@app.get("/api/cover-letter")
async def get_cover_letter(company: str = Query(...), title: str = Query(...)):
    from finder import _slug
    slug = f"{_slug(company)}__{_slug(title)}"
    path = COVER_LETTERS_DIR / f"{slug}.md"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Cover letter not found")
    return {"content": path.read_text(encoding="utf-8")}


@app.get("/api/run")
async def run_agent():
    if not run_lock.acquire(blocking=False):
        async def _busy():
            yield f"data: {json.dumps({'step': 'busy'})}\n\n"
        return StreamingResponse(_busy(), media_type="text/event-stream")

    q: queue.Queue = queue.Queue()

    def _on_progress(step: int, label: str, status: str) -> None:
        q.put({"step": step, "label": label, "status": status})

    def _pipeline_thread() -> None:
        try:
            from finder import run_pipeline
            result = run_pipeline(on_progress=_on_progress)
            q.put({"step": "complete", **result})
        except Exception as exc:
            q.put({"step": "error", "message": str(exc)})
        finally:
            run_lock.release()
            q.put(None)

    threading.Thread(target=_pipeline_thread, daemon=True).start()

    async def _event_stream():
        import asyncio
        loop = asyncio.get_running_loop()
        while True:
            event = await loop.run_in_executor(None, q.get)
            if event is None:
                break
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(_event_stream(), media_type="text/event-stream")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
