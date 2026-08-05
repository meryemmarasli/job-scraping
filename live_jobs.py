"""Fetch live AI / ML / training job postings from public job APIs."""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from urllib.parse import quote, urlparse

import requests
from bs4 import BeautifulSoup

from models import JobAnnotation
from scraper import (
    USER_AGENT,
    _extract_requirements,
    _extract_salary,
    _extract_skills,
    _infer_remote,
)

DEFAULT_KEYWORDS = [
    "AI",
    "ML",
    "AI trainer",
    "AI training",
    "AI engineer",
    "AI specialist",
    "ML engineer",
    "machine learning",
    "deep learning",
    "LLM",
    "GenAI",
    "Gen AI",
    "RLHF",
    "prompt engineer",
    "data labeling",
    "data annotation",
    "annotation",
    "NLP",
    "computer vision",
    "generative AI",
    "MLOps",
    "data scientist",
    "foundation model",
    "large language model",
    "model evaluation",
    "AI/ML",
]


def parse_keywords(text: str | None) -> list[str]:
    """Split a user keyword string into a clean list (comma or newline separated)."""
    if not text:
        return list(DEFAULT_KEYWORDS)
    parts: list[str] = []
    for chunk in re.split(r"[\n,;]+", text):
        item = chunk.strip()
        if item and item.lower() not in {p.lower() for p in parts}:
            parts.append(item)
    return parts or list(DEFAULT_KEYWORDS)


def keywords_to_text(keywords: list[str] | None = None) -> str:
    return ", ".join(keywords or DEFAULT_KEYWORDS)


def matches_keywords(
    title: str,
    description: str = "",
    tags: list[str] | None = None,
    keywords: list[str] | None = None,
) -> bool:
    """True if any keyword appears in title, description, or tags.

    Short tokens (AI, ML, NLP) only match against title + tags so boilerplate
    description mentions do not flood results.
    """
    keywords = keywords or DEFAULT_KEYWORDS
    title = title or ""
    description = description or ""
    tags = tags or []
    tag_blob = " ".join(tags)
    title_tags = f"{title}\n{tag_blob}"
    full_blob = f"{title}\n{description}\n{tag_blob}"

    for raw in keywords:
        kw = raw.strip()
        if not kw:
            continue
        # Short tokens like AI / ML / NLP: title + tags only, with word boundaries.
        if len(kw) <= 3 and re.fullmatch(r"[A-Za-z0-9.+/-]+", kw):
            if re.search(rf"\b{re.escape(kw)}\b", title_tags, flags=re.IGNORECASE):
                return True
            continue
        if kw.lower() in full_blob.lower():
            return True
    return False


def _headers() -> dict[str, str]:
    return {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
    }


def _clean(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def _html_to_text(html: str | None) -> str:
    if not html:
        return ""
    return BeautifulSoup(html, "lxml").get_text("\n", strip=True)


def _normalize_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/").lower()


def _annotation_from_fields(
    *,
    title: str,
    company: str,
    location: str,
    description_html: str,
    source_url: str,
    employment_type: str = "",
    salary: str = "",
    tags: list[str] | None = None,
    remote_hint: str = "",
) -> JobAnnotation:
    raw_text = _html_to_text(description_html)
    description = _clean(raw_text)[:4000]
    tag_skills = ", ".join(t for t in (tags or []) if t)
    skills = _extract_skills(f"{title} {description} {tag_skills}")
    if tag_skills and not skills:
        skills = tag_skills

    return JobAnnotation(
        title=_clean(title),
        company=_clean(company),
        location=_clean(location),
        employment_type=_clean(employment_type),
        remote=remote_hint or _infer_remote(f"{location} {description}"),
        salary=_clean(salary) or _extract_salary(description),
        skills=skills,
        requirements=_extract_requirements(raw_text),
        description=description,
        source_url=source_url,
        scraped_at=datetime.now(timezone.utc).isoformat(),
        raw_text=raw_text[:8000],
        notes="",
        reviewed=False,
    )


def _fetch_remotive(keywords: list[str]) -> list[JobAnnotation]:
    payloads: list[dict] = []
    urls = ["https://remotive.com/api/remote-jobs"]
    for kw in keywords[:8]:
        urls.append(f"https://remotive.com/api/remote-jobs?search={quote(kw)}")

    seen_ids: set[str] = set()
    for url in urls:
        try:
            response = requests.get(url, headers=_headers(), timeout=30)
            response.raise_for_status()
            for job in response.json().get("jobs") or []:
                job_id = str(job.get("id") or job.get("url") or "")
                if job_id in seen_ids:
                    continue
                seen_ids.add(job_id)
                payloads.append(job)
        except requests.RequestException:
            continue
        time.sleep(0.15)

    out: list[JobAnnotation] = []
    for job in payloads:
        title = job.get("title") or ""
        desc = job.get("description") or ""
        tags = job.get("tags") or []
        if not matches_keywords(title, _html_to_text(desc), tags, keywords):
            continue
        salary_parts = []
        if job.get("salary"):
            salary_parts.append(str(job["salary"]))
        out.append(
            _annotation_from_fields(
                title=title,
                company=job.get("company_name") or "",
                location=job.get("candidate_required_location") or "",
                description_html=desc,
                source_url=job.get("url") or "",
                employment_type=job.get("job_type") or "",
                salary=" ".join(salary_parts),
                tags=tags,
                remote_hint="Remote",
            )
        )
    return out


def _fetch_remoteok(keywords: list[str]) -> list[JobAnnotation]:
    response = requests.get("https://remoteok.com/api", headers=_headers(), timeout=30)
    response.raise_for_status()
    payload = response.json()
    jobs = payload[1:] if isinstance(payload, list) and payload else []
    out: list[JobAnnotation] = []
    for job in jobs:
        if not isinstance(job, dict) or not job.get("id"):
            continue
        title = job.get("position") or job.get("title") or ""
        desc = job.get("description") or ""
        tags = job.get("tags") or []
        if not matches_keywords(title, _html_to_text(desc), tags, keywords):
            continue

        salary = ""
        if job.get("salary_min") or job.get("salary_max"):
            lo = job.get("salary_min") or 0
            hi = job.get("salary_max") or 0
            if lo or hi:
                salary = f"USD {lo}-{hi}".replace("USD 0-0", "").strip()

        out.append(
            _annotation_from_fields(
                title=title,
                company=job.get("company") or "",
                location=job.get("location") or "Remote",
                description_html=desc,
                source_url=job.get("url") or job.get("apply_url") or "",
                employment_type="",
                salary=salary,
                tags=tags,
                remote_hint="Remote",
            )
        )
    return out


def _fetch_arbeitnow(keywords: list[str], max_pages: int = 25) -> list[JobAnnotation]:
    out: list[JobAnnotation] = []
    for page in range(1, max_pages + 1):
        try:
            response = requests.get(
                "https://www.arbeitnow.com/api/job-board-api",
                params={"page": page},
                headers=_headers(),
                timeout=30,
            )
            if response.status_code == 429:
                break
            response.raise_for_status()
        except requests.RequestException:
            break

        jobs = response.json().get("data") or []
        if not jobs:
            break
        for job in jobs:
            title = job.get("title") or ""
            desc = job.get("description") or ""
            tags = job.get("tags") or []
            if not matches_keywords(title, _html_to_text(desc), tags, keywords):
                continue
            remote = "Remote" if job.get("remote") else ""
            location = ", ".join(
                part for part in [job.get("location") or "", job.get("city") or ""] if part
            )
            out.append(
                _annotation_from_fields(
                    title=title,
                    company=job.get("company_name") or "",
                    location=location,
                    description_html=desc,
                    source_url=job.get("url") or "",
                    employment_type="",
                    salary="",
                    tags=tags,
                    remote_hint=remote,
                )
            )
        time.sleep(0.35)
    return out


def _fetch_jobicy(keywords: list[str]) -> list[JobAnnotation]:
    out: list[JobAnnotation] = []
    tags_to_try: list[str] = []
    for kw in keywords:
        tag = re.sub(r"\s+", "-", kw.strip().lower())
        tag = re.sub(r"[^a-z0-9+-]+", "", tag)
        if 3 <= len(tag) <= 50 and tag not in tags_to_try:
            tags_to_try.append(tag)
    for fallback in ("python", "data", "engineer", "devops", "software"):
        if fallback not in tags_to_try:
            tags_to_try.append(fallback)

    seen_urls: set[str] = set()
    for tag in tags_to_try[:12]:
        response = requests.get(
            "https://jobicy.com/api/v2/remote-jobs",
            params={"count": 100, "tag": tag},
            headers=_headers(),
            timeout=30,
        )
        if response.status_code != 200:
            continue
        jobs = response.json().get("jobs") or []
        for job in jobs:
            url = job.get("url") or ""
            if url in seen_urls:
                continue
            title = job.get("jobTitle") or ""
            desc = job.get("jobDescription") or job.get("jobExcerpt") or ""
            industry = job.get("jobIndustry") or []
            if isinstance(industry, str):
                industry = [industry]
            if not matches_keywords(title, _html_to_text(desc), industry, keywords):
                continue
            seen_urls.add(url)
            job_type = job.get("jobType") or []
            if isinstance(job_type, list):
                employment = ", ".join(job_type)
            else:
                employment = str(job_type)
            salary = ""
            lo = job.get("annualSalaryMin")
            hi = job.get("annualSalaryMax")
            if lo or hi:
                currency = job.get("salaryCurrency") or "USD"
                salary = f"{currency} {lo or ''}-{hi or ''}".strip("- ")

            out.append(
                _annotation_from_fields(
                    title=title,
                    company=job.get("companyName") or "",
                    location=job.get("jobGeo") or "Remote",
                    description_html=desc,
                    source_url=url,
                    employment_type=employment,
                    salary=salary,
                    tags=industry,
                    remote_hint="Remote",
                )
            )
        time.sleep(0.15)
    return out


def fetch_live_ai_jobs(
    limit: int = 75,
    sources: list[str] | None = None,
    keywords: list[str] | None = None,
) -> tuple[list[JobAnnotation], list[dict[str, str]], dict[str, int]]:
    """
    Pull real jobs from public APIs and keep ones matching keywords.

    Returns (jobs, errors, source_counts).
    """
    if keywords is None:
        keywords = list(DEFAULT_KEYWORDS)
    else:
        keywords = parse_keywords(keywords_to_text(keywords))

    selected = sources or ["remotive", "remoteok", "arbeitnow", "jobicy"]
    fetchers = {
        "remotive": _fetch_remotive,
        "remoteok": _fetch_remoteok,
        "arbeitnow": _fetch_arbeitnow,
        "jobicy": _fetch_jobicy,
    }

    collected: list[JobAnnotation] = []
    errors: list[dict[str, str]] = []
    source_counts: dict[str, int] = {}

    for name in selected:
        fetcher = fetchers.get(name)
        if not fetcher:
            continue
        try:
            jobs = fetcher(keywords)
            source_counts[name] = len(jobs)
            collected.extend(jobs)
        except Exception as exc:  # noqa: BLE001 - surface in UI
            source_counts[name] = 0
            errors.append({"url": name, "error": str(exc)})

    deduped: list[JobAnnotation] = []
    seen_urls: set[str] = set()
    seen_keys: set[str] = set()
    for job in collected:
        url_key = _normalize_url(job.source_url)
        identity = f"{job.title.lower()}::{job.company.lower()}"
        if url_key and url_key in seen_urls:
            continue
        if identity in seen_keys:
            continue
        if url_key:
            seen_urls.add(url_key)
        seen_keys.add(identity)
        deduped.append(job)

    # Prefer title matches first so the ~75 we keep are more on-target.
    def rank(job: JobAnnotation) -> tuple[int, str, str]:
        title_hit = 0 if matches_keywords(job.title, "", [], keywords) else 1
        return (title_hit, job.title.lower(), job.company.lower())

    deduped.sort(key=rank)
    return deduped[: max(1, limit)], errors, source_counts
