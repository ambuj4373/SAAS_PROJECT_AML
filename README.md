# Probitas

Research-grade due-diligence intelligence on any UK company or charity.

A pay-per-report tool that aggregates public data from Companies House, Charity Commission, OFSI, OFAC, and the UN, applies governance and financial analysis, and produces an evidence-anchored 25-page report — delivered in around 90 seconds.

> **Note:** Probitas is research-grade intelligence, not regulated AML advice. Users in regulated sectors must apply their own MLR-compliant procedures.

---

## Project status

| Layer | State |
|---|---|
| Intelligence pipeline (Python) | **Done** — multi-source sanctions, risk scoring, narrative generation, deterministic claim verification |
| Product spec | **Locked** — see [PRODUCT_SPEC.md](PRODUCT_SPEC.md) |
| Frontend build brief | **Ready** — see [DESIGN_BRIEF.md](DESIGN_BRIEF.md) |
| Next.js frontend | Pending |
| FastAPI bridge | Pending |
| Stripe + email + PDF | Pending |

---

## What's in the repo

```
api_clients/      Source adapters: Companies House, Charity Commission,
                  OFSI, OFAC, UN, Tavily, Serper
core/             Domain logic: scoring config, sanctions matching,
                  narrative verifier, self-verification, caching, LLM client
pipeline/         LangGraph orchestration: 7-stage charity + company graphs
prompts/          LLM prompt templates for the analysis layer
reports/          End-to-end orchestrators returning self-contained
                  CharityReportBundle / CompanyReportBundle
scripts/          Local verification runner
tests/            52 unit tests covering sanctions, scoring, verifier
```

Generated reports include a 0–100 risk score across six categories
(Geography · Financial · Governance · Sanctions · Adverse Media · Operational),
sanctions screening against three authoritative lists, governance and financial
anomaly analysis, adverse media findings, and a verification badge reporting
how many of the report's claims are evidence-backed.

---

## Local development

```bash
# Python 3.11+ recommended
python3 -m pip install -r requirements.txt

# Configure API keys (Companies House, Charity Commission, OpenAI, etc.)
cp .env.example .env
# edit .env

# Run the unit tests
python3 -m pytest -q --ignore=tests/test_charity_pipeline_golden.py

# Run an end-to-end report on a known charity
python3 scripts/run_verification.py charity 220949
```

The verification runner writes the full bundle, narrative, prompt, and pipeline
state to `verification_runs/<entity>_<timestamp>/`.

---

## Architecture (target state)

```
Browser ─► Next.js (Vercel) ─► FastAPI bridge ─► reports/ pipeline ─► Postgres + R2
                ▲                                       │
                └──── SSE for live progress ────────────┘
```

The Python `reports/` package is the API surface. Everything that ships
externally calls `generate_charity_report()` or `generate_company_report()` and
serialises the bundle as JSON. The bundle shape is documented in
[DESIGN_BRIEF.md](DESIGN_BRIEF.md) §5.

---

## Roadmap

See [PRODUCT_SPEC.md](PRODUCT_SPEC.md) §10 for the full v1 → v3 roadmap.

**v1** (target 4–6 weeks): landing page + search + Stripe Checkout + report
viewer + PDF + email delivery.
**v2**: optional accounts, watchlists, API access for accountants, white-label
PDFs for accountancy firms.
**v3**: bulk lookup, integrations (Xero, QuickBooks, Sage), custom risk
weightings.

---

## Licence

MIT.
