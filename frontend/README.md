# Probitas frontend

Static HTML + FastAPI bridge that wraps the Python intelligence pipeline.

## What's in this folder

```
public/         Static HTML + CSS (the designs)
  index.html        Landing page
  preview.html      Entity preview (after search, before payment)
  progress.html     Live 7-stage pipeline progress with SSE
  report-*.html     Report viewer (charity / company)
  styles.css        Shared design system

api/            FastAPI bridge
  main.py           Routes + static serving
  runner.py         Background pipeline executor
  preview_lookup.py Fast entity preview using existing api_clients/
  serialize.py      Bundle → JSON
  db.py             SQLite storage

data/           SQLite database lives here (gitignored)
run.sh          Local dev launcher
```

## Quick start

1. **Set the admin bypass code** in the project root `.env`:

   ```
   PROBITAS_ADMIN_CODE=your-secret-here
   ```

   Pick anything random — this is the password that lets you run reports
   without going through Stripe Checkout. Only you should know it.

2. **Install Python deps** (one-time):

   ```
   python3 -m pip install fastapi 'uvicorn[standard]' python-dotenv
   ```

3. **Launch**:

   ```
   ./frontend/run.sh
   ```

   Open http://localhost:8000 in a browser.

## Using it

- Type a UK charity number (e.g. `220949` for British Red Cross) or company
  number (e.g. `00445790` for Tesco PLC) and hit Search.
- Confirm the entity on the preview page.
- Click **"Have an admin code?"** in the corner of the CTA card.
- Enter the code from your `.env`. The pipeline starts immediately —
  no Stripe, no payment.
- Watch the 7-stage progress page. About 90 seconds.
- Final report renders with risk score, sanctions screening, narrative,
  verification badge, deterministic checks.

## API surface

| Endpoint | Purpose |
|---|---|
| `GET  /api/preview/{type}/{id}` | Free entity preview — registry data only |
| `POST /api/runs/start` | Start a pipeline run (admin bypass, Stripe TBD) |
| `GET  /api/runs/{run_id}` | Fetch the completed bundle as JSON |
| `GET  /api/runs/{run_id}/stream` | SSE: live stage progress |
| `GET  /api/health` | Liveness + admin-bypass-configured flag |

## What's coming next session

- Stripe Checkout for paid path (admin bypass stays for testing)
- Resend transactional email with signed report links
- PDF export via Puppeteer
- Postgres migration (SQLite is fine for v1 local)
- Deployment to Vercel + Railway/Fly
