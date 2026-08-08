"""FastAPI backend: scrape job boards, review/annotate, export."""

from __future__ import annotations

import asyncio
import csv
import io
import json
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse

from models import JobUpdate, ScrapeRequest, utc_now
from online_search import run_online_search_async
from scraper import run_scrape_async
from storage import find_job, get_state, set_scrape_progress, update_jobs, upsert_jobs

app = FastAPI(title="Job Scraper", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/state")
def api_state():
    return get_state()


@app.get("/api/jobs")
def list_jobs(include_deleted: bool = False):
    state = get_state()
    jobs = state.jobs if include_deleted else [j for j in state.jobs if j.status != "deleted"]
    return {"jobs": jobs}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = find_job(job_id)
    if not job or job.status == "deleted":
        raise HTTPException(404, "Job not found")
    return job


@app.patch("/api/jobs/{job_id}")
def patch_job(job_id: str, body: JobUpdate):
    found = {"ok": False}

    def mutate(state):
        for job in state.jobs:
            if job.id != job_id:
                continue
            data = body.model_dump(exclude_unset=True)
            for key, value in data.items():
                setattr(job, key, value)
            if data.get("status") is None:
                job.status = "saved"
            job.updated_at = utc_now()
            found["ok"] = True
            return

    state = update_jobs(mutate)
    if not found["ok"]:
        raise HTTPException(404, "Job not found")
    return next(j for j in state.jobs if j.id == job_id)


@app.post("/api/jobs/{job_id}/save")
def save_job(job_id: str, body: JobUpdate):
    payload = body.model_dump(exclude_unset=True)
    payload["status"] = "saved"
    return patch_job(job_id, JobUpdate(**payload))


@app.post("/api/jobs/{job_id}/delete")
def delete_job(job_id: str):
    found = {"ok": False}

    def mutate(state):
        for job in state.jobs:
            if job.id == job_id:
                job.status = "deleted"
                job.updated_at = utc_now()
                found["ok"] = True
                return

    update_jobs(mutate)
    if not found["ok"]:
        raise HTTPException(404, "Job not found")
    return {"ok": True, "id": job_id}


@app.post("/api/scrape/start")
def start_scrape(body: ScrapeRequest):
    state = get_state()
    if state.scrape.get("running"):
        raise HTTPException(409, "A scrape is already running")

    urls = [u.strip() for u in (body.urls or []) if u and u.strip()]
    mode = body.mode or "online"
    keywords = (body.keywords or "").strip()

    # Keywords required for online search; optional when scraping boards only.
    if mode in ("online", "both") and not keywords:
        raise HTTPException(400, "Add at least one keyword for online search")
    if mode in ("urls", "both") and not urls:
        raise HTTPException(400, "Add at least one board URL")

    set_scrape_progress(
        running=True,
        finished=False,
        message="Starting…",
        collected=0,
        target=body.min_jobs,
        failed_urls=[],
        error=None,
    )

    # Fresh run: keep saved approvals, clear previous unreviewed/deleted so the
    # annotation queue reflects this collection toward min_jobs.
    def clear_queue(state):
        state.jobs = [j for j in state.jobs if j.status == "saved"]

    update_jobs(clear_queue)

    def on_progress(payload: dict):
        set_scrape_progress(
            running=not payload.get("finished", False),
            finished=bool(payload.get("finished")),
            message=payload.get("message", ""),
            collected=payload.get("collected", 0),
            target=payload.get("target", body.min_jobs),
            failed_urls=payload.get("failed_urls", []),
            error=payload.get("error"),
        )

    # Combined runner for "both" or single-mode paths.
    def finish(jobs, failed):
        upsert_jobs(jobs)
        got = len(jobs)
        zero = got == 0
        short = got < body.min_jobs
        err = None
        if zero and failed:
            err = f"Search failed — no contract jobs collected ({len(failed)} source error(s))."
        elif zero:
            err = "No matching contract jobs found. Try different keywords or board URLs."
        elif short:
            err = (
                f"Only found {got} of {body.min_jobs} requested. "
                "Sources exhausted — try more boards, broader keywords, or Both mode."
            )
        elif failed:
            err = f"Finished with {len(failed)} source warning(s)."
        set_scrape_progress(
            running=False,
            finished=True,
            message=(
                err
                if (zero or short)
                else f"Done. Collected {got} / {body.min_jobs} matching contract jobs."
            ),
            collected=got,
            target=body.min_jobs,
            failed_urls=failed,
            error=err,
        )

    if mode == "online":
        run_online_search_async(keywords, body.min_jobs, on_progress, finish)
        return {"ok": True, "message": "Online search started"}

    if mode == "urls":
        run_scrape_async(urls, keywords, body.min_jobs, on_progress, finish)
        return {"ok": True, "message": "Scrape started"}

    # both: online first, then fill remaining from URL boards if needed
    def after_online(online_jobs, online_failed):
        upsert_jobs(online_jobs)
        remaining = max(0, body.min_jobs - len(online_jobs))
        if remaining <= 0 or not urls:
            finish(online_jobs, online_failed)
            return

        def after_urls(url_jobs, url_failed):
            all_jobs = online_jobs + url_jobs
            finish(all_jobs, online_failed + url_failed)

        set_scrape_progress(
            running=True,
            finished=False,
            message=f"Online found {len(online_jobs)}. Scraping boards for {remaining} more…",
            collected=len(online_jobs),
            target=body.min_jobs,
            failed_urls=online_failed,
            error=None,
        )
        run_scrape_async(urls, keywords, remaining, on_progress, after_urls)

    run_online_search_async(keywords, body.min_jobs, on_progress, after_online)
    return {"ok": True, "message": "Online search + board scrape started"}


@app.get("/api/scrape/progress")
async def scrape_progress():
    async def event_stream():
        last = None
        while True:
            state = get_state()
            payload = state.scrape
            encoded = json.dumps(payload)
            if encoded != last:
                yield f"data: {encoded}\n\n"
                last = encoded
            if payload.get("finished") and not payload.get("running"):
                # Send one last event then close.
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/api/export")
def export_jobs(format: Literal["json", "csv"] = Query("json")):
    state = get_state()
    approved = [j for j in state.jobs if j.status == "saved"]
    if format == "json":
        data = json.dumps([j.model_dump() for j in approved], indent=2, ensure_ascii=False)
        return Response(
            content=data,
            media_type="application/json",
            headers={"Content-Disposition": 'attachment; filename="approved-jobs.json"'},
        )

    buf = io.StringIO()
    fields = [
        "id",
        "title",
        "company",
        "client_partner",
        "location",
        "employment_type",
        "pay_rate",
        "salary",
        "hours_per_week",
        "duration",
        "work_mode",
        "languages",
        "domain",
        "task_type",
        "responsibilities",
        "requirements",
        "preferred",
        "tools_skills",
        "screening",
        "description",
        "posted_date",
        "url",
        "notes",
        "status",
        "source_board",
        "created_at",
        "updated_at",
    ]
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for job in approved:
        writer.writerow(job.model_dump())
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="approved-jobs.csv"'},
    )


@app.post("/api/reset")
def reset_unreviewed():
    """Optional helper: clear unreviewed/deleted, keep saved."""

    def mutate(state):
        state.jobs = [j for j in state.jobs if j.status == "saved"]
        state.scrape = {
            "running": False,
            "message": "",
            "collected": 0,
            "target": 0,
            "failed_urls": [],
            "finished": False,
            "error": None,
        }

    return update_jobs(mutate)
