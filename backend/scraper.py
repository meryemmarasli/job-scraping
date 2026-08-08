"""Scrape job boards: static HTML first, Playwright fallback for JS pages."""

from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable
from urllib import robotparser
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from models import Job

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36 JobScraper/1.0"
)

REQUEST_TIMEOUT = 25
DELAY_SECONDS = 0.75
MAX_PAGES_PER_BOARD = 8
MAX_TOTAL_FETCHES = 120

ProgressCallback = Callable[[dict], None]


def parse_keywords(text: str) -> list[str]:
    parts = []
    for chunk in re.split(r"[,;\n]+", text or ""):
        item = chunk.strip()
        if item and item.lower() not in {p.lower() for p in parts}:
            parts.append(item)
    return parts


def matches_keywords(title: str, description: str, keywords: list[str]) -> bool:
    if not keywords:
        return True
    blob = f"{title}\n{description}".lower()
    return any(kw.lower() in blob for kw in keywords)


# Require contract / freelance / temporary indicators — exclude pure full-time permanent.
_CONTRACT_POSITIVE = (
    "contract",
    "contractor",
    "freelance",
    "freelancer",
    "consulting",
    "consultant",
    "temporary",
    "fixed-term",
    "fixed term",
    "1099",
    "independent contractor",
    "contingent",
    "gig work",
    "hourly contractor",
    "part-time contract",
    "temp role",
    "temp position",
)

_CONTRACT_TYPE_TOKENS = {
    "contract",
    "contractor",
    "freelance",
    "freelancer",
    "temporary",
    "temp",
    "consulting",
    "consultant",
    "contingent",
    "gig",
    "1099",
    "fixed-term",
    "fixed_term",
    "part_time",
    "part-time",
}


_LABELING_PLATFORMS = (
    "mercor",
    "surge",
    "surgehq",
    "scale ai",
    "scale.ai",
    "outlier",
    "remotasks",
    "alignerr",
    "dataannotation",
    "data annotation",
    "invisible technologies",
    "toloka",
    "appen",
    "lionbridge",
    "telus international",
)


def is_contract_job(
    title: str = "",
    description: str = "",
    *,
    job_type: str = "",
    tags: list[str] | None = None,
    company: str = "",
    source: str = "",
) -> bool:
    """True when listing looks like contract / freelance / temporary work."""
    platform_blob = f"{company} {source}".lower()
    if any(p in platform_blob for p in _LABELING_PLATFORMS):
        return True

    type_blob = " ".join(
        [
            (job_type or "").lower().replace("-", "_").replace(" ", "_"),
            " ".join((t or "").lower().replace("-", "_").replace(" ", "_") for t in (tags or [])),
        ]
    )
    tokens = set(re.split(r"[^a-z0-9_+]+", type_blob))
    if tokens & _CONTRACT_TYPE_TOKENS:
        return True

    blob = f"{title}\n{description}\n{job_type}\n{' '.join(tags or [])}".lower()
    if any(term in blob for term in _CONTRACT_POSITIVE):
        return True
    if re.search(r"\btemp\b", blob) or re.search(r"\bgig\b", blob):
        return True
    return False


def enrich_labeling_fields(
    *,
    title: str = "",
    description: str = "",
    company: str = "",
    location: str = "",
    salary: str = "",
) -> dict:
    """
    Heuristically fill AI labeling / expert-contributor fields from listing text.
    Tuned for Mercor / Surge / Scale / Outlier-style postings.
    """
    blob = f"{title}\n{company}\n{location}\n{salary}\n{description}"
    low = blob.lower()
    out: dict = {
        "employment_type": "contract",
        "work_mode": "remote",
    }

    # Pay / rate
    pay = salary
    if not pay:
        m = re.search(
            r"(\$\s?\d{1,3}(?:,\d{3})*(?:\.\d+)?(?:\s*[-–to]+\s*\$?\d{1,3}(?:,\d{3})*(?:\.\d+)?)?\s*(?:/\s*(?:hr|hour)|per\s*hour|hourly)?)",
            blob,
            flags=re.I,
        )
        if m:
            pay = _clean(m.group(1))
        else:
            m = re.search(
                r"(\d{2,3}\s*[-–]\s*\d{2,3}\s*(?:USD|usd)?\s*(?:/\s*(?:hr|hour)|per\s*hour))",
                blob,
                flags=re.I,
            )
            if m:
                pay = _clean(m.group(1))
    if pay:
        out["pay_rate"] = pay[:120]
        out["salary"] = pay[:120]

    # Hours / week
    hm = re.search(
        r"(\d{1,2}\s*[-–to]+\s*\d{1,2}\s*(?:hours?|hrs?)\s*(?:/\s*week|per\s*week|weekly)?|"
        r"(?:up to|around|approx\.?|approximately)?\s*\d{1,2}\s*(?:hours?|hrs?)\s*(?:/\s*week|per\s*week))",
        blob,
        flags=re.I,
    )
    if hm:
        out["hours_per_week"] = _clean(hm.group(1))[:80]

    # Duration
    dm = re.search(
        r"((?:project|engagement|contract)\s+(?:length|duration)[:\s]+[^.\n]{5,60}|"
        r"\d+\s*[-–]\s*\d+\s*(?:weeks?|months?)|"
        r"rolling\s+basis|"
        r"open[- ]ended)",
        blob,
        flags=re.I,
    )
    if dm:
        out["duration"] = _clean(dm.group(1))[:120]

    # Work mode
    if re.search(r"\basync(?:hronous)?\b", low):
        out["work_mode"] = "async remote"
    elif re.search(r"\bhybrid\b", low):
        out["work_mode"] = "hybrid"
    elif re.search(r"\bremote\b|\bwork from home\b|\bwfh\b", low):
        out["work_mode"] = "remote"

    # Employment
    if re.search(r"\bfreelance\b", low):
        out["employment_type"] = "freelance"
    elif re.search(r"\btemporary\b|\btemp\b", low):
        out["employment_type"] = "temporary"
    elif re.search(r"\bcontract\b|\bcontractor\b|\b1099\b", low):
        out["employment_type"] = "contract"

    # Domain
    domain_map = [
        (r"\b(biology|biomedical|life[- ]science|molecular)\b", "Biology / life sciences"),
        (r"\b(math(?:ematics)?|statistic|proof[- ]based)\b", "Mathematics"),
        (r"\b(physics|chemistry|stem)\b", "STEM"),
        (r"\b(software|coding|programming|software engineer|python|javascript)\b", "Coding / engineering"),
        (r"\b(legal|law|attorney)\b", "Legal"),
        (r"\b(medical|clinical|healthcare|physician)\b", "Medical / healthcare"),
        (r"\b(finance|accounting|economics)\b", "Finance / economics"),
        (r"\b(linguistics?|nlp|language)\b", "Language / NLP"),
    ]
    for pattern, label in domain_map:
        if re.search(pattern, low):
            out["domain"] = label
            break

    # Task type
    task_hits = []
    task_map = [
        (r"\brlhf\b|reinforcement learning from human feedback", "RLHF"),
        (r"\bpreference\b.*\b(rank|rating|label)|pairwise comparison", "Preference ranking"),
        (r"\b(data )?label(?:ing|ling)\b|\bannotat", "Data labeling / annotation"),
        (r"\bevaluat(?:e|ion)|model (?:eval|response)", "Model evaluation"),
        (r"\bred[- ]?team", "Red teaming"),
        (r"\bocr\b", "OCR / extraction QA"),
        (r"\bprompt(?:s|ing)?\b", "Prompt writing"),
        (r"\bcontent moderation\b", "Content moderation"),
    ]
    for pattern, label in task_map:
        if re.search(pattern, low) and label not in task_hits:
            task_hits.append(label)
    if task_hits:
        out["task_type"] = ", ".join(task_hits[:4])

    # Languages
    if re.search(r"\benglish\b", low):
        out["languages"] = "English"
        if re.search(r"\bbilingual\b|\bfluent in\b", low):
            out["languages"] = "English + additional (see description)"

    # Client / partner lab (often anonymized)
    cm = re.search(
        r"(?:partner(?:ing)? with|for)\s+(?:a |an |one of (?:the )?)?(leading (?:frontier )?AI (?:research )?lab[^.\n]{0,40}|OpenAI|Anthropic|Google DeepMind|Meta AI|Microsoft)",
        blob,
        flags=re.I,
    )
    if cm:
        out["client_partner"] = _clean(cm.group(1))[:200]

    # Tools
    tools = []
    for pattern, label in (
        (r"\blatex\b", "LaTeX"),
        (r"\bpython\b", "Python"),
        (r"\bjupyter\b", "Jupyter"),
        (r"\bsql\b", "SQL"),
        (r"\bmatlab\b", "MATLAB"),
        (r"\bexcel\b", "Excel"),
    ):
        if re.search(pattern, low):
            tools.append(label)
    if tools:
        out["tools_skills"] = ", ".join(dict.fromkeys(tools))[:200]

    # Screening
    screen_bits = []
    if re.search(r"\bai interview\b", low):
        screen_bits.append("AI interview")
    if re.search(r"\bscreening\b|\bassessment\b|\bqualification\b", low):
        screen_bits.append("Screening / assessment")
    if re.search(r"\bresume\b|\bcv\b", low):
        screen_bits.append("Resume / profile")
    if screen_bits:
        out["screening"] = ", ".join(dict.fromkeys(screen_bits))[:200]

    # Section snips for responsibilities / requirements / preferred
    def _section(*headers: str, limit: int = 900) -> str:
        for header in headers:
            m = re.search(
                rf"(?:^|\n)\s*{header}\s*[:\n]+(.+?)(?=\n\s*(?:[A-Z][A-Za-z /&-]{{2,40}})\s*[:\n]|\Z)",
                description,
                flags=re.I | re.S,
            )
            if m:
                return _clean(m.group(1))[:limit]
        return ""

    responsibilities = _section(
        "What you.?ll do",
        "What you will do",
        "Key Responsibilities",
        "Responsibilities",
        "About the role",
    )
    requirements = _section(
        "Required Qualifications",
        "What we.?re looking for",
        "Who You Are",
        "Requirements",
        "Qualifications",
    )
    preferred = _section(
        "Preferred Qualifications",
        "Nice to have",
        "Bonus",
        "Preferred",
    )
    if responsibilities:
        out["responsibilities"] = responsibilities
    if requirements:
        out["requirements"] = requirements
    if preferred:
        out["preferred"] = preferred

    return out


def _clean(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def _headers() -> dict[str, str]:
    return {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    }


def robots_allowed(url: str) -> bool:
    """
    Honor robots.txt using the same browser User-Agent we scrape with.

    Important: urllib.robotparser.RobotFileParser.read() fetches robots.txt with
    Python's default UA. Many boards (e.g. Mercor) respond 401/403 to that, and
    robotparser then sets disallow_all=True — which falsely blocks every URL.
    """
    try:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return True
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        resp = requests.get(
            robots_url,
            headers=_headers(),
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
        )
        # Missing / unreadable robots.txt → allow (common convention).
        if resp.status_code in (401, 403, 404, 410):
            return True
        if resp.status_code >= 400:
            return True

        rp = robotparser.RobotFileParser()
        rp.parse(resp.text.splitlines())
        # Match against browser UA without our scraper suffix so we use the
        # site's User-agent: * rules, not a non-existent bot group.
        agent = USER_AGENT.replace(" JobScraper/1.0", "").strip() or USER_AGENT
        return rp.can_fetch(agent, url)
    except Exception:
        return True


def fetch_static(url: str) -> tuple[str, str]:
    response = requests.get(
        url,
        headers=_headers(),
        timeout=REQUEST_TIMEOUT,
        allow_redirects=True,
    )
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return response.text, response.url


def fetch_playwright(url: str) -> tuple[str, str]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=USER_AGENT)
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(1500)
            html = page.content()
            final = page.url
        finally:
            browser.close()
    return html, final


def looks_js_heavy_or_empty(html: str) -> bool:
    soup = BeautifulSoup(html, "lxml")
    text = _clean(soup.get_text(" ", strip=True))
    if len(text) < 200:
        return True
    # Few anchors + lots of scripts is a common SPA signal.
    anchors = soup.find_all("a", href=True)
    scripts = soup.find_all("script")
    if len(anchors) < 5 and len(scripts) > 8:
        return True
    root = soup.select_one("#root, #app, [data-reactroot]")
    if root and len(_clean(root.get_text(" ", strip=True))) < 80:
        return True
    return False


def extract_job_links(board_url: str, html: str, limit: int = 50) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    parsed = urlparse(board_url)
    host = parsed.netloc.lower()
    links: list[str] = []
    seen: set[str] = set()

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "mailto:", "javascript:")):
            continue
        full = urljoin(board_url, href)
        fp = urlparse(full)
        if fp.scheme not in ("http", "https"):
            continue
        path = fp.path.rstrip("/")
        low_host = fp.netloc.lower()

        looks_like_job = False
        if "greenhouse.io" in low_host and "/jobs/" in path:
            looks_like_job = True
        elif "lever.co" in low_host:
            parts = [p for p in path.split("/") if p]
            if len(parts) >= 2:
                looks_like_job = True
        elif "ashbyhq.com" in low_host and "/jobs/" in path:
            looks_like_job = True
        elif "workable.com" in low_host and ("/j/" in path or "/jobs/" in path):
            looks_like_job = True
        elif re.search(r"/(jobs?|careers?|positions?|openings?|vacancies)/.+", path, re.I):
            looks_like_job = True
        elif re.search(r"/(job|position|opening)[_-]?\w+", path, re.I):
            looks_like_job = True

        # Prefer same site for generic careers pages.
        if looks_like_job and host not in low_host and low_host not in host:
            if not any(x in low_host for x in ("greenhouse", "lever", "ashby", "workable")):
                continue

        key = f"{fp.scheme}://{fp.netloc}{path}".lower()
        if not looks_like_job or key in seen:
            continue
        seen.add(key)
        links.append(full)
        if len(links) >= limit:
            break
    return links


def _meta(soup: BeautifulSoup, *keys: str) -> str:
    for key in keys:
        tag = soup.find("meta", attrs={"property": key}) or soup.find("meta", attrs={"name": key})
        if tag and tag.get("content"):
            return _clean(tag["content"])
    return ""


def _first_text(soup: BeautifulSoup, selectors: list[str]) -> str:
    for sel in selectors:
        node = soup.select_one(sel)
        if node:
            text = _clean(node.get_text(" ", strip=True))
            if text:
                return text
    return ""


def parse_job_page(url: str, html: str, source_board: str = "") -> Job:
    soup = BeautifulSoup(html, "lxml")
    title = (
        _meta(soup, "og:title", "twitter:title")
        or _first_text(soup, ["h1", ".job-title", "[data-testid='job-title']", "title"])
    )
    company = (
        _meta(soup, "og:site_name")
        or _first_text(
            soup,
            [
                ".company",
                ".company-name",
                "[data-testid='company-name']",
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
            ".location",
            ".job-location",
            "[data-testid='job-location']",
            "[class*='location']",
        ],
    )
    description = _first_text(
        soup,
        [
            ".job-description",
            "#content",
            "[data-testid='job-description']",
            "article",
            "main",
        ],
    )
    if not description:
        for tag in soup(["script", "style", "noscript", "nav", "footer"]):
            tag.decompose()
        description = _clean(soup.get_text(" ", strip=True))[:4000]

    salary = ""
    salary_match = re.search(
        r"(\$\s?\d{1,3}(?:,\d{3})*(?:\.\d+)?(?:\s?[-–to]+\s?\$?\d{1,3}(?:,\d{3})*(?:\.\d+)?)?(?:\s*/\s*(?:yr|year|hr|hour))?)",
        f"{title} {description}",
        flags=re.I,
    )
    if salary_match:
        salary = _clean(salary_match.group(1))

    posted = ""
    date_match = re.search(
        r"(Posted|Date|Published)[:\s]+([A-Za-z0-9 ,/-]{4,40})",
        description,
        flags=re.I,
    )
    if date_match:
        posted = _clean(date_match.group(2))
    time_tag = soup.find("time")
    if time_tag:
        posted = posted or _clean(time_tag.get("datetime") or time_tag.get_text(" ", strip=True))

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
        salary=(extras.get("salary") or salary)[:120],
        pay_rate=(extras.get("pay_rate") or salary)[:120],
        description=_clean(description)[:5000],
        posted_date=posted[:120],
        url=url,
        source_board=source_board or urlparse(url).netloc,
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


def find_next_page(board_url: str, html: str) -> str | None:
    soup = BeautifulSoup(html, "lxml")
    for a in soup.find_all("a", href=True):
        label = _clean(a.get_text(" ", strip=True)).lower()
        if label in {"next", "next page", "→", "›", "older"} or "next" in (a.get("rel") or []):
            return urljoin(board_url, a["href"])
    return None


def scrape_url_once(url: str) -> tuple[str, str, str]:
    """Return (html, final_url, method)."""
    html, final = fetch_static(url)
    method = "static"
    if looks_js_heavy_or_empty(html):
        try:
            html, final = fetch_playwright(url)
            method = "playwright"
        except Exception:
            method = "static"
    return html, final, method


def scrape_jobs(
    urls: list[str],
    keywords: list[str],
    min_jobs: int,
    on_progress: ProgressCallback | None = None,
) -> tuple[list[Job], list[dict]]:
    """
    Scrape until min_jobs contract jobs are collected (or caps hit).
    Keywords are optional — when provided, listings must also match them.
    Returns (jobs, failed_urls).
    """
    collected: list[Job] = []
    seen_urls: set[str] = set()
    failed: list[dict] = []
    fetches = 0

    def progress(message: str, **extra):
        if on_progress:
            on_progress(
                {
                    "message": message,
                    "collected": len(collected),
                    "target": min_jobs,
                    **extra,
                }
            )

    clean_urls = []
    for raw in urls:
        u = (raw or "").strip()
        if not u:
            continue
        if not u.startswith(("http://", "https://")):
            u = "https://" + u
        clean_urls.append(u)

    if not clean_urls:
        failed.append({"url": "(boards)", "error": "No board URLs provided"})
        progress("Add at least one board URL.", failed_urls=failed, finished=True)
        return [], failed

    if not keywords:
        progress(
            "Scraping boards (no keyword filter — contract roles only)…",
            failed_urls=failed,
        )
    else:
        kw_label = ", ".join(keywords[:5])
        progress(
            f"Scraping boards for contract roles (keywords: {kw_label})…",
            failed_urls=failed,
        )

    for board in clean_urls:
        if len(collected) >= min_jobs or fetches >= MAX_TOTAL_FETCHES:
            break

        if not robots_allowed(board):
            failed.append({"url": board, "error": "Blocked by robots.txt"})
            progress(f"Skipped {board} (robots.txt)", failed_urls=failed)
            continue

        page_url = board
        pages_done = 0

        while (
            page_url
            and pages_done < MAX_PAGES_PER_BOARD
            and len(collected) < min_jobs
            and fetches < MAX_TOTAL_FETCHES
        ):
            try:
                progress(f"Fetching {page_url}…")
                time.sleep(DELAY_SECONDS)
                html, final_url, method = scrape_url_once(page_url)
                fetches += 1
                pages_done += 1

                links = extract_job_links(final_url, html)
                # If no child links, treat the URL itself as a job page.
                targets = links or [final_url]

                for link in targets:
                    if len(collected) >= min_jobs or fetches >= MAX_TOTAL_FETCHES:
                        break
                    key = link.rstrip("/").lower()
                    if key in seen_urls:
                        continue
                    if not robots_allowed(link):
                        continue

                    try:
                        time.sleep(DELAY_SECONDS)
                        job_html, job_final, _ = scrape_url_once(link)
                        fetches += 1
                        job = parse_job_page(job_final, job_html, source_board=urlparse(board).netloc)
                        if not job.title:
                            continue
                        if not matches_keywords(job.title, job.description, keywords):
                            continue
                        if not is_contract_job(
                            job.title,
                            job.description,
                            company=job.company,
                            source=job.source_board,
                        ):
                            continue
                        seen_urls.add(key)
                        collected.append(job)
                        progress(
                            f"Collected {len(collected)} / {min_jobs} jobs…",
                            failed_urls=failed,
                            method=method,
                        )
                    except Exception as exc:  # noqa: BLE001
                        failed.append({"url": link, "error": str(exc)})
                        progress(
                            f"Failed listing {link}: {exc}",
                            failed_urls=failed,
                        )

                next_url = find_next_page(final_url, html)
                if not next_url or next_url.rstrip("/") == page_url.rstrip("/"):
                    break
                page_url = next_url
            except Exception as exc:  # noqa: BLE001
                failed.append({"url": page_url, "error": str(exc)})
                progress(f"Failed board {page_url}: {exc}", failed_urls=failed)
                break

    progress(
        f"Done. Collected {len(collected)} / {min_jobs} matching jobs.",
        failed_urls=failed,
        finished=True,
    )
    return collected, failed


def run_scrape_async(
    urls: list[str],
    keywords_text: str,
    min_jobs: int,
    on_progress: ProgressCallback,
    on_done: Callable[[list[Job], list[dict]], None],
) -> None:
    keywords = parse_keywords(keywords_text)

    def work():
        try:
            jobs, failed = scrape_jobs(urls, keywords, min_jobs, on_progress=on_progress)
            on_done(jobs, failed)
        except Exception as exc:  # noqa: BLE001
            on_progress(
                {
                    "message": f"Scrape crashed: {exc}",
                    "collected": 0,
                    "target": min_jobs,
                    "finished": True,
                    "error": str(exc),
                    "failed_urls": [],
                }
            )
            on_done([], [{"url": "(run)", "error": str(exc)}])

    # Run in a background thread so FastAPI can stream progress.
    executor = ThreadPoolExecutor(max_workers=1)
    executor.submit(work)
