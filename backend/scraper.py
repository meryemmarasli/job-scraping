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
DELAY_SECONDS = 0.55
MAX_PAGES_PER_BOARD = 8
MAX_TOTAL_FETCHES = 120
MAX_LINKS_PER_PAGE = 80

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


_LABELING_ROLE_TERMS = (
    "annotat",
    "labeling",
    "labelling",
    "data label",
    "rlhf",
    "ai trainer",
    "ai training",
    "data contributor",
    "expert network",
    "preference rank",
    "model evaluat",
    "human feedback",
    "red team",
    "ocr specialist",
    "prompt writer",
    "llm evaluat",
)


def is_contract_job(
    title: str = "",
    description: str = "",
    *,
    job_type: str = "",
    tags: list[str] | None = None,
    company: str = "",
    source: str = "",
    allow_labeling_roles: bool = True,
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
    # Labeling / AI-trainer gigs are almost always contractor work even when
    # the posting omits the word "contract".
    if allow_labeling_roles and any(term in blob for term in _LABELING_ROLE_TERMS):
        if not re.search(r"\bfull[- ]?time\b.*\bpermanent\b|\bpermanent\b.*\bfull[- ]?time\b", blob):
            return True
    return False


def extract_pay_rate(*texts: str, prefer_hourly: bool = True) -> str:
    """
    Pull the most likely pay figure from listing text.

    Prefers explicit hourly rates (common for Mercor/Surge/Scale-style gigs)
    over the first random $-amount on the page (funding, headcount, years, etc.).
    """
    blob = " ".join(t for t in texts if t)
    if not blob:
        return ""

    # Normalize dashes / spacing so ranges parse cleanly.
    normalized = (
        blob.replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("–", "-")
        .replace("—", "-")
        .replace("／", "/")
    )
    normalized = re.sub(r"\s+", " ", normalized)

    candidates: list[tuple[int, str]] = []

    patterns = [
        # $70-75/hr, $70 – $75 per hour, $45/hour, $90 to $110 an hour
        (
            100,
            r"(\$\s?\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?(?:\s*(?:-|to)\s*\$?\s?\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?)?\s*(?:/\s*h(?:r|our)?|per\s*hour|an\s*hour|hourly))",
        ),
        # 70-75 USD/hr, 45 USD per hour
        (
            95,
            r"(\d{2,3}(?:\.\d{1,2})?\s*(?:(?:-|to)\s*\d{2,3}(?:\.\d{1,2})?)?\s*(?:USD|usd|US\$)\s*(?:/\s*h(?:r|our)?|per\s*hour))",
        ),
        # USD 70-75/hr
        (
            95,
            r"((?:USD|US\$)\s*\d{2,3}(?:\.\d{1,2})?(?:\s*(?:-|to)\s*\d{2,3}(?:\.\d{1,2})?)?\s*(?:/\s*h(?:r|our)?|per\s*hour))",
        ),
        # Pay/rate/compensation: $70-75 or $90 to $110 nearby
        (
            90,
            r"(?:(?:pay|rate|compensation|earning|wage|hourly)s?\s*(?:of|is|:|-)?\s*)"
            r"(\$\s?\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?(?:\s*(?:-|to)\s*\$?\s?\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?)?"
            r"(?:\s*(?:/\s*h(?:r|our)?|per\s*hour|hourly|an\s*hour))?)",
        ),
        # Bare range with /hr and no $ : 40-60/hr
        (
            80,
            r"(\b\d{2,3}(?:\.\d{1,2})?\s*(?:-|to)\s*\d{2,3}(?:\.\d{1,2})?\s*(?:/\s*h(?:r|our)?|per\s*hour)\b)",
        ),
        # Annual salary ranges when no hourly found
        (
            40,
            r"(\$\s?\d{2,3}(?:,\d{3})+(?:\.\d{1,2})?(?:\s*(?:-|to)\s*\$?\s?\d{2,3}(?:,\d{3})+(?:\.\d{1,2})?)?\s*(?:/\s*(?:yr|year)|per\s*year|annually)?)",
        ),
        (
            35,
            r"((?:USD|US\$)\s*\d{2,3}(?:,\d{3})+(?:\s*(?:-|to)\s*\d{2,3}(?:,\d{3})+)?)",
        ),
    ]

    for base_score, pattern in patterns:
        for m in re.finditer(pattern, normalized, flags=re.I):
            raw = _clean(m.group(1))
            if not raw:
                continue
            start = max(0, m.start() - 40)
            end = min(len(normalized), m.end() + 40)
            ctx = normalized[start:end].lower()

            score = base_score
            if re.search(r"/\s*h(?:r|our)?|per\s*hour|hourly|an\s*hour", raw, re.I):
                score += 25
            if re.search(r"\b(pay|rate|compensation|wage|earning|hourly)\b", ctx):
                score += 15
            if re.search(r"/\s*(?:yr|year)|per\s*year|annually|salary", raw + " " + ctx, re.I):
                score -= 10 if prefer_hourly else 0
            # Reject obvious non-pay contexts
            if re.search(
                r"\b(founded|employees?|headcount|funding|raised|valuation|users?|models?)\b",
                ctx,
            ):
                score -= 50
            # Reject years like $2024
            if re.search(r"\$?\s?20[1-3]\d\b", raw):
                score -= 80

            formatted = _format_pay(raw, prefer_hourly=prefer_hourly)
            if not formatted:
                continue
            candidates.append((score, formatted))

    if not candidates:
        return ""
    candidates.sort(key=lambda x: (-x[0], len(x[1])))
    best_score, best = candidates[0]
    if best_score < 30:
        return ""
    return best[:120]


def _format_pay(raw: str, prefer_hourly: bool = True) -> str:
    """Normalize a matched pay string to a consistent display form."""
    text = _clean(raw)
    text = text.replace("US$", "USD")
    text = re.sub(r"\s*-\s*", "-", text)
    text = re.sub(r"\s+to\s+", "-", text, flags=re.I)
    text = re.sub(r"\$\s+", "$", text)

    hourly = bool(
        re.search(r"/\s*h(?:r|our)?|per\s*hour|an\s*hour|hourly", text, re.I)
    )
    yearly = bool(re.search(r"/\s*(?:yr|year)|per\s*year|annually", text, re.I))

    # Extract numeric parts
    nums = re.findall(r"\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?", text)
    if not nums:
        return ""
    values = []
    for n in nums[:2]:
        try:
            values.append(float(n.replace(",", "")))
        except ValueError:
            continue
    if not values:
        return ""

    # Drop absurd hourly candidates (e.g. $50000/hr misread)
    if hourly and any(v > 500 for v in values):
        hourly = False
        yearly = True
    # Labeling gigs: numbers 15–400 without unit are almost always hourly
    if prefer_hourly and not hourly and not yearly and all(15 <= v <= 400 for v in values):
        hourly = True
    # Large numbers without unit → annual
    if not hourly and not yearly and any(v >= 1000 for v in values):
        yearly = True

    def fmt(v: float) -> str:
        if v >= 1000:
            return f"${v:,.0f}"
        if abs(v - int(v)) < 1e-6:
            return f"${int(v)}"
        return f"${v:.2f}".rstrip("0").rstrip(".")

    if len(values) >= 2 and values[0] != values[1]:
        lo, hi = (values[0], values[1]) if values[0] <= values[1] else (values[1], values[0])
        core = f"{fmt(lo)}-{fmt(hi)}"
    else:
        core = fmt(values[0])

    if hourly:
        return f"{core}/hr"
    if yearly:
        return f"{core}/yr"
    return core


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

    # Pay / rate — prefer structured salary only if it already looks like pay;
    # otherwise mine the listing for the best hourly/annual match.
    structured = _format_pay(salary) if salary else ""
    mined = extract_pay_rate(title, description, salary)
    if structured and mined:
        # Prefer hourly mined rate over annual API figures for contract gigs.
        if "/hr" in mined and "/hr" not in structured:
            pay = mined
        elif "/hr" in structured:
            pay = structured
        else:
            # Prefer the more specific / complete string
            pay = structured if len(structured) >= len(mined) else mined
    else:
        pay = structured or mined
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


def extract_job_links(board_url: str, html: str, limit: int = MAX_LINKS_PER_PAGE) -> list[str]:
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
        elif "mercor.com" in low_host and "/jobs/" in path and path.count("/") >= 2:
            looks_like_job = True
        elif re.search(r"/(jobs?|careers?|positions?|openings?|vacancies)/.+", path, re.I):
            looks_like_job = True
        elif re.search(r"/(job|position|opening)[_-]?\w+", path, re.I):
            looks_like_job = True

        # Prefer same site for generic careers pages.
        if looks_like_job and host not in low_host and low_host not in host:
            if not any(
                x in low_host for x in ("greenhouse", "lever", "ashby", "workable", "mercor")
            ):
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

    salary = extract_pay_rate(title, description)

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

    pay = (extras.get("pay_rate") or salary)[:120]

    return Job(
        title=_clean(title)[:300],
        company=_clean(company)[:200],
        location=_clean(location)[:200],
        salary=pay,
        pay_rate=pay,
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
    Caps scale with min_jobs so larger targets keep digging.
    Returns (jobs, failed_urls).
    """
    collected: list[Job] = []
    seen_urls: set[str] = set()
    failed: list[dict] = []
    fetches = 0
    target = max(1, int(min_jobs or 1))
    max_pages = max(MAX_PAGES_PER_BOARD, min(40, target + 4))
    max_fetches = max(MAX_TOTAL_FETCHES, min(400, target * 20))
    link_limit = max(MAX_LINKS_PER_PAGE, min(200, target * 10))

    def progress(message: str, **extra):
        if on_progress:
            on_progress(
                {
                    "message": message,
                    "collected": len(collected),
                    "target": target,
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
            f"Scraping boards until {target} contract roles (no keyword filter)…",
            failed_urls=failed,
        )
    else:
        kw_label = ", ".join(keywords[:5])
        progress(
            f"Scraping boards until {target} contract roles (keywords: {kw_label})…",
            failed_urls=failed,
        )

    # Keep walking every board until target is met (or caps / boards exhausted).
    for board in clean_urls:
        if len(collected) >= target or fetches >= max_fetches:
            break

        if not robots_allowed(board):
            failed.append({"url": board, "error": "Blocked by robots.txt"})
            progress(f"Skipped {board} (robots.txt)", failed_urls=failed)
            continue

        page_url = board
        pages_done = 0

        while (
            page_url
            and pages_done < max_pages
            and len(collected) < target
            and fetches < max_fetches
        ):
            try:
                progress(
                    f"Fetching {page_url}… ({len(collected)}/{target})",
                    failed_urls=failed,
                )
                time.sleep(DELAY_SECONDS)
                html, final_url, method = scrape_url_once(page_url)
                fetches += 1
                pages_done += 1

                links = extract_job_links(final_url, html, limit=link_limit)
                # If no child links, treat the URL itself as a job page.
                targets = links or [final_url]

                for link in targets:
                    if len(collected) >= target or fetches >= max_fetches:
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
                        job = parse_job_page(
                            job_final, job_html, source_board=urlparse(board).netloc
                        )
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
                            f"Collected {len(collected)} / {target} jobs…",
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

    if len(collected) < target:
        progress(
            f"Stopped at {len(collected)} / {target} — boards exhausted or fetch cap hit.",
            failed_urls=failed,
            finished=True,
        )
    else:
        progress(
            f"Done. Collected {len(collected)} / {target} matching jobs.",
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
