"""Scrape AI training / ML job postings and extract structured annotations."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from models import JobAnnotation

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

AI_KEYWORDS = (
    "ai",
    "ml",
    "machine learning",
    "deep learning",
    "llm",
    "large language",
    "nlp",
    "computer vision",
    "data scientist",
    "data engineer",
    "mlops",
    "ai trainer",
    "ai training",
    "prompt engineer",
    "annotation",
    "labeling",
    "labelling",
    "rlhf",
    "reinforcement learning",
    "foundation model",
    "generative ai",
)

SKILL_PATTERNS = [
    r"\bPython\b",
    r"\bPyTorch\b",
    r"\bTensorFlow\b",
    r"\bJAX\b",
    r"\bCUDA\b",
    r"\bKubernetes\b",
    r"\bDocker\b",
    r"\bAWS\b",
    r"\bGCP\b",
    r"\bAzure\b",
    r"\bSQL\b",
    r"\bSpark\b",
    r"\bHugging\s?Face\b",
    r"\bLangChain\b",
    r"\bTransformers\b",
    r"\bLLM\b",
    r"\bNLP\b",
    r"\bComputer Vision\b",
    r"\bRLHF\b",
    r"\bAnnotation\b",
    r"\bLabeling\b",
]


def fetch_html(url: str, timeout: int = 25) -> str:
    response = requests.get(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
        timeout=timeout,
        allow_redirects=True,
    )
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return response.text


def _clean(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def _meta(soup: BeautifulSoup, *keys: str) -> str:
    for key in keys:
        tag = soup.find("meta", attrs={"property": key}) or soup.find("meta", attrs={"name": key})
        if tag and tag.get("content"):
            return _clean(tag["content"])
    return ""


def _first_text(soup: BeautifulSoup, selectors: list[str]) -> str:
    for selector in selectors:
        node = soup.select_one(selector)
        if node:
            text = _clean(node.get_text(" ", strip=True))
            if text:
                return text
    return ""


def _json_ld_jobs(soup: BeautifulSoup) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text() or ""
        raw = raw.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("@type") in ("JobPosting", ["JobPosting"]):
                jobs.append(item)
            graph = item.get("@graph")
            if isinstance(graph, list):
                for node in graph:
                    if isinstance(node, dict) and node.get("@type") == "JobPosting":
                        jobs.append(node)
    return jobs


def _from_json_ld(job: dict[str, Any], url: str, raw_text: str) -> JobAnnotation:
    org = job.get("hiringOrganization") or {}
    if isinstance(org, list):
        org = org[0] if org else {}
    company = org.get("name", "") if isinstance(org, dict) else str(org)

    location = ""
    loc = job.get("jobLocation")
    if isinstance(loc, dict):
        address = loc.get("address") or {}
        if isinstance(address, dict):
            parts = [
                address.get("addressLocality", ""),
                address.get("addressRegion", ""),
                address.get("addressCountry", ""),
            ]
            location = ", ".join(p for p in parts if p)
        else:
            location = str(address)
    elif isinstance(loc, list) and loc:
        location = _from_json_ld({**job, "jobLocation": loc[0]}, url, raw_text).location

    salary = ""
    base = job.get("baseSalary") or {}
    if isinstance(base, dict):
        value = base.get("value")
        if isinstance(value, dict):
            amount = value.get("value") or value.get("minValue")
            currency = base.get("currency") or value.get("currency") or ""
            unit = value.get("unitText") or ""
            if amount:
                salary = f"{currency} {amount}".strip()
                if unit:
                    salary = f"{salary} / {unit}"
        elif value:
            salary = str(value)

    description = _clean(BeautifulSoup(str(job.get("description", "")), "lxml").get_text(" "))
    skills = _extract_skills(f"{job.get('title', '')} {description} {raw_text}")
    requirements = _extract_requirements(description or raw_text)

    return JobAnnotation(
        title=_clean(str(job.get("title", ""))),
        company=_clean(str(company)),
        location=_clean(str(location)),
        employment_type=_clean(str(job.get("employmentType", ""))),
        remote=_infer_remote(f"{location} {description} {raw_text}"),
        salary=_clean(salary),
        skills=skills,
        requirements=requirements,
        description=description[:4000],
        source_url=url,
        scraped_at=datetime.now(timezone.utc).isoformat(),
        raw_text=raw_text[:8000],
    )


def _extract_skills(text: str) -> str:
    found: list[str] = []
    for pattern in SKILL_PATTERNS:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            token = match.group(0).strip()
            if token.lower() not in {s.lower() for s in found}:
                found.append(token)
    return ", ".join(found)


def _extract_requirements(text: str) -> str:
    lines = [ln.strip(" •-\t") for ln in text.splitlines() if ln.strip()]
    bullets = [ln for ln in lines if re.match(r"^(\*|\d+\.|[-•])\s+", ln) or len(ln) < 180]
    keywords = (
        "require",
        "must",
        "experience",
        "qualification",
        "degree",
        "bachelor",
        "master",
        "phd",
        "years",
    )
    picked = [ln for ln in bullets if any(k in ln.lower() for k in keywords)]
    if not picked:
        # Fall back to a short excerpt around "requirements"
        match = re.search(
            r"(requirements?|qualifications?)[:\s]+(.{80,600})",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if match:
            return _clean(match.group(2))[:800]
        return ""
    return " | ".join(_clean(p) for p in picked[:12])


def _infer_remote(text: str) -> str:
    lower = text.lower()
    if re.search(r"\b(remote|work from home|wfh|distributed)\b", lower):
        if re.search(r"\bhybrid\b", lower):
            return "Hybrid"
        return "Remote"
    if re.search(r"\bhybrid\b", lower):
        return "Hybrid"
    if re.search(r"\b(on[- ]?site|in[- ]?office)\b", lower):
        return "On-site"
    return ""


def _extract_salary(text: str) -> str:
    patterns = [
        r"\$\s?\d{2,3}(?:,\d{3})+(?:\s?[-–to]+\s?\$?\d{2,3}(?:,\d{3})+)?(?:\s*/\s*(?:yr|year|hr|hour))?",
        r"(?:USD|EUR|GBP)\s?\d{2,3}(?:,\d{3})+(?:\s?[-–to]+\s?\d{2,3}(?:,\d{3})+)?",
        r"\b\d{2,3}k\s*[-–]\s*\d{2,3}k\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return _clean(match.group(0))
    return ""


def _visible_text(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "noscript", "svg", "footer", "nav"]):
        tag.decompose()
    return soup.get_text("\n", strip=True)


def _generic_extract(soup: BeautifulSoup, url: str, raw_text: str) -> JobAnnotation:
    title = (
        _meta(soup, "og:title", "twitter:title")
        or _first_text(
            soup,
            [
                "h1",
                "[data-testid='job-title']",
                ".job-title",
                ".posting-headline h2",
                "title",
            ],
        )
    )
    company = (
        _meta(soup, "og:site_name")
        or _first_text(
            soup,
            [
                "[data-testid='company-name']",
                ".company",
                ".company-name",
                ".posting-headline .company-name",
                "a[data-qa='company-name']",
            ],
        )
    )
    if not company:
        host = urlparse(url).netloc.replace("www.", "")
        company = host.split(".")[0].title() if host else ""

    location = _first_text(
        soup,
        [
            "[data-testid='job-location']",
            ".location",
            ".job-location",
            ".posting-categories .location",
            "[class*='location']",
        ],
    )
    if not location:
        loc_match = re.search(
            r"(?:Location|Based in|Office)[:\s]+([A-Za-z0-9 ,./\-]+)",
            raw_text,
            flags=re.IGNORECASE,
        )
        location = _clean(loc_match.group(1)) if loc_match else ""

    description = _first_text(
        soup,
        [
            "[data-testid='job-description']",
            "#content",
            ".job-description",
            ".posting-page",
            "article",
            "main",
        ],
    )
    if not description:
        description = _clean(raw_text)[:4000]

    employment = ""
    emp_match = re.search(
        r"\b(Full[- ]?time|Part[- ]?time|Contract|Internship|Temporary)\b",
        raw_text,
        flags=re.IGNORECASE,
    )
    if emp_match:
        employment = emp_match.group(1)

    return JobAnnotation(
        title=_clean(title),
        company=_clean(company),
        location=_clean(location),
        employment_type=_clean(employment),
        remote=_infer_remote(raw_text),
        salary=_extract_salary(raw_text),
        skills=_extract_skills(f"{title} {description} {raw_text}"),
        requirements=_extract_requirements(raw_text),
        description=_clean(description)[:4000],
        source_url=url,
        scraped_at=datetime.now(timezone.utc).isoformat(),
        raw_text=raw_text[:8000],
    )


def looks_like_ai_training_job(annotation: JobAnnotation) -> bool:
    blob = " ".join(
        [
            annotation.title,
            annotation.company,
            annotation.description,
            annotation.skills,
            annotation.requirements,
            annotation.raw_text,
        ]
    ).lower()
    return any(keyword in blob for keyword in AI_KEYWORDS)


def scrape_job_url(url: str) -> JobAnnotation:
    """Fetch a job URL and return structured annotations."""
    html = fetch_html(url)
    soup = BeautifulSoup(html, "lxml")
    raw_text = _visible_text(BeautifulSoup(html, "lxml"))

    json_ld_jobs = _json_ld_jobs(soup)
    if json_ld_jobs:
        return _from_json_ld(json_ld_jobs[0], url, raw_text)
    return _generic_extract(soup, url, raw_text)


def scrape_many(urls: list[str], ai_only: bool = True) -> tuple[list[JobAnnotation], list[dict[str, str]]]:
    """Scrape multiple URLs. Returns (successes, errors)."""
    results: list[JobAnnotation] = []
    errors: list[dict[str, str]] = []
    for url in urls:
        url = url.strip()
        if not url:
            continue
        try:
            annotation = scrape_job_url(url)
            if ai_only and not looks_like_ai_training_job(annotation):
                errors.append(
                    {
                        "url": url,
                        "error": "Page scraped, but it does not look like an AI/ML/training job. "
                        "Disable 'AI jobs only' to keep it anyway.",
                    }
                )
                continue
            results.append(annotation)
        except Exception as exc:  # noqa: BLE001 - surface scrape failures in UI
            errors.append({"url": url, "error": str(exc)})
    return results, errors


# Sample HTML used when the user wants to try the flow without live URLs.
SAMPLE_JOB_HTML = """
<html><head>
<title>Senior AI Training Specialist - LabelForge</title>
<meta property="og:title" content="Senior AI Training Specialist" />
<meta property="og:site_name" content="LabelForge" />
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "JobPosting",
  "title": "Senior AI Training Specialist",
  "description": "<p>Help train and evaluate large language models. Requirements: 3+ years annotation or RLHF experience, strong written English, familiarity with Python preferred. Skills: RLHF, Prompt Engineering, Annotation, LLM evaluation.</p>",
  "employmentType": "FULL_TIME",
  "hiringOrganization": {"@type": "Organization", "name": "LabelForge"},
  "jobLocation": {
    "@type": "Place",
    "address": {
      "@type": "PostalAddress",
      "addressLocality": "San Francisco",
      "addressRegion": "CA",
      "addressCountry": "US"
    }
  },
  "baseSalary": {
    "@type": "MonetaryAmount",
    "currency": "USD",
    "value": {"@type": "QuantitativeValue", "value": 95000, "unitText": "YEAR"}
  }
}
</script>
</head><body>
<h1>Senior AI Training Specialist</h1>
<div class="location">Remote (US)</div>
<div class="job-description">
Help train and evaluate large language models for production AI products.
Requirements:
- 3+ years annotation or RLHF experience
- Strong written English
- Familiarity with Python preferred
</div>
</body></html>
"""


def scrape_sample() -> JobAnnotation:
    soup = BeautifulSoup(SAMPLE_JOB_HTML, "lxml")
    raw_text = _visible_text(BeautifulSoup(SAMPLE_JOB_HTML, "lxml"))
    jobs = _json_ld_jobs(soup)
    if jobs:
        ann = _from_json_ld(jobs[0], "https://example.com/jobs/ai-training-specialist", raw_text)
        ann.remote = _infer_remote(raw_text) or ann.remote
        return ann
    return _generic_extract(soup, "https://example.com/jobs/ai-training-specialist", raw_text)
