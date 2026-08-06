# Job Scraping

A Streamlit app that pulls AI / ML / training job postings from public job boards, extracts structured annotations, lets you review and edit them one by one, and exports everything as JSON.

## Quick start

```bash
# from the project root
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

Then open **http://localhost:8501** in your browser.

## How to use it

1. **Keywords** (sidebar) — defaults cover AI training / ML roles. Edit freely (comma or newline separated), or click **Reset keywords to defaults**.
2. **Fetch live jobs** — pulls matching postings from public APIs (default import size is ~75).
3. **Review** — use **← Back** / **Next →** to walk through jobs. Left side shows the job preview; right side has editable annotations.
4. **Save** — use **Save & Next →** or **Save only** to write corrections to the local SQLite backend (`data/jobs.db`).
5. **Finish** — on the last job, click **Finish →** to download all jobs as JSON (or reviewed-only).

You can also paste specific job URLs and scrape those pages directly. Optional checkbox keeps only URL results that match your keywords.

## How it works

```
Keywords / URLs
      │
      ▼
 Public job APIs  ──or──  HTML page scrape
      │
      ▼
 Annotation extraction (title, company, location, skills, …)
      │
      ▼
 SQLite store  ←→  Streamlit review UI (edit / next / back)
      │
      ▼
 JSON export
```

| File | Role |
|------|------|
| `app.py` | Streamlit UI: fetch, review, edit, export |
| `live_jobs.py` | Live pulls from public job APIs + keyword matching |
| `scraper.py` | Scrape a single job URL and extract fields |
| `storage.py` | SQLite persistence for annotations |
| `models.py` | Shared job / annotation schema |

Extracted fields include title, company, location, employment type, remote/on-site, salary, skills, requirements, description, and source URL. Edits are saved locally so you can leave and come back.

## How jobs are pulled

### Live fetch (main path)

**Fetch live jobs** calls public JSON APIs (no browser automation):

| Source | Endpoint / approach |
|--------|---------------------|
| [Remotive](https://remotive.com) | `/api/remote-jobs` plus keyword search queries |
| [RemoteOK](https://remoteok.com) | `/api` job feed |
| [Arbeitnow](https://www.arbeitnow.com) | `/api/job-board-api` across multiple pages |
| [Jobicy](https://jobicy.com) | `/api/v2/remote-jobs` with keyword-derived tags |

Flow:

1. Request listings from each selected source.
2. Keep postings that match **at least one** of your keywords in the title, description, or tags.
3. Short keywords like `AI` / `ML` / `NLP` only match **title + tags** (not long description boilerplate), so results stay on-target.
4. Deduplicate by URL and by title+company.
5. Prefer title matches, then return up to your **Max jobs to import** limit (default **75**).

Default keywords include terms like `AI trainer`, `machine learning`, `LLM`, `RLHF`, `data labeling`, `prompt engineer`, and related phrases. Customize them anytime in the sidebar.

### URL scrape (optional)

Paste Greenhouse, Lever, or other public job page URLs. The scraper:

1. Fetches the HTML.
2. Prefers [schema.org `JobPosting`](https://schema.org/JobPosting) JSON-LD when present.
3. Otherwise falls back to common page selectors + text heuristics.
4. Optionally filters results with your keywords.

LinkedIn / Indeed often block automated scrapes; company career pages and board APIs work more reliably.

## Export format

Downloaded JSON looks like:

```json
{
  "exported_at": "2026-08-05T21:00:00+00:00",
  "count": 75,
  "jobs": [
    {
      "id": "...",
      "created_at": "...",
      "updated_at": "...",
      "annotations": {
        "title": "...",
        "company": "...",
        "location": "...",
        "employment_type": "...",
        "remote": "...",
        "salary": "...",
        "skills": "...",
        "requirements": "...",
        "description": "...",
        "source_url": "...",
        "reviewed": true
      }
    }
  ]
}
```

## Notes

- Live listings depend on what the public APIs return that day; counts vary.
- Please respect each board’s API terms (attribution / link-backs where required).
- Local data lives under `data/` and is gitignored.
- Streamlit’s Deploy button is hidden via `.streamlit/config.toml`.
