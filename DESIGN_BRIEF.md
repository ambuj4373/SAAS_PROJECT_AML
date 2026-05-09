# Build the Probitas product — Next.js frontend

You are designing and building a complete Next.js 14 web product called **Probitas**. The Python backend pipeline already exists and is locked. Your job is the entire frontend, end-to-end, designed to plug into that backend with no rework.

---

## 1. The product, in one paragraph

**Probitas** is a pay-per-report due-diligence tool for UK professionals. A user types a UK company number or charity number, pays £15 via Stripe Checkout (no signup), and gets back a 25-page evidence-anchored report covering Companies House / Charity Commission filings, sanctions screening (OFSI + OFAC + UN), governance, financials, adverse media, and a 0–100 risk score with category breakdown. Delivered in 90 seconds, emailed as a PDF + permanent web link. Built for accountants doing client KYB, compliance officers, finance leads vetting suppliers, and trustees doing grantee diligence.

Tagline: **"A probity check on any UK company or charity."**

The brand name *Probitas* is the Latin word for **integrity / probity / proven character** — same naming register as LexisNexis, Veritas, Bureau Veritas, Equifax. Institutional, weighty, 19th-century-credible. The hero copy doubles as a name explanation: "A probity check on any UK company or charity."

---

## 2. Audience — write for one specific person

When writing copy, picture **Sarah, 41, partner at a 4-person accountancy firm in Reading**. She has 12 new client onboardings to do this month. Each one currently takes her 90 minutes of clicking through Companies House, Googling director names, and praying she didn't miss anything. She's not impressed by jargon, she's allergic to fluff, and she's already paying £400/month for two compliance tools she barely uses. She'll buy in 30 seconds if the sample report convinces her, and never if the marketing site has the words "AI-powered" or "revolutionary".

Write every line for Sarah.

Voice rules:
- British English (organisation, not organization)
- Specific numbers ("5,135 OFSI entries") over vague adjectives ("comprehensive coverage")
- Active voice ("Probitas checks 3 sanctions lists") over passive ("3 sanctions lists are checked")
- Plain English ("no signup, no subscription") over jargon ("frictionless onboarding")
- Quiet confidence over hype, exclamation marks, em-dash drama
- Lead with the source ("Companies House says…") over our cleverness ("Our AI analyses…")
- Show with screenshots and a real sample report; don't tell with marketing claims

**Words to use:** probity · intelligence · research · evidence · sources · check · verify · vet · diligence · report · screening · risk · governance · finding · record · filing · profile · audit-trail · standing.

**Words to avoid:** AI-powered · revolutionary · cutting-edge · disruptive · seamless · solution · platform · ecosystem · empower · unlock · transform · journey · synergy · holistic · best-in-class · enterprise-grade · world-class · next-generation.

**Banned permanently:** "AML solution", "AML compliance tool", "regulatory tool" — for legal-positioning reasons. We sell *research-grade intelligence*, not regulated AML advice.

---

## 3. Aesthetic — Stripe + Linear, with editorial weight

Generous whitespace, restrained palette, real typography. Not Bloomberg (too dense), not Mercury (too playful), not big-4 consultancy (too stuffy).

| Element | Decision |
|---|---|
| **Wordmark** | Lowercase `probitas` set in a confident sans-serif with slight institutional weight. Söhne, GT America, or Tiempos Headline. No icon mark for v1 — wordmark IS the brand. Optional thin rule above to nod at masthead conventions. |
| **Primary colour** | Deep indigo `#3D3DDC` — trustworthy without being banking-blue, distinctive without being startup-purple |
| **Neutrals** | Charcoal `#1A1D29`, mid-grey `#6B7280`, soft white `#FAFAFA`, line `#E5E7EB` |
| **Risk palette** (only on risk-level pills) | Low `#10B981` · Medium `#F59E0B` · High `#EF4444` · Critical `#7C2D12` |
| **Type** | Inter (UI) · Source Serif 4 (long-form report body — gives the report a "document" feel) · JetBrains Mono (numbers, IDs, citations) |
| **Imagery** | Zero stock photography. Zero illustrations of "people pointing at screens". Zero hero shots of the founder. Use real screenshots of the actual product, real source-authority logos (Companies House, Charity Commission), real document mock-ups |
| **Mood** | Closer to *The Economist* than *TechCrunch*. Closer to LexisNexis than Mercury. |
| **Border radius** | 8–12px. Subtle 1px borders over heavy shadows. |
| **Dark mode** | v2 — ship light first |

---

## 4. Pages and flows — build all of these

### 4.1 Landing page (`/`)

The single most important page. Build the trust ladder in this order:

1. **Hero**
   ```
   # A probity check on any UK company or charity.
   The same sources regulators use — Companies House, Charity Commission,
   OFSI, OFAC, UN — read into a 25-page evidence-anchored report.
   Delivered in 90 seconds. £15.
   [ Search a company or charity → ]
   No signup. No subscription. Sample report below.
   ```
   No hero illustration. No carousel. The search input lives in the hero and accepts UK company numbers (8 digits) OR charity numbers (1–7 digits, optionally with -1 suffix) with auto-detection.

2. **Sample report** — the British Red Cross dossier (charity 220949), partially visible above the fold to invite scroll. This is the single highest-converting element on the page; it must look incredible.

3. **Source authorities** — a quiet horizontal strip naming Companies House, Charity Commission, OFSI, OFAC, UN. Borrow their authority. No logos that aren't legally usable; names set in a small caps row works fine.

4. **How it works** — three steps (Search → Pay → Get report), with the actual seven pipeline stages listed underneath as proof of depth.

5. **What's in a Probitas report** — annotated screenshot or component-by-component breakdown showing risk score, sanctions screening, evidence anchoring, verification badge.

6. **Pricing** — single card: £15 per report. Pack 5 £55. Pack 20 £180. Firm £400/mo.

7. **FAQ**: How long does it take? (90s). Is the data current? (live from gov.uk APIs). What sanctions lists? (OFSI/OFAC/UN). Who sees my searches? (no one — no account). What if there's an issue? (re-run free).

8. **Footer** — terms, privacy, sources, contact + UK Ltd registration number small in the bottom corner. That's all the corporate identity the site needs.

### 4.2 Entity preview (`/check/[type]/[id]`)
After search, before payment. Free preview of public data so the user confirms they searched the right thing:
- Entity name, registration date, status
- Registered address
- Trustee/director count
- Latest filed accounts year
- "Generate full report — £15" CTA
- Disclaimer: this preview is not the report

### 4.3 Stripe Checkout
Standard hosted Stripe Checkout. Pass `entity_type` and `entity_id` in metadata. Success → `/run/[run_id]?session_id=...`. Cancel → back to entity preview.

### 4.4 Live progress page (`/run/[run_id]`)
The pipeline takes ~90 seconds. **Do not show a generic spinner** — show the seven actual stages:

1. Charity Commission & Companies House records
2. Document extraction & enrichment
3. Web intelligence & OSINT
4. Governance & financial analysis
5. Sanctions screening
6. Risk scoring
7. Building analysis prompt + LLM narrative

Each stage gets a row with a state (pending / running / done) and elapsed time. Smooth transitions, not jumpy. **This is where the user feels the depth of the product** — sell it.

Implementation: Server-Sent Events from a Next.js Route Handler streaming the `on_stage_start` / `on_stage_end` callbacks from the Python pipeline. Polling fallback every 2s.

### 4.5 Report viewer (`/report/[run_id]`)
The product's centrepiece.

Layout:
- **Sticky left sidebar**: section TOC, scroll-synced, click to jump
- **Main column**: the rendered markdown narrative (~25KB)
- **Sticky right rail**: risk-score badge, verification reliability badge, sanctions summary, "Download PDF", "Email me a copy", run timestamp, cost

Hero of the main column:
- Entity name (large, Source Serif 4 or similar editorial face)
- Risk score: large numeric (e.g. `42 / 100`) + level pill (`MEDIUM`) + 6 category mini-bars (Geography, Financial, Governance, Sanctions, Adverse Media, Operational)
- Verification badge: `Good · 80% · 24/30 claims evidence-backed` — clickable to expand the unsupported/uncertain claims panel
- Narrative-check badge: `0 critical, 0 warnings · deterministic checks passed` — clickable to expand the rules-run list

Body:
- Render the markdown with `react-markdown` + `remark-gfm` (tables) + `rehype-sanitize`. No raw HTML. Tables styled as document tables, not Excel.
- Section 6B "Sanctions List Screening" — special render: each "Sources Checked" item gets a labelled chip linking to source authority.
- Citations like `(Source: CC API)` get pill-styled to feel like footnotes.
- Body uses Source Serif 4 for the long-form text — gives the report a "document" feel rather than a webpage feel.

### 4.6 Email-link access
No accounts → reports accessed via signed URL emailed at completion. URL format: `/report/[run_id]?token=...`. Token valid 90 days.

Email draft (to template):
```
Subject: Your Probitas report on [Entity Name] is ready

Hi [first name],

Your report is ready. The headline:
**Risk level: [LOW/MEDIUM/HIGH/CRITICAL]** · 24 of 30 claims evidence-backed

[ View report → ] (link valid 90 days)
[ Download PDF ]

Two things worth knowing:
1. This is research, not regulated advice. If you're under MLR 2017,
   apply your own CDD procedures on top of this.
2. If anything looks wrong, reply to this email — we'll re-run it for free.

— Probitas
probitas.co.uk
```

### 4.7 Static pages
`/terms`, `/privacy`, `/sources` (where data comes from + frequency), `/about`, `/contact`. Keep content placeholders short — founder will fill in legal text.

`/about`: a paragraph on why the product exists. At the bottom, one line of founder name + LinkedIn link. **No founder photo. No biography. No "Founded by" billing.** Brand is built on what the product solves, not on who built it.

---

## 5. Backend contract — DO NOT INVENT FIELDS

The Next.js app calls a Python service exposing the existing `reports/` package. Set up a thin FastAPI bridge OR call Python via Next.js subprocess — your call. The JSON contract is fixed.

**Endpoints:**
- `POST /api/reports/charity` with `{ "charity_number": "220949" }`
- `POST /api/reports/company` with `{ "company_number": "01234567" }`
- `GET /api/reports/{run_id}/stream` — SSE for live progress

Returns the report bundle serialized as JSON. Shape:

```ts
type ReportBundle = {
  charity_number?: string;
  company_number?: string;
  entity_name: string;
  state: {
    charity_data?: { /* registry data */ };
    company_data?: { /* registry data */ };
    trustees: string[];
    sanctions_screening: {
      any_high_confidence: boolean;
      entity: SanctionsHit[];
      trustees: Record<string, SanctionsHit[]>;
      sources_checked: string[];   // ["OFSI", "OFAC", "UN"]
    };
    risk_score: {
      overall_score: number;       // 0-100
      overall_level: "Low" | "Medium" | "High" | "Critical";
      categories: Array<{
        name: string;              // Geography | Financial | Governance | Sanctions | "Adverse Media" | Operational
        score: number;
        rationale: string;
      }>;
    };
    adverse_org: AdverseHit[];
    financial_history: FinancialYear[];
    // ~15 more fields
  };
  narrative_report: string;        // ~25KB markdown — bulk of the UI
  cost_info: {
    cost_usd: number;
    model: string;
    prompt_tokens: number;
    completion_tokens: number;
  };
  verification: {
    overall_reliability: number;   // 0-1
    reliability_label: "Low" | "Moderate" | "Good" | "High";
    claims_checked: number;
    claims_supported: number;
    claims_unsupported: number;
    claims_uncertain: number;
    unsupported_claims: Array<{
      claim: string;
      evidence_ref: string;
      confidence: number;
      note: string;
    }>;
  };
  narrative_check: {
    is_clean: boolean;
    critical_count: number;
    warning_count: number;
    rules_run: string[];
    issues: Array<{
      severity: "critical" | "warning" | "info";
      rule: string;
      excerpt: string;
      detail: string;
    }>;
  };
  errors: string[];
  warnings: string[];
  timings: Record<string, number>;
};
```

**Pipeline stages** (for the live progress UI; already wired with `on_stage_start` / `on_stage_end` callbacks):
```
charity_commission_lookup, document_extraction, web_intelligence,
analysis_engines, screen_sanctions, risk_scoring, generate_report
```

---

## 6. Hard constraints

- **No login. No accounts.** Reports identified by signed run_id only.
- **No multi-tenancy / teams / admin.** v1 is single-user-per-purchase.
- **Mobile-responsive but desktop-first.** Compliance work happens on a laptop.
- **Accessibility:** WCAG 2.1 AA. Real focus rings, real keyboard nav, semantic HTML. Risk colours must also encode shape/text — don't rely on colour alone.
- **Performance:** landing TTI < 2s, report viewer renders 25KB markdown without jank.
- **Privacy:** no fingerprinting analytics on search/report path. Plausible (no cookies) is fine on landing only.

---

## 7. Anti-goals — do NOT design

- Onboarding wizard, product tour, empty states for "your first report"
- Team management, sharing, role permissions
- Subscription billing portal
- Dark mode (v2)
- Mobile-app-first patterns
- Founder photo, "Meet the team" page, "Founded by..." block on the homepage
- "As featured in" logo strip before any actual press exists
- Stock photography of any kind
- AI assistant / chatbot inside the report
- Gamification, streaks, badges
- Hero illustrations of "people pointing at screens"

---

## 8. Tech stack — fixed

- **Next.js 14** App Router (Server Components default)
- **Tailwind CSS** + **shadcn/ui** for primitives
- **Stripe Checkout** (Hosted, not Elements — minimal PCI scope)
- **Resend** for transactional email
- **react-markdown** + `remark-gfm` + `rehype-sanitize` for narrative rendering
- **lucide-react** for icons
- **No** state management library — Server Components + URL state only
- **No** ORM — Postgres via `postgres.js`
- Sits inside the existing project at `/frontend/`. Python pipeline stays at the root.

Database schema:
```sql
runs (
  id uuid primary key,
  entity_type text,           -- 'charity' | 'company'
  entity_id text,
  entity_name text,
  stripe_session_id text,
  email text,
  bundle_json jsonb,
  pdf_url text,
  status text,                -- 'pending' | 'running' | 'done' | 'failed' | 'refunded'
  risk_level text,
  risk_score numeric,
  created_at timestamptz,
  completed_at timestamptz,
  expires_at timestamptz       -- created_at + 90 days
);

stripe_events (
  event_id text primary key,
  processed_at timestamptz
);

access_tokens (
  token text primary key,
  run_id uuid references runs(id),
  expires_at timestamptz
);
```

---

## 9. Deliverables

1. All routes from §4, fully working with mocked or live data
2. The FastAPI bridge service (or Next.js subprocess wrapper) calling `reports.generate_charity_report` / `reports.generate_company_report`
3. SSE streaming for pipeline progress
4. Stripe webhook handler triggering the pipeline run on `checkout.session.completed`
5. PDF export (use `@react-pdf/renderer` or server-side Puppeteer)
6. Email-receipt template (Resend transactional, plain, link to report)
7. Postgres schema + migration file
8. `.env.example` with every required key
9. README documenting the local-dev flow: `docker compose up` → Stripe CLI listening → fill a charity number → see the full path

---

## 10. What to ask before you start (only if you can't decide)

- Final price point (default £15)
- Email-sender domain for Resend (default `reports@probitas.co.uk`)

Everything else: decide and build. Bias toward shipping the full surface over polishing one screen.
