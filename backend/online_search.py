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
    extract_pay_rate,
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
    # Prefer structured API pay when it's real; otherwise use mined listing text.
    pay = extras.get("pay_rate") or extract_pay_rate(title, description, salary) or _clean(salary)
    return Job(
        title=_clean(title)[:300],
        company=_clean(company)[:200],
        location=_clean(location)[:200],
        salary=_clean(pay)[:120],
        pay_rate=_clean(pay)[:120],
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
    # Broad pull + per-keyword queries so we can fill min_jobs.
    queries = list(dict.fromkeys([*keywords[:8], ""]))
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
            time.sleep(0.2)
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
            try:
                lo_n, hi_n = float(lo or 0), float(hi or 0)
            except (TypeError, ValueError):
                lo_n = hi_n = 0
            if lo_n or hi_n:
                # RemoteOK values are typically annual USD; keep /yr unless clearly hourly.
                if lo_n and hi_n and max(lo_n, hi_n) <= 500:
                    salary = f"${int(lo_n)}-${int(hi_n)}/hr" if lo_n != hi_n else f"${int(lo_n)}/hr"
                elif lo_n and hi_n:
                    salary = f"${int(lo_n):,}-${int(hi_n):,}/yr"
                else:
                    v = int(lo_n or hi_n)
                    salary = f"${v}/hr" if v <= 500 else f"${v:,}/yr"
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


def _from_arbeitnow(keywords: List[str], max_pages: int = 20) -> Tuple[List[Job], List[dict]]:
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
    for fallback in ("contract", "freelance", "consultant", "temporary", "annotator", "labeling", "rlhf"):
        if fallback not in tags:
            tags.append(fallback)

    seen = set()
    for tag in tags[:14]:
        try:
            resp = requests.get(
                "https://jobicy.com/api/v2/remote-jobs",
                params={"count": 100, "tag": tag},
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
                try:
                    lo_n = float(lo) if lo not in (None, "") else 0
                    hi_n = float(hi) if hi not in (None, "") else 0
                except (TypeError, ValueError):
                    lo_n = hi_n = 0
                if lo_n or hi_n:
                    currency = (item.get("salaryCurrency") or "USD").upper()
                    prefix = "$" if currency in ("USD", "US$") else f"{currency} "
                    if lo_n and hi_n and lo_n != hi_n:
                        salary = f"{prefix}{int(lo_n):,}-{prefix}{int(hi_n):,}/yr".replace(
                            f"{prefix}{prefix}", prefix
                        )
                        # Avoid "$50,000-$60,000" doubling when prefix is $
                        if prefix == "$":
                            salary = f"${int(lo_n):,}-${int(hi_n):,}/yr"
                    else:
                        salary = f"{prefix}{int(lo_n or hi_n):,}/yr"
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

    target = max(1, int(min_jobs or 1))

    def progress(message: str, collected: int = 0, **extra):
        if on_progress:
            on_progress(
                {
                    "message": message,
                    "collected": collected,
                    "target": target,
                    **extra,
                }
            )

    collected: List[Job] = []
    failed: List[dict] = []
    seen = set()

    # Deeper Arbeitnow pagination when the user asks for more jobs.
    arbeitnow_pages = max(12, min(40, target + 8))

    def add_jobs(jobs: List[Job]) -> int:
        added = 0
        for job in jobs:
            if len(collected) >= target:
                break
            key = (job.url or "").rstrip("/").lower()
            if not key or key in seen:
                continue
            seen.add(key)
            collected.append(job)
            added += 1
            progress(
                f"Collected {len(collected)} / {target} jobs from online search…",
                len(collected),
                failed_urls=failed,
            )
        return added

    # Round 1: standard sources. Round 2: deeper Arbeitnow if still short.
    source_passes = [
        [
            ("Remotive", lambda: _from_remotive(keywords)),
            ("RemoteOK", lambda: _from_remoteok(keywords)),
            ("Arbeitnow", lambda: _from_arbeitnow(keywords, max_pages=arbeitnow_pages)),
            ("Jobicy", lambda: _from_jobicy(keywords)),
        ],
        [
            (
                "Arbeitnow (deeper)",
                lambda: _from_arbeitnow(keywords, max_pages=min(50, arbeitnow_pages + 15)),
            ),
            ("Remotive (retry)", lambda: _from_remotive(keywords)),
            ("Jobicy (retry)", lambda: _from_jobicy(keywords)),
        ],
    ]

    for pass_idx, sources in enumerate(source_passes, start=1):
        if len(collected) >= target:
            break
        progress(
            f"Online pass {pass_idx}: filling to {target} contract roles…",
            len(collected),
            failed_urls=failed,
        )
        grew = False
        for name, fetcher in sources:
            if len(collected) >= target:
                break
            progress(
                f"Searching {name} for contract roles: {', '.join(keywords[:5])}…",
                len(collected),
                failed_urls=failed,
            )
            try:
                jobs, errs = fetcher()
                failed.extend(errs)
                if add_jobs(jobs):
                    grew = True
            except Exception as exc:  # noqa: BLE001
                failed.append({"url": name, "error": str(exc)})
                progress(f"{name} failed: {exc}", len(collected), failed_urls=failed)
        if pass_idx > 1 and not grew:
            break

    # Prefer title hits first, then trim only if we overshot.
    def rank(job: Job) -> Tuple[int, str]:
        title_hit = 0 if matches_keywords(job.title, "", keywords) else 1
        return (title_hit, job.title.lower())

    collected.sort(key=rank)
    if len(collected) > target:
        collected = collected[:target]

    if len(collected) < target:
        progress(
            f"Stopped at {len(collected)} / {target} — sources exhausted.",
            len(collected),
            failed_urls=failed,
            finished=True,
        )
    else:
        progress(
            f"Done. Collected {len(collected)} / {target} contract jobs from online search.",
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
