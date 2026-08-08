# Job Scraping

Collect **contract / AI data-labeling** roles, annotate them in a review UI, and export approved jobs as JSON or CSV.

Built for Mercor-, Surge-, Scale-, and Outlier-style expert contributor postings, but works with public job APIs and any careers-page URLs you paste.

## How to run

You need **Python 3.9+** and **Node.js 18+** (with npm) installed.

### 1. First time — install dependencies

Open a terminal in this project folder and run:

```bash
./setup
```

This creates the Python virtualenv, installs backend packages, Playwright Chromium, and frontend packages. You only need to do this once (or again after pulling big dependency changes).

### 2. Every time — start the app

```bash
./dev
```

This starts **both** the API and the UI. Leave that terminal open.

When you see that the app is running, open:

**http://localhost:5173**

| What | URL |
|------|-----|
| App (use this) | http://localhost:5173 |
| API only | http://127.0.0.1:8000 |

### 3. Stop

In the terminal where `./dev` is running, press **Ctrl+C**. That stops the API and UI together.

### 4. Using the app

1. Open **http://localhost:5173**
2. Left side — choose **Online**, **Boards**, or **Both**
3. Add keywords (required for Online/Both) and/or board URLs, set minimum jobs
4. Click the green start button
5. Review jobs on the right → **Save** or **Delete / Skip**
6. Download approved jobs from the top bar (**JSON** or **CSV**)

### Optional: Make commands

```bash
make setup   # same as ./setup
make start   # same as ./dev
```

### Troubleshooting

| Problem | Fix |
|--------|-----|
| `Permission denied: ./setup` or `./dev` | Run `chmod +x setup dev`, then try again |
| `No .venv found` | Run `./setup` once |
| Port 8000 or 5173 already in use | Quit whatever is using that port, then `./dev` again |
| Blank or broken JS-heavy boards | `source .venv/bin/activate && playwright install chromium` |
| Page at port 8000 looks wrong | That’s the API — use **http://localhost:5173** |

### Manual start (if you don’t use `./dev`)

```bash
# Terminal 1 — API
source .venv/bin/activate
cd backend && uvicorn main:app --reload --port 8000

# Terminal 2 — UI
cd frontend && npm run dev
```

Then open **http://localhost:5173**.

## Stack

| Layer | Tech |
|--------|------|
| Backend | FastAPI, `requests` + BeautifulSoup, Playwright (JS-heavy pages) |
| Frontend | React + Vite + Tailwind |
| Storage | Local JSON (`data/jobs.json`) — no database |

## How it works

### Filters (left panel)

1. **Where to look**
   - **Online** — keyword search across public job APIs (Remotive, RemoteOK, Arbeitnow, Jobicy). No board URL required.
   - **Boards** — scrape career pages you paste (Mercor, Greenhouse, Lever, company sites, etc.). Keywords optional.
   - **Both** — online search first, then your board URLs to fill remaining slots.
2. **Keywords** — required for Online / Both; optional for Boards.
3. **Minimum jobs** — keeps collecting until this many matching roles are found, or sources run out.

Only **contract / freelance / temporary** roles (and labeling-style gigs) are kept.

### Annotation (right panel)

- One job at a time: listing preview + editable fields
- **Save** — approve for export  
- **Delete / Skip** — discard (excluded from export)  
- **← / →** — navigate (also `k` / `j` when not typing)

Fields are shaped for AI labeling / expert roles, including:

- Identity — title, company/platform, client/partner lab, URL, posted date  
- Engagement — employment type, work mode, **pay rate**, hours/week, duration, location, languages  
- Role — domain, task type (RLHF, labeling, evaluation…), tools, screening, responsibilities, requirements, preferred  
- Full description + notes  

Pay is extracted with context-aware matching (prefers `$XX–$YY/hr` over unrelated `$` amounts on the page).

### Export (top bar)

Download **approved (`saved`) jobs only** as JSON or CSV anytime.

## Project layout

```
backend/
  main.py           # FastAPI routes + SSE progress
  models.py         # Job / scrape request schemas
  scraper.py        # Board scrape + pay / contract heuristics
  online_search.py  # Public job API search
  storage.py        # data/jobs.json persistence
frontend/
  src/App.jsx
  src/components/FilterPanel.jsx
  src/components/ReviewScreen.jsx
  src/components/TopBar.jsx
data/
  jobs.json         # created at runtime (gitignored)
```

## API

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/health` | Health check |
| GET | `/api/state` | Jobs + scrape status |
| POST | `/api/scrape/start` | Start online search and/or board scrape |
| GET | `/api/scrape/progress` | SSE progress stream |
| POST | `/api/jobs/{id}/save` | Save / approve (body = field updates) |
| POST | `/api/jobs/{id}/delete` | Skip / discard |
| GET | `/api/export?format=json\|csv` | Download approved jobs |
| POST | `/api/reset` | Clear unreviewed/deleted; keep saved |

### Start scrape body

```json
{
  "mode": "online",
  "keywords": "annotator, RLHF, data labeling",
  "min_jobs": 10,
  "urls": []
}
```

- `mode`: `"online"` | `"urls"` | `"both"`
- `keywords`: required for `online` / `both`; optional for `urls`
- `urls`: required for `urls` / `both`
- `min_jobs`: 1–200

Each new run clears previous unreviewed/deleted jobs and keeps **saved** approvals. Progress and jobs persist in `data/jobs.json` across refresh.

## Notes

- The frontend proxies `/api` to `http://127.0.0.1:8000` (see `frontend/vite.config.js`).
- Board scrapes respect robots.txt (fetched with a browser User-Agent). Some SPA boards need Playwright.
- If the minimum can’t be met, the UI reports how many were found vs requested.
- With `--reload`, most backend code changes restart automatically; restart `uvicorn` if they don’t.
