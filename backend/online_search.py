"""Search public job APIs online using keywords (no board URLs required)."""

from __future__ import annotations

import re
import time
from typing import Callable, Dict, List, Tuple

import requests
from bs4 import BeautifulSoup

from models import Job
from scraper import (
    USER_AGENT,
    enrich_labeling_fields,
    is_contract_job,
    matches_keywords,
    parse_keywords,
)

ProgressCallback = Callable[[dict], None]


def _headers() -> Dict[str, str]:
    return {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
    }


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _html_to_text(html: str) -> str:
    if not html:
        return ""
    return BeautifulSoup(html, "lxml").get_text(" ", strip=True)


def _job(
    *,
    title: str,
    company: str,
    location: str,
    salary: str,
    description: str,
    url: str,
    source: str,
    posted_date: str = "",
) -> Job:
    extras = enrich_labeling_fields(
        title=title,
        description=description,
        company=company,
        location=location,
        salary=salary,
    )
    return Job(
        title=_clean(title)[:300],
        company=_clean(company)[:200],
        location=_clean(location)[:200],
        salary=_clean(extras.get("salary") or salary)[:120],
        pay_rate=_clean(extras.get("pay_rate") or salary)[:120],
        description=_clean(description)[:5000],
        posted_date=_clean(posted_date)[:120],
        url=url,
        source_board=source,
        status="unreviewed",
        employment_type=extras.get("employment_type", "contract"),
        hours_per_week=extras.get("hours_per_week", ""),
        duration=extras.get("duration", ""),
        work_mode=extras.get("work_mode", "remote"),
        languages=extras.get("languages", ""),
        domain=extras.get("domain", ""),
        task_type=extras.get("task_type", ""),
        responsibilities=extras.get("responsibilities", ""),
        requirements=extras.get("requirements", ""),
        preferred=extras.get("preferred", ""),
        tools_skills=extras.get("tools_skills", ""),
        screening=extras.get("screening", ""),
        client_partner=extras.get("client_partner", ""),
    )


def _from_remotive(keywords: List[str]) -> Tuple[List[Job], List[dict]]:
    jobs: List[Job] = []
    failed: List[dict] = []
    queries = keywords[:6] or [""]
    seen = set()
    for kw in queries:
        url = "https://remotive.com/api/remote-jobs"
        params = {"search": kw} if kw else None
        try:
            resp = requests.get(url, params=params, headers=_headers(), timeout=30)
            resp.raise_for_status()
            for item in resp.json().get("jobs") or []:
                link = item.get("url") or ""
                key = link.rstrip("/").lower()
                if not key or key in seen:
                    continue
                title = item.get("title") or ""
                desc = _html_to_text(item.get("description") or "")
                job_type = str(item.get("job_type") or "")
                if not matches_keywords(title, desc, keywords):
                    continue
                if not is_contract_job(
                    title,
                    desc,
                    job_type=job_type,
                    company=item.get("company_name") or "",
                    source="remotive.com",
                ):
                    continue
                seen.add(key)
                jobs.append(
                    _job(
                        title=title,
                        company=item.get("company_name") or "",
                        location=item.get("candidate_required_location") or "Remote",
                        salary=str(item.get("salary") or ""),
                        description=desc,
                        url=link,
                        source="remotive.com",
                        posted_date=str(item.get("publication_date") or ""),
                    )
                )
            time.sleep(0.25)
        except Exception as exc:  # noqa: BLE001
            failed.append({"url": f"remotive:{kw or 'all'}", "error": str(exc)})
    return jobs, failed


def _from_remoteok(keywords: List[str]) -> Tuple[List[Job], List[dict]]:
    jobs: List[Job] = []
    failed: List[dict] = []
    try:
        resp = requests.get("https://remoteok.com/api", headers=_headers(), timeout=30)
        resp.raise_for_status()
        payload = resp.json()
        items = payload[1:] if isinstance(payload, list) and payload else []
        for item in items:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            title = item.get("position") or item.get("title") or ""
            desc = _html_to_text(item.get("description") or "")
            tags = [str(t) for t in (item.get("tags") or [])]
            tag_blob = " ".join(tags)
            if not matches_keywords(title, f"{desc}\n{tag_blob}", keywords):
                continue
            if not is_contract_job(
                title, desc, tags=tags, company=item.get("company") or "", source="remoteok.com"
            ):
                continue
            salary = ""
            lo, hi = item.get("salary_min") or 0, item.get("salary_max") or 0
            if lo or hi:
                salary = f"USD {lo}-{hi}".replace("USD 0-0", "").strip()
            jobs.append(
                _job(
                    title=title,
                    company=item.get("company") or "",
                    location=item.get("location") or "Remote",
                    salary=salary,
                    description=desc,
                    url=item.get("url") or item.get("apply_url") or "",
                    source="remoteok.com",
                    posted_date=str(item.get("date") or ""),
                )
            )
    except Exception as exc:  # noqa: BLE001
        failed.append({"url": "remoteok", "error": str(exc)})
    return jobs, failed


def _from_arbeitnow(keywords: List[str], max_pages: int = 8) -> Tuple[List[Job], List[dict]]:
    jobs: List[Job] = []
    failed: List[dict] = []
    try:
        for page in range(1, max_pages + 1):
            resp = requests.get(
                "https://www.arbeitnow.com/api/job-board-api",
                params={"page": page},
                headers=_headers(),
                timeout=30,
            )
            if resp.status_code == 429:
                failed.append({"url": f"arbeitnow:page{page}", "error": "Rate limited"})
                break
            resp.raise_for_status()
            batch = resp.json().get("data") or []
            if not batch:
                break
            for item in batch:
                title = item.get("title") or ""
                desc = _html_to_text(item.get("description") or "")
                tags = [str(t) for t in (item.get("tags") or [])]
                job_types = item.get("job_types") or []
                if isinstance(job_types, list):
                    tags.extend(str(t) for t in job_types)
                elif job_types:
                    tags.append(str(job_types))
                tag_blob = " ".join(tags)
                if not matches_keywords(title, f"{desc}\n{tag_blob}", keywords):
                    continue
                if not is_contract_job(
                    title,
                    desc,
                    tags=tags,
                    company=item.get("company_name") or "",
                    source="arbeitnow.com",
                ):
                    continue
                jobs.append(
                    _job(
                        title=title,
                        company=item.get("company_name") or "",
                        location=item.get("location") or item.get("city") or "",
                        salary="",
                        description=desc,
                        url=item.get("url") or "",
                        source="arbeitnow.com",
                        posted_date=str(item.get("created_at") or ""),
                    )
                )
            time.sleep(0.3)
    except Exception as exc:  # noqa: BLE001
        failed.append({"url": "arbeitnow", "error": str(exc)})
    return jobs, failed


def _from_jobicy(keywords: List[str]) -> Tuple[List[Job], List[dict]]:
    jobs: List[Job] = []
    failed: List[dict] = []
    # Use keyword-derived tags plus contract-oriented fallbacks.
    tags = []
    for kw in keywords:
        tag = re.sub(r"[^a-z0-9+-]+", "", re.sub(r"\s+", "-", kw.lower()))
        if 3 <= len(tag) <= 50 and tag not in tags:
            tags.append(tag)
    for fallback in ("contract", "freelance", "consultant", "temporary"):
        if fallback not in tags:
            tags.append(fallback)

    seen = set()
    for tag in tags[:10]:
        try:
            resp = requests.get(
                "https://jobicy.com/api/v2/remote-jobs",
                params={"count": 50, "tag": tag},
                headers=_headers(),
                timeout=30,
            )
            if resp.status_code != 200:
                continue
            for item in resp.json().get("jobs") or []:
                link = item.get("url") or ""
                key = link.rstrip("/").lower()
                if not key or key in seen:
                    continue
                title = item.get("jobTitle") or ""
                desc = _html_to_text(item.get("jobDescription") or item.get("jobExcerpt") or "")
                job_type = str(item.get("jobType") or item.get("jobLevel") or "")
                if not matches_keywords(title, desc, keywords):
                    continue
                if not is_contract_job(
                    title,
                    desc,
                    job_type=job_type,
                    company=item.get("companyName") or "",
                    source="jobicy.com",
                ):
                    continue
                seen.add(key)
                salary = ""
                lo, hi = item.get("annualSalaryMin"), item.get("annualSalaryMax")
                if lo or hi:
                    currency = item.get("salaryCurrency") or "USD"
                    salary = f"{currency} {lo or ''}-{hi or ''}".strip("- ")
                jobs.append(
                    _job(
                        title=title,
                        company=item.get("companyName") or "",
                        location=item.get("jobGeo") or "Remote",
                        salary=salary,
                        description=desc,
                        url=link,
                        source="jobicy.com",
                        posted_date=str(item.get("pubDate") or ""),
                    )
                )
            time.sleep(0.2)
        except Exception as exc:  # noqa: BLE001
            failed.append({"url": f"jobicy:{tag}", "error": str(exc)})
    return jobs, failed


def search_online(
    keywords_text: str,
    min_jobs: int,
    on_progress: ProgressCallback = None,
) -> Tuple[List[Job], List[dict]]:
    keywords = parse_keywords(keywords_text)
    if not keywords:
        return [], [{"url": "(keywords)", "error": "Add at least one keyword"}]

    def progress(message: str, collected: int = 0, **extra):
        if on_progress:
            on_progress(
                {
                    "message": message,
                    "collected": collected,
                    "target": min_jobs,
                    **extra,
                }
            )

    collected: List[Job] = []
    failed: List[dict] = []
    seen = set()

    sources = [
        ("Remotive", _from_remotive),
        ("RemoteOK", _from_remoteok),
        ("Arbeitnow", _from_arbeitnow),
        ("Jobicy", _from_jobicy),
    ]

    for name, fetcher in sources:
        if len(collected) >= min_jobs:
            break
        progress(f"Searching {name} for contract roles: {', '.join(keywords[:5])}…", len(collected), failed_urls=failed)
        try:
            jobs, errs = fetcher(keywords)
            failed.extend(errs)
            for job in jobs:
                key = (job.url or "").rstrip("/").lower()
                if not key or key in seen:
                    continue
                seen.add(key)
                collected.append(job)
                progress(
                    f"Collected {len(collected)} / {min_jobs} jobs from online search…",
                    len(collected),
                    failed_urls=failed,
                )
                if len(collected) >= min_jobs:
                    break
        except Exception as exc:  # noqa: BLE001
            failed.append({"url": name, "error": str(exc)})
            progress(f"{name} failed: {exc}", len(collected), failed_urls=failed)

    # Prefer title hits first
    def rank(job: Job) -> Tuple[int, str]:
        title_hit = 0 if matches_keywords(job.title, "", keywords) else 1
        return (title_hit, job.title.lower())

    collected.sort(key=rank)
    collected = collected[: max(min_jobs, min(len(collected), min_jobs))]

    progress(
        f"Done. Collected {len(collected)} / {min_jobs} contract jobs from online search.",
        len(collected),
        failed_urls=failed,
        finished=True,
    )
    return collected, failed


def run_online_search_async(
    keywords_text: str,
    min_jobs: int,
    on_progress: ProgressCallback,
    on_done: Callable[[List[Job], List[dict]], None],
) -> None:
    from concurrent.futures import ThreadPoolExecutor

    def work():
        try:
            jobs, failed = search_online(keywords_text, min_jobs, on_progress=on_progress)
            on_done(jobs, failed)
        except Exception as exc:  # noqa: BLE001
            on_progress(
                {
                    "message": f"Online search crashed: {exc}",
                    "collected": 0,
                    "target": min_jobs,
                    "finished": True,
                    "error": str(exc),
                    "failed_urls": [],
                }
            )
            on_done([], [{"url": "(run)", "error": str(exc)}])

    ThreadPoolExecutor(max_workers=1).submit(work)
