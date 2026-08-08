"""JSON file persistence for jobs + scrape status."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from models import AppState, Job, utc_now

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_PATH = DATA_DIR / "jobs.json"

_lock = threading.Lock()


def _default_state() -> AppState:
    return AppState()


def load_state() -> AppState:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not STATE_PATH.exists():
        state = _default_state()
        save_state(state)
        return state
    try:
        raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return AppState.model_validate(raw)
    except (OSError, json.JSONDecodeError, ValueError):
        return _default_state()


def save_state(state: AppState) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(state.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)


def get_state() -> AppState:
    with _lock:
        return load_state()


def update_jobs(mutator) -> AppState:
    with _lock:
        state = load_state()
        mutator(state)
        save_state(state)
        return state


def upsert_jobs(new_jobs: list[Job], *, replace_unreviewed: bool = False) -> AppState:
    with _lock:
        state = load_state()
        by_url = {j.url.rstrip("/").lower(): j for j in state.jobs if j.url}
        for job in new_jobs:
            key = job.url.rstrip("/").lower() if job.url else job.id
            existing = by_url.get(key)
            if existing and existing.status in ("saved", "deleted"):
                continue
            if existing:
                # Refresh scraped fields but keep annotations if any
                for field in (
                    "title",
                    "company",
                    "client_partner",
                    "location",
                    "salary",
                    "pay_rate",
                    "employment_type",
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
                    "source_board",
                ):
                    value = getattr(job, field, None)
                    if value:
                        setattr(existing, field, value)
                existing.updated_at = utc_now()
            else:
                state.jobs.append(job)
                by_url[key] = job
        save_state(state)
        return state


def set_scrape_progress(**kwargs) -> AppState:
    with _lock:
        state = load_state()
        state.scrape.update(kwargs)
        save_state(state)
        return state


def find_job(job_id: str) -> Job | None:
    state = get_state()
    for job in state.jobs:
        if job.id == job_id:
            return job
    return None
