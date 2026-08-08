"""Job models for scrape → review → export."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


JobStatus = Literal["unreviewed", "saved", "deleted"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Job(BaseModel):
    """
    Fields shaped around AI data-labeling / expert contributor roles
    (Mercor, Surge AI, Scale AI, Outlier-style postings).
    """

    id: str = Field(default_factory=lambda: str(uuid4()))

    # Identity
    title: str = ""
    company: str = ""  # Platform / employer (Mercor, Surge, Scale…)
    client_partner: str = ""  # Partner AI lab if disclosed
    location: str = ""  # Geo eligibility (Worldwide, US-only, …)
    url: str = ""
    source_board: str = ""
    posted_date: str = ""

    # Engagement terms (these roles are usually contract + hourly)
    employment_type: str = "contract"
    pay_rate: str = ""  # e.g. $45–$65/hr
    salary: str = ""  # legacy / range display; often mirrors pay_rate
    hours_per_week: str = ""  # e.g. 15–30 hrs/week
    duration: str = ""  # project length / rolling
    work_mode: str = "remote"  # remote, async remote, hybrid
    languages: str = ""  # English required, bilingual, …

    # Role shape
    domain: str = ""  # Biology, Math, Coding, General annotation…
    task_type: str = ""  # RLHF, labeling, evaluation, preference ranking…
    responsibilities: str = ""
    requirements: str = ""
    preferred: str = ""
    tools_skills: str = ""  # LaTeX, Python, Jupyter…
    screening: str = ""  # AI interview, assessment, resume…

    # Freeform + review
    description: str = ""
    notes: str = ""
    status: JobStatus = "unreviewed"
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)


class ScrapeRequest(BaseModel):
    urls: List[str] = Field(default_factory=list)
    keywords: str = ""
    min_jobs: int = Field(default=10, ge=1, le=200)
    # "online" = keyword search APIs, "urls" = scrape boards, "both" = do both
    mode: Literal["urls", "online", "both"] = "online"


class JobUpdate(BaseModel):
    title: Optional[str] = None
    company: Optional[str] = None
    client_partner: Optional[str] = None
    location: Optional[str] = None
    salary: Optional[str] = None
    pay_rate: Optional[str] = None
    employment_type: Optional[str] = None
    hours_per_week: Optional[str] = None
    duration: Optional[str] = None
    work_mode: Optional[str] = None
    languages: Optional[str] = None
    domain: Optional[str] = None
    task_type: Optional[str] = None
    responsibilities: Optional[str] = None
    requirements: Optional[str] = None
    preferred: Optional[str] = None
    tools_skills: Optional[str] = None
    screening: Optional[str] = None
    description: Optional[str] = None
    posted_date: Optional[str] = None
    url: Optional[str] = None
    notes: Optional[str] = None
    status: Optional[JobStatus] = None


class AppState(BaseModel):
    jobs: List[Job] = Field(default_factory=list)
    scrape: Dict[str, Any] = Field(
        default_factory=lambda: {
            "running": False,
            "message": "",
            "collected": 0,
            "target": 0,
            "failed_urls": [],
            "finished": False,
            "error": None,
        }
    )
