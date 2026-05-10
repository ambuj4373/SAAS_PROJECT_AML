# Product Spec — UK Company & Charity Intelligence Reports

**Status:** v1 working spec. Living document — update as decisions get made.
**Owner:** Ambuj (founder).
**Author of this doc:** Claude, acting as CTO. Decisions below are recommendations with rationale; founder approves or pushes back.

---

## 1. What we're building (one paragraph, definitive)

A self-serve web product where any UK person can pay £15, type in a UK company number or charity number, and get back a 25-page evidence-anchored intelligence report covering registry data, governance, financials, sanctions screening (OFSI + OFAC + UN), adverse media, and a 0–100 risk score. No signup. Stripe Checkout. Report delivered in ~90 seconds and emailed as a PDF + permanent web link. Built for accountants doing client onboarding, compliance officers screening counterparties, finance leads vetting suppliers, and trustees running due diligence on grant recipients.

**What this is NOT:** an MLR-compliant AML solution that replaces a regulated firm's procedures. It is intelligence input — "research-grade" — that compliance professionals use as part of their own process. (See §3.)

---

## 2. Who pays and why

**Primary audience: UK accountants and bookkeepers (~35,000 firms).**
They have to do KYB (Know Your Business) on every new client under MLR 2017. Doing it manually takes 1–2 hours per client and costs them in time. £15 for a 90-second comprehensive report is a no-brainer. *This is where the money is — both retail (one-off) and B2B (monthly credits).*

**Secondary audience:** SME founders/finance leads vetting suppliers, charity trustees doing grantee diligence, journalists doing background research, lawyers doing pre-litigation checks.

**The job-to-be-done:** "I have to know whether this entity is legitimate and risk-free enough to do business with, and I need a paper trail showing I checked."

The "paper trail" is half the value. The PDF report is evidence the user did their diligence — important for accountants under MLR audit, for trustees under Charity Commission scrutiny, for finance leads under their own internal control regime.

---

## 3. Legal stance — READ THIS FIRST

This is the single most important section. Get this wrong and you risk regulatory action and customer lawsuits.

### 3.1 What we are
A **research and intelligence aggregation product**. We take public data (Companies House, Charity Commission, OFSI, OFAC, UN), apply AI summarisation, and deliver a structured report. We are *not* a regulated firm and we do *not* perform MLR-compliant customer due diligence on behalf of users.

### 3.2 What we tell users
On every report and every page:

> This report aggregates public data and AI-generated analysis. It is research input only and does not constitute regulated AML advice or MLR-compliant Customer Due Diligence. Users in regulated sectors must apply their own MLR procedures. Sources are dated and may not reflect filings made after the report timestamp.

This disclaimer goes:
- In the marketing copy ("research-grade intelligence", not "AML compliance")
- On the report itself, page 1 and as a watermark
- In the Terms of Service the user agrees to before payment
- In the email body that delivers the report

### 3.3 Why this matters
If we market this as an "AML report" to regulated firms (accountants, lawyers), we're effectively offering a regulated service. That has FCA/HMRC implications and would expose us to liability when an accountant gets fined because the report missed something. By positioning as **intelligence**, we shift the regulatory accountability back where it belongs — with the regulated user — while still being indispensable to their workflow.

### 3.4 Naming consequence
We **do not** call the product "AML Reports" or "Compliance Tool" externally. (Keep AML internal — it's accurate from a workflow perspective but not a positioning one.)

### 3.5 Other legal must-haves
- **GDPR:** privacy policy + cookie policy. Lawful basis for processing director/trustee personal data is **legitimate interest** (Article 6(1)(f)) for financial-crime prevention research. Document this in a Legitimate Interests Assessment (LIA).
- **Data retention:** report bundles deleted from our DB after 90 days unless user requests longer. Stripe payment records kept 7 years (HMRC requirement).
- **Right to erasure:** if a director/trustee asks us to delete data on them, we delete the relevant report bundles. We do *not* delete the underlying public sources (we don't control them).
- **Terms of service:** standard SaaS terms + explicit disclaimer (§3.2) + liability cap at fees paid + no-warranty for accuracy of upstream sources.
- **Limitation of liability:** total liability capped at the fees paid in the 12 months preceding the claim. Standard.

---

## 4. Pricing

### 4.1 Recommended structure

| Plan | Price | Reports | £/report | Who it's for |
|---|---|---|---|---|
| Single | £15 | 1 | £15.00 | Trying it out, occasional users |
| Pack 5 | £55 | 5 | £11.00 | Regular SME users |
| Pack 20 | £180 | 20 | £9.00 | Active accountants |
| Firm | £400/mo | 60/mo | £6.67 | Accountancy firms |

Credits never expire. Unused credits are non-refundable.

### 4.2 Margin
Unit cost is ~$0.025/report (LLM + APIs + infra). At £15, gross margin is ~99%. At £6.67 (Firm tier), still ~96%. **Pricing is positioning, not cost-driven.**

### 4.3 What's NOT in pricing
- No free tier. Free invites abuse (scrapers, competitors, mass lookups).
- No free trial. Instead, **a sample report** on the landing page so users can see exactly what they're paying for.
- No discounts beyond the tier table. No referral codes for v1. No "first month free".

### 4.4 Refund policy
If the report fails to generate (pipeline error), automatic 100% refund + apology email. If the user disputes the report quality, manual review + case-by-case credit. *No quibble-refunds* — this is a finished product, not a service.

---

## 5. v1 scope — exactly what ships

### 5.1 Functionality
1. Landing page with sample report and clear pricing
2. Search bar accepting UK charity number OR UK company number (auto-detect)
3. Free entity preview (name, status, registered address) before payment
4. Stripe Checkout — single report (£15) or Pack 5 (£55)
5. Live pipeline progress page showing the 7 stages
6. Report viewer (web) with the markdown narrative + risk score + verification badges
7. PDF download of the report
8. Email delivery of the report (signed link valid 90 days)
9. Static pages: terms, privacy, sources, FAQ, contact
10. Stripe webhook handling refunds automatically on pipeline failure

### 5.2 Both entity types in v1
Charities and companies. Backend already supports both — there's no good reason to ship one without the other.

### 5.3 Sample report
A real generated report on a well-known entity (e.g. British Red Cross for charities, Tesco PLC for companies), pre-rendered and visible on the landing page without payment. This is the single most important conversion lever — let users *see* the depth before they pay.

### 5.4 Caching
30-day cache by entity ID. If two paying users request the same entity within 30 days, we serve the cached bundle (saves ~$0.025/run, returns in <2s instead of 90s, same report content). The second user is told "this report was generated 4 days ago — would you like the cached version (instant) or a fresh run (90s, no extra charge)?" Default to cached.

---

## 6. Out of scope for v1

Anything below is explicitly NOT in v1. Add to v2 if demand proves it.

- User accounts, login, password reset, OAuth
- Team/multi-user purchases
- Subscription billing portal (Stripe customer portal)
- Saved searches, watchlists, alerts when filings change
- API access (B2B integration)
- White-label / co-branded reports for accountancy firms
- Bulk upload (CSV of company numbers → batch reports)
- Mobile app
- Dark mode
- Internationalisation (UK-only data, English-only)
- Comparison reports (entity A vs entity B)
- Custom risk weightings per user
- Direct messaging / customer chat
- Affiliate / referral program

---

## 7. System architecture

```
                                ┌──────────────────────┐
                                │ Stripe Checkout      │
                                │ (hosted)             │
                                └──────────┬───────────┘
                                           │ webhook
┌────────────────┐    HTTPS    ┌───────────▼──────────┐
│  User browser  │◄───────────►│  Next.js (Vercel)    │
└────────────────┘             │  - landing, search   │
                               │  - report viewer     │
                               │  - SSE progress      │
                               └──────────┬───────────┘
                                          │ HTTP (internal)
                               ┌──────────▼───────────┐
                               │  FastAPI bridge      │
                               │  (Railway/Fly.io)    │
                               │  - validates request │
                               │  - kicks off pipeline│
                               │  - streams progress  │
                               └──────────┬───────────┘
                                          │ in-process
                               ┌──────────▼───────────┐
                               │  Python pipeline     │
                               │  reports/charity.py  │
                               │  reports/company.py  │
                               │  (existing code)     │
                               └──┬────────┬──────────┘
                                  │        │
                          ┌───────▼──┐  ┌──▼─────────┐
                          │ Postgres │  │ Cloudflare │
                          │ (Neon)   │  │ R2 (PDFs)  │
                          └──────────┘  └────────────┘
                                  │
                          ┌───────▼──┐
                          │  Resend  │
                          │  (email) │
                          └──────────┘
```

**Components:**

| Layer | Choice | Why |
|---|---|---|
| Frontend host | Vercel | Native Next.js, free tier covers v1 |
| Backend host | Railway or Fly.io | Python + long-running pipeline, $5–20/mo |
| Database | Neon (Postgres) | Serverless, generous free tier, branching for dev |
| Object storage | Cloudflare R2 | S3-compatible, free egress |
| Email | Resend | Best dev UX, 3K free emails/mo |
| Payments | Stripe | Default, Hosted Checkout = minimal PCI scope |
| DNS | Cloudflare | Free, performant |
| Errors | Sentry | Free tier sufficient for v1 |
| Uptime | Better Stack | Free tier, alerting via email |
| Analytics | Plausible | Privacy-first, no cookies needed for landing-only tracking |

**Estimated infra cost at v1 traffic (≤500 reports/mo):** ~£25/month total.

---

## 8. Data model

### 8.1 What we store

**`runs` table** — one row per report run
- `id` (uuid, primary key, public-facing in URLs)
- `entity_type` (charity | company)
- `entity_id` (charity_number or company_number)
- `entity_name` (denormalised for fast list view)
- `stripe_session_id`
- `email` (where to send the report)
- `bundle_json` (full pipeline output — the source of truth for the report)
- `pdf_url` (R2 key)
- `status` (pending | running | done | failed | refunded)
- `risk_level`, `risk_score` (denormalised for quick filtering)
- `created_at`, `completed_at`
- `expires_at` (created_at + 90 days for soft-delete)

**`stripe_events` table** — webhook idempotency
- `event_id` (Stripe's id, primary key)
- `processed_at`

**`access_tokens` table** — signed URLs for report access without login
- `token` (random 32-char, primary key)
- `run_id` (foreign key)
- `expires_at`

That's it. No users table. No sessions. No teams.

### 8.2 What we DON'T store
- Search queries that didn't lead to payment (clear immediately)
- IP addresses beyond 24h (only for fraud signals)
- Any PII not strictly required for the product

### 8.3 Retention
- `runs` rows older than 90 days: bundle_json + PDF moved to cold storage (or deleted). Metadata kept for tax/accounting.
- Stripe records: 7 years (HMRC).
- Email logs: 30 days.

---

## 9. Brand & naming — locked

**Brand name: Probitas.**

*Probitas* (PRO-bi-tas) is the Latin word for **integrity, probity, proven character**. It's the actual concept the buyer is trying to demonstrate when they perform KYB/EDD. Same trick LexisNexis pulled — the name *is* the corpus. Same register as Veritas, Equifax, Bureau Veritas: 19th-century institutional weight.

Why it's right for this audience:
- **Real word, real meaning, already in the lexicon.** Probity is what auditors test for, what regulators expect, what trustees must demonstrate. The name doesn't need to be taught.
- **Institutional, not startup-y.** Sounds like it's been operating since 1880. Sits comfortably next to LexisNexis, World-Check, Bureau Veritas in a procurement spreadsheet.
- **International scaling.** Universal Latin — works across UK, EU, US legal/financial vocabulary without retranslation.
- **Pairs with the product.** *"The Probitas report on [Entity] · 24 of 30 claims evidence-backed."* Reads like a citation, not a tagline.

Domain plan — founder confirms availability before incorporating:
- **Primary:** `probitas.co.uk` (UK signal; expected to be available — `.com` is held by Probitas Partners, a US private-equity firm, but the categories are different and there's no consumer confusion)
- **Fallback 1:** `probitas.uk`
- **Fallback 2:** `probitasreports.com` or `getprobitas.com`

Note for founder: do a 5-minute search for "Probitas compliance" / "Probitas KYC" before committing — Probitas Partners is finance-adjacent so worth confirming there's no direct compliance-product conflict. If anything material shows up, push back and we re-pick.

Visual direction lives in §13.6.

---

## 10. Roadmap

### v1 — MVP (target: 4–6 weeks of build)
Everything in §5. Charities + Companies. Single + Pack 5. Goal: 50 paying customers in the first month.

### v1.1 — Polish (weeks 7–8)
- Pack 20 + Firm tier
- Plausible analytics on landing
- A/B test: hero copy variants
- Improve sample report (more variety, side-by-side risk levels)
- Retargeting on landing-page visitors who didn't convert

### v2 — Scale enablers (months 3–4)
- User accounts (optional — credits attached to email login via magic link, no password)
- Watchlists (alert when a watched entity files something material)
- API access for accountants (£0.05/report wholesale, no UI, JSON only)
- White-label PDF (firm name + logo on the report cover)

### v3 — B2B push (months 6–12)
- Integrations: Xero, QuickBooks, Sage (accountant workflows)
- Bulk upload + scheduled re-checks
- Custom risk weightings per firm
- Compliance training content / certified workflow

---

## 11. Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **Regulator deems us a regulated firm** | Low | High | §3 positioning + disclaimer + qualified legal review before launch |
| **Source API outage** (Companies House, Charity Commission, OFSI) | Medium | Medium | Cache aggressively; show "data as of X" timestamps; auto-refund if pipeline fails |
| **LLM hallucinates a critical fact** | Medium | High | Verification layer + narrative_check (already built); evidence anchoring; user disclaimer |
| **Customer sues over a missed sanction match** | Low | High | §3 disclaimer; ToS liability cap; insurance (professional indemnity, ~£500/yr) |
| **Defamation claim from named director/trustee** | Low | Medium | All claims sourced; nothing original we author about people; right-to-correct policy |
| **Stripe disputes / chargebacks** | Low | Low | Strong sample report = informed buyers; refund-on-failure auto-trigger; clear email receipts |
| **Scraping / bulk abuse** | High | Low | No free tier; rate-limit by IP and Stripe customer; CAPTCHA on search if abuse detected |
| **Hosting costs blow up at scale** | Low | Low | Per-unit cost is fixed and tiny; we'd be drowning in revenue before infra costs hurt |
| **Solo founder bus factor** | High | High | Document everything (this doc, README, runbooks); contractor-friendly codebase; minimal vendor lock-in |

**Insurance:** £500/yr for professional indemnity (covers claims about the work product) is the single best risk-spend at launch. Direct Line / Hiscox both quote online.

---

## 12. Decisions — locked

Made by Claude as CTO + senior brand/marketing lead. Founder veto on any line item.

| # | Decision | Locked answer | One-line rationale |
|---|---|---|---|
| 1 | **Brand name** | **Probitas** | Latin for *integrity / probity / proven character* — the exact concept the buyer is trying to demonstrate. Institutional register (LexisNexis / Veritas / Bureau Veritas family), real word, international scaling, sounds 100 years old in the right way. |
| 2 | **Primary domain** | `probitas.co.uk` (fallback: `probitas.uk` then `probitasreports.com`) | `.co.uk` signals UK-only, builds trust with UK accountants. Founder confirms availability + buys; budget £20/yr. |
| 3 | **Pricing** | Per §4.1 table — locked | Single £15 (low-friction first purchase), Pack 5 £55 (–27%), Pack 20 £180 (–40%), Firm £400/mo (–56%). Steep tier savings drive upsell into the firm tier where the real B2B revenue is. |
| 4 | **Email-from addresses** | `reports@probitas.co.uk` (transactional), `support@probitas.co.uk` (replies) | Action-clear sender = lower spam complaints. No personally-named address — keeps the brand product-led, not founder-led. |
| 5 | **Founder presence on the site** | **Minimal — single line on `/about`** | Brand is built on what the product solves, not on who built it. About page = a paragraph on why the product exists, founder name + LinkedIn as one line at the bottom. No photo, no biography, no "Founded by" billing on the homepage. |
| 6 | **Company structure** | **Ltd from day one** | Liability separation (critical given AML adjacency), Stripe payouts work cleaner, looks legitimate to B2B buyers, £12 to incorporate via Companies House. |
| 7 | **Business bank** | **Tide** | Fastest setup (<24h online), free Tier for v1 traffic, clean Stripe payout integration, UK-based support. |
| 8 | **Legal docs** | **UK solicitor (~£600 one-off)** — not DIY templates | The disclaimer in §3.2 is the single most important line on the site; templates won't draft it correctly for AML adjacency. Get a quote on Lawhive or directly via a small commercial firm. Cheapest insurance you'll ever buy. |
| 9 | **Launch geography** | **UK only — England & Wales charities + all UK companies** | Companies House covers all of UK; Charity Commission covers E&W only (Scotland's OSCR + NI's CCNI are separate, deferred to v2). Disclose the charity gap on the search page. |
| 10 | **First-month goal** | **50 paying customers + 5 accountancy-firm sign-ups** | 50 retail validates demand. 5 firms validates the real B2B path — the firms are the prize; retail is the loud signal that gets us there. |

---

## 13. Brand & marketing positioning

This section is the source of truth for every line of copy, every design choice, every channel post. Anyone writing for Probitas should read this section first.

### 13.1 Positioning statement (internal — never shown to users)

> For UK professionals who need to know who they're doing business with, **Probitas** is research-grade intelligence on any UK company or charity. We aggregate the same sources regulators use — Companies House, Charity Commission, OFSI, OFAC, UN — and deliver a 25-page evidence-anchored report in under two minutes for £15. Use it as your due diligence starting point, not your finishing line.

### 13.2 Tagline (locked)

**"Know who you're dealing with."**

Five words, declarative, universal across audiences (accountants, trustees, finance leads). Reads well as a hero, on a billboard, in a tweet, on the bottom of an email. No second tagline — one product, one line.

### 13.3 The audience as one person

When writing copy, picture **Sarah, 41, partner at a 4-person accountancy firm in Reading**. She has 12 new client onboardings to do this month. Each one currently takes her 90 minutes of clicking through Companies House, Googling director names, and praying she didn't miss anything. She's not impressed by jargon, she's allergic to fluff, and she's already paying £400/month for two other compliance tools that she barely uses. She'll buy Probitas in 30 seconds if the sample report convinces her, and never if the marketing site has the words "AI-powered" or "revolutionary".

Write every line for Sarah.

### 13.4 Voice principles

| Do | Don't |
|---|---|
| British English (organisation, not organization) | American spelling |
| Specific numbers ("5,135 OFSI entries") | Vague adjectives ("comprehensive coverage") |
| Active voice ("Probitas checks 3 sanctions lists") | Passive voice ("3 sanctions lists are checked") |
| Plain English ("no signup, no subscription") | Industry jargon ("frictionless onboarding flow") |
| Quiet confidence | Hype, exclamation marks, em-dash drama |
| Lead with the source ("Companies House says…") | Lead with our cleverness ("Our AI analyses…") |
| Show, with screenshots and a real sample report | Tell, with marketing claims |
| Short sentences. Clipped where it helps. | Long, explanatory paragraphs that read like a corporate brochure where every clause modifies the previous one. |

### 13.5 Words we use, words we don't

**Use:** probity · intelligence · research · evidence · sources · check · verify · vet · diligence · report · screening · risk · governance · finding · record · filing · profile · audit-trail · standing

**Avoid:** AI-powered · revolutionary · cutting-edge · disruptive · seamless · solution · platform · ecosystem · empower · unlock · transform · journey · synergy · holistic · best-in-class · enterprise-grade · world-class · next-generation

**Banned permanently:** "AML solution", "AML compliance tool", "regulatory tool" — for legal-positioning reasons (§3). Internal use only.

### 13.6 Visual identity direction

| Element | Decision |
|---|---|
| **Wordmark** | Lowercase `probitas` set in a confident sans-serif with a slight institutional weight (recommend Söhne, GT America, or Tiempos for a more editorial feel). No icon mark for v1 — the wordmark IS the brand. Optional: small Latin-style underline or a single rule above the wordmark to nod at masthead/document conventions without being literal about it. |
| **Primary colour** | Deep indigo `#3D3DDC` — trustworthy without being banking-blue, distinctive without being startup-purple. |
| **Neutrals** | Charcoal `#1A1D29`, mid-grey `#6B7280`, soft white `#FAFAFA`, line `#E5E7EB`. |
| **Risk palette** (used only on risk-level pills) | Low `#10B981` · Medium `#F59E0B` · High `#EF4444` · Critical `#7C2D12`. |
| **Typography** | Inter (UI) · Source Serif 4 (the long-form report body, gives it a "document" feel) · JetBrains Mono (numbers, IDs, citations). |
| **Imagery** | Zero stock photography. Zero illustrations of "people pointing at screens". Use real screenshots of the actual product, real source-authority logos (Companies House, Charity Commission), and real document mock-ups. |
| **Mood** | Calm, considered, evidence-led. Closer to *The Economist* than to *TechCrunch*. |

### 13.7 The trust ladder (in priority order on the landing page)

1. **The sample report itself** — visible and readable without a click, on a known entity (British Red Cross). Strongest possible signal: "this is what £15 buys you." This single element does more conversion work than everything else on the page combined.
2. **The source authorities** — Companies House, Charity Commission, OFSI, OFAC, UN — named in a row near the hero. We borrow their authority.
3. **The verification badge** — "80% reliability · 24 of 30 claims evidence-backed". Honest about limits = more trustworthy than "100% accurate AI".
4. **Specific numbers on capability** — "5,135 OFSI entries · 8,765 OFAC SDN entities · 1,009 UN subjects screened on every report". Real numbers > marketing adjectives.
5. **UK Ltd registration number** — small, in the footer. Quiet legitimacy signal; not founder-facing.
6. **Customer voices** — added post-launch, never invented.

**Anti-trust signals to avoid:** stock-photo team pages, fake testimonials, vague "trusted by 1000s", AI-generated marketing imagery, hero shots of the founder, "as featured in" logo strips before any actual press exists.

### 13.8 Hero copy (drafted, ready to ship)

> # A probity check on any UK company or charity.
>
> The same sources regulators use — Companies House, Charity Commission, OFSI, OFAC, UN — read into a 25-page evidence-anchored report. Delivered in 90 seconds. £15.
>
> [ Search a company or charity → ]
>
> *No signup. No subscription. Sample report below.*

The hero does double-duty: "probity check" both teaches the brand name's meaning and describes the product, in five words. By the second sentence the user has the full value proposition without a single buzzword. No hero illustration, no carousel, no founder photo, no "trusted by" logos until they're real. The sample report sits directly underneath, partially visible above the fold to invite the scroll. The first scroll the user makes is into the actual product output — which is the entire conversion mechanic.

### 13.9 Launch marketing plan (week 6 onwards)

**Channel priority and what to say in each:**

1. **LinkedIn — product-led posts, founder voice** (your personal account, but content is product-first, not founder-bio):
   - Post 1 (Sunday evening): "Just shipped — Probitas. A probity check on any UK company or charity. 25 pages, 90 seconds, £15. Here's a free sample on the British Red Cross." Link + screenshot. No origin story.
   - Post 2 (mid-week): annotated screenshot — "Here's what's in a £15 Probitas report" with arrows pointing to risk score, sanctions screening, evidence anchoring. Conversion post.
   - Post 3 (week 2): useful content post: "Six things you can learn from a UK charity's filing patterns" — product mentioned once at the end.
2. **Reddit** — r/UKAccounting, r/UKBusiness, r/UKSmallBusiness. Lead with the artefact: "I built a thing — here's a free sample probity check on [household-name entity]. Honest feedback welcome." One post per sub, two-week gap.
3. **Indie Hackers + Hacker News** — single launch post: "Show HN: Probitas — UK company/charity due diligence in 90 seconds for £15." Lead with the technical depth (multi-source sanctions, deterministic hallucination check) — HN audience values craft.
4. **Direct outreach to 50 small accountancy firms** — find them via Companies House search filtered to "Activities of accounting, book-keeping and auditing", SIC 69201, 1–10 employees. Cold email is the slowest channel but the highest-converting. Aim for 5 firm sign-ups in month 1.
5. **Product Hunt** — week 4 post-launch only, after the LinkedIn flywheel has built. PH skews US/consumer so ROI is uncertain; don't burn the launch on it.

**Channels NOT to use at launch:** Twitter/X (low UK B2B intent), Facebook ads (wrong audience), Google Ads (£15 AOV × accountancy-firm CPCs of £8+ = unprofitable until the Firm tier kicks in).

**Note on the founder voice:** LinkedIn is the right channel because it's where Sarah is — but the *content* is about Probitas, not about you. No "my journey", no "founder story", no humblebrag. Product-led posts that happen to come from your account.

**Success metric for launch month:** 50 retail purchases + 5 firm trials. £750 retail + 5 firm trials = ~£900 in revenue if 1–2 firms convert. Profitability isn't the month-1 goal — validation is.

### 13.10 Post-purchase moment

The single highest-leverage place to build the brand is the email a customer gets 90 seconds after paying. Draft:

> Subject: Your Probitas report on **[Entity Name]** is ready
>
> Hi [first name],
>
> Your report is ready. The headline:
>
> **Risk level: [LOW/MEDIUM/HIGH/CRITICAL]** · 24 of 30 claims evidence-backed
>
> [ View report → ] (link valid 90 days)
> [ Download PDF ]
>
> Two things worth knowing:
>
> 1. This is research, not regulated advice. If you're under MLR 2017, apply your own CDD procedures on top of this.
> 2. If anything looks wrong, reply to this email — we'll re-run it for free.
>
> — Probitas
> [probitas.co.uk]

Honest about limits, offering a real reply path, branded as the product, not the founder. That email does more long-term brand work than any paid channel.

---

## 14. Known issues + deferred work

These came out of first-pass user testing of the live frontend. They're deliberately deferred to keep the current commit focused; each is real and tracked.

| Issue | What's wrong today | Fix shape | Effort |
|---|---|---|---|
| **Trustees table** shows "no Companies House link" for every trustee | The current enrichment looks for trustees' *Companies House* directorships, but charity trustees usually aren't on CH. The right data is *other charity trusteeships*, which the Charity Commission shows on each charity's page but not via the public API. | Either scrape the public CC web pages (per-charity trustee section) or enrich from the bulk CC datasets (download CSV, build a name → charities index in SQLite). The latter is more robust. | ~1 day |
| **Visual polish in the report** — basic tables, no charts, no maps | The report renders markdown straight; the user expects financial-trend charts, geographic risk maps, beautiful tables. | Add a small charting layer (recharts or Chart.js) and a UK/world country map (D3 or react-simple-maps) keyed off the country-risk dict. Style the markdown tables with a richer set of CSS rules. | ~1 day |
| **Social-media OSINT misses real accounts** | The current scrape uses Tavily/Serper text searches and often returns nothing; even when an account exists, we don't surface the link. | Two-pronged: (a) parse `og:` and `link rel="me"` tags directly from the entity website if we have it; (b) add a manual website-override input on the preview page (now SHIPPED in this commit) so the user can hand us the right URL. | (a) ~half day, (b) DONE |
| **Company pipeline returning Unknown entity for valid numbers** (e.g. 13211214 = Wise PLC) | The pipeline's `run_company_check_node` either threw silently or returned no `company_name` for some companies. `fetch_company_full_profile` works correctly when called directly — so the loss happens inside the pipeline node's exception handling. | Add structured error capture inside `run_company_check_node`; expose the failure reason via the bundle's `errors` field; surface it on the report viewer. | ~2 hours |
| **`Read time` and `cost` exposed to users** | Internal pipeline timings and our LLM cost showing in the report viewer header — confusing and reveals our margins. | SHIPPED in this commit — those fields removed from the user-facing meta and right rail. | DONE |
| **HRCOB references in narrative** | "HRCOB" and "HROB" are internal jargon from the codebase's previous incarnation, leaking into customer-facing report text. | SHIPPED in this commit — all uppercase HRCOB/HROB labels replaced with neutral terms ("Core Controls", "High-Risk Onboarding"); internal data-key references kept so the pipeline data still flows. | DONE |
| **Stripe Checkout** | Paid path returns 402 for now; admin bypass code is the only way to start a run. | Wire Stripe Hosted Checkout, webhook handler triggers `runner.start_run` on `checkout.session.completed`; admin bypass stays for testing. | ~half day |
| **PDF download + email delivery** | "Download PDF" and "Email me a copy" buttons are placeholders. | `@react-pdf/renderer` server-side or Puppeteer; Resend transactional email with the signed report link. | ~half day |

---

## 15. What happens next

Decisions are locked. Build sequence:

1. **Week 0** *(founder)*: Incorporate Probitas Ltd via Companies House (£12); confirm + register `probitas.co.uk` (Cloudflare Registrar); open Tide business account; engage UK solicitor for T&Cs / privacy policy / disclaimer.
2. **Week 0** *(me)*: Update the design brief with Probitas brand locked; spin up Postgres schema + FastAPI bridge skeleton.
3. **Weeks 1–2** *(me)*: FastAPI bridge → Stripe Checkout → webhook → pipeline trigger end-to-end.
4. **Weeks 2–4** *(me)*: Next.js frontend — landing (with the British Red Cross sample report visibly embedded), search, checkout flow, live progress page, report viewer.
5. **Week 4** *(me)*: Email templates via Resend, PDF generation, the full transactional loop.
6. **Week 5** *(both)*: Sentry + uptime monitoring; T&Cs delivered by solicitor; soft-launch to 5 friendly users (find via your network); fix what they break.
7. **Week 6** *(founder, with my support)*: Public launch per §13.9 plan. LinkedIn post Sunday evening.

The backend is done. Everything from here is wiring + UI + go-to-market.
