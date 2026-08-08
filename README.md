# Job Scraping

Full-stack app that finds job listings via online keyword search and/or board URL scraping, lets you review and annotate each job, and exports approved ones as JSON or CSV.

## Stack

- **Backend:** FastAPI, `requests` + BeautifulSoup, Playwright fallback
- **Frontend:** React + Vite + Tailwind
- **Storage:** local JSON file (`data/jobs.json`) — no database

## Quick start

### Backend

```bash
cd backend
python3 -m venv ../.venv
source ../.venv/bin/activate
pip install -r requirements.txt
playwright install chromium
uvicorn main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Open **http://localhost:5173**

## How it works

1. **Filters (left)** — enter keywords (required; filter every result), pick a source, then start:
   - **Online** — Remotive, RemoteOK, Arbeitnow, Jobicy
   - **Boards** — careers pages you paste (still keyword-filtered)
   - **Both** — online first, then your boards
2. **Collect** — backend streams progress until the minimum is met (with safety caps). Board scrapes use static HTML first, Playwright if JS-heavy.
3. **Review (right)** — one job at a time: preview + editable annotations. **Save** approves; **Delete / Skip** discards (excluded from export). Arrow keys navigate.
4. **Download** — anytime from the top bar: approved (`saved`) jobs only, as JSON or CSV.

## API

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/state` | Jobs + scrape status |
| POST | `/api/scrape/start` | Start scrape |
| GET | `/api/scrape/progress` | SSE progress stream |
| POST | `/api/jobs/{id}/save` | Save / approve |
| POST | `/api/jobs/{id}/delete` | Skip / discard |
| GET | `/api/export?format=json\|csv` | Download approved jobs |

Progress survives refresh via `data/jobs.json`.
