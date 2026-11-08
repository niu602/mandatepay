# MandatePay — A Company Card for AI Agents

> **A company card for AI agents: rules in plain English, spending in bounds, every dollar audited.**

PayPal AI Hackathon 2026 · Submission deadline: **2026-11-13 04:00 Beijing time**

Today an AI can write code, send email, and book meetings — but nobody grants an AI
payment authority, because the money is gone if it goes wrong. MandatePay gives an AI
agent a **spending cage**: the user states constraints in plain English (monthly budget,
vendor allowlist, per-transaction threshold), and the agent executes purchases inside
those bounds. Every transaction produces a **three-layer Mandate record**
(Intent → Cart → Payment) that can be audited line by line.

**The pitch in one line:** everyone else is building *"an AI that buys things."*
We are building *"an AI that is allowed to buy things."*

<!-- DEPLOYED_URL -->
**Live instance:** <https://mandatepay-zisen-dph6eucmf4gjckct.eastasia-01.azurewebsites.net>
<sub>Azure App Service (B1, East Asia) · sandbox mode · public URL, no login required</sub>

Prefer to run it yourself? The instructions right below are the primary path —
15 minutes, no questions asked.

---

## Target award

**Best Use of Agentic Commerce** ($5,000)

---

## Run it in 15 minutes

This is the part that matters most — a stranger should be able to clone this and get it
running without asking a single question.

### 1. Requirements

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.11+ | 3.11.9 pinned in `.python-version` (platforms default to 3.14, which is too new) |
| pip packages | see `requirements.txt` | installs in ~1–2 min |
| Database | **optional** | SQLite by default (zero config). PostgreSQL only if you want it |
| PayPal account | **required** | Sandbox credentials, free — steps below |
| LLM endpoint | **optional** | Any OpenAI-compatible endpoint. Without a key the agent falls back to rule-based parsing |

```bash
git clone https://github.com/niu602/mandatepay.git
cd mandatepay
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Get your PayPal sandbox credentials (3 steps, ~3 minutes)

1. Go to **developer.paypal.com/dashboard/applications/sandbox**
   → sign in (create a developer account if you don't have one).
2. Switch the **Sandbox / Live** toggle to **Sandbox** (top right), then open the
   **Apps & Credentials** tab → **REST API apps** → **Create App**.
   Give it any name (e.g. `mandatepay`) and pick type **Merchant**.
3. Copy **Client ID** and **Secret** from the app page (the "Sandbox" tab, not Live).

Then create a **sandbox buyer account** to pay with:

- **developer.paypal.com → Testing Tools → Sandbox Accounts** → **Personal** account
  → **Create Account**. Give it an email and a password you can remember.
  Fund it (sandbox balance is fake money — give it $5,000).
- **Sandbox test card** (works at the API level, used by the automated regression test):
  VISA `4333134069790191`, expiry `11/31` (i.e. 2031-11), any 3-digit CSC.

### 3. Configure

```bash
cp .env.example .env
```

Fill in `.env` — every value says where to get it:

```bash
# ── PayPal sandbox (from step 2) ────────────────────────────────────
PAYPAL_MODE=sandbox
PAYPAL_CLIENT_ID=your_sandbox_client_id      # ← step 2, "Apps & Credentials"
PAYPAL_CLIENT_SECRET=your_sandbox_secret      # ← step 2, "Apps & Credentials"

# ── Session signing key (public deployment: strongly recommended) ───
# Generate: python -c "import secrets; print(secrets.token_urlsafe(48))"
SESSION_SECRET=                               # ← leave empty locally, it self-derives

# ── LLM (any OpenAI-compatible endpoint; optional) ──────────────────
# Z.ai — free tier, sign up with an overseas account:
LLM_API_KEY=sk-xxxxxxxxxxxxxxxx
LLM_BASE_URL=https://api.z.ai/api/paas/v4
LLM_MODEL=glm-4.7-flash

# ── Database (optional — leave empty for SQLite) ────────────────────
DATABASE_URL=

# ── Webhook (optional — see below) ──────────────────────────────────
PAYPAL_WEBHOOK_ID=
```

> **`.env` is gitignored.** Never commit real credentials. `scripts/scan_secrets.py`
> scans the whole working tree *and* the entire git history for leaked keys, and it
> self-checks its own rules — run it before every commit.

### 4. Initialise and run

```bash
python scripts/init_db.py            # idempotent schema creation (SQLite by default)
python scripts/seed_catalog_local.py # load the supplier catalog (Notion $10 / Figma $15)
                                    # ← no PayPal credentials needed for this step

python -m uvicorn app.main:app --port 8888
```

Open **http://127.0.0.1:8888/** — you should see the PayPal sandbox payment page.

> **Two catalog scripts, and the difference matters:**
>
> | Script | Needs PayPal credentials | What it does |
> |---|---|---|
> | `scripts/seed_catalog_local.py` | ❌ **no** | Writes the catalog into your local database only. Safe, offline, idempotent. **Start here.** |
> | `scripts/setup_catalog.py` | ✅ yes | Creates real products/plans **in the PayPal sandbox** via the Agent Toolkit, so the agent can order against real `paypal_product_id`s. Optional — run it after you've got credentials. |
>
> The local seed is what makes the "clone and run in 15 minutes" path work: you can
> browse every page and see the catalog without touching PayPal. `seed_catalog_local.py`
> skips itself if the catalog already has entries, so it never duplicates suppliers.

### 5. Verify it actually works

```bash
# Automated sandbox payment loop (create → simulate approve → capture → assert COMPLETED)
python -m agent.e2e_check --auto --amount 7.5

# Constraint engine test suite (113 tests, ~2s, fully offline)
python -m pytest tests/ -q

# LLM intent parsing: 20 sentences × 3 runs, with degradation accounting
python scripts/verify_llm_zai.py --repeat 3 --thinking disabled
```

> **Already set up locally?** [STARTUP.md](STARTUP.md) is the developer quick
> reference — one-screen start commands, the 30-second post-start self-check,
> the day-to-day edit→verify→commit loop, Windows/Linux differences, and how to
> clear leftover ports. This section is the first-time setup path; that one is
> for day two.

---

## What each page shows

| Page | What you see | Expected result |
|---|---|---|
| `/` | PayPal sandbox checkout button | Creates a real sandbox order; the buyer approves in the PayPal popup, then the page captures it → order status `COMPLETED` |
| `/constraints` | One text box + structured rule cards + a live demo panel | Type a sentence like *"SaaS budget 200 dollars a month, anything over 50 ask me first, only Notion and Figma"* → it becomes structured rules; the demo panel shows an **insight** (spend context) and an **explanation** (why a decision came out the way it did) |
| `/budget` | AG Studio dashboard: monthly budget used / remaining / projected month-end / per-supplier breakdown | Renders with real data from `/api/budget/summary`. If the AG Studio runtime can't load, the page **automatically falls back** to a hand-built view — same data, plain HTML |
| `/approvals` | List of purchase requests, each with a decision badge | Shows `AUTO_APPROVE` / `REQUIRE_HUMAN` / `REJECT`. A `PENDING_HUMAN` request has a **"Go to PayPal"** + **"I paid, capture it"** button pair — the human-authorisation half of the loop |
| `/audit` | Every transaction as three stacked cards | One row per transaction → **Intent** (what was allowed) / **Cart** (what was bought) / **Payment** (how the money moved), tied together by a `trace_id`. Raw JSON is available from `GET /api/mandates` |

### The three scenarios (expected decisions)

With the default constraints — monthly budget **$200**, per-transaction threshold **$50**,
vendor allowlist **[Notion, Figma]**:

| Purchase | Decision | Why |
|---|---|---|
| Notion Pro · $10 | `AUTO_APPROVE` | Vendor allowlisted, well under the threshold |
| Figma Enterprise · $80 | `REQUIRE_HUMAN` | Allowlisted but over the $50 threshold → stops and asks a human |
| Slack Pro · $15 | `REJECT` | Not on the allowlist → refused outright, with an explanation of how to fix it |

> These are **not hardcoded**. The decisions come out of `agent/constraints.py::evaluate()`,
> a pure function, and the table above is what that function actually returns
> (verified against the production engine, not asserted by hand).

---

## Why the agent stops and asks

When the agent places an order, PayPal returns `PAYER_ACTION_REQUIRED` and an
`approval_url`. A human has to open that URL and approve the payment before it can be
captured.

**This is not a bug. It is the product.**

1. MandatePay's entire premise is *"the AI must not spend money behind your back."*
   Fully automatic capture contradicts that premise directly.
2. PayPal's own agentic-commerce model is *"a human pre-authorises a scope; the agent
   acts inside it"* — not *"the agent has the card."*
3. Judges evaluate the **safety of the interaction**, not the degree of automation.

So the front-end ships a **"Go to PayPal"** + **"I paid, capture it"** button pair:
**human authorises, machine executes.** That is the correct shape of agentic commerce.

> **We deliberately do not automate the browser.** Not because it's hard — because the
> failure modes are wrong. PayPal's guest checkout treats any non-test email as a new
> buyer and walks it through account creation (password → date of birth → ID →
> postcode → address verification → SMS code). Sandbox doesn't send SMS, so that path
> is a dead end. Using the **sandbox buyer account** avoids the whole flow.
> See [DEMO_SCRIPT.md](DEMO_SCRIPT.md) for the recorded findings.

---

## Architecture

```
                     ┌─────────────────────── Browser ───────────────────────┐
                     │  /  /constraints  /budget  /approvals  /audit        │
                     │      (static/*.html — plain HTML, no build step)      │
                     └───────────────────┬───────────────────────────────────┘
                                         │ same-origin fetch (session cookie)
                     ┌───────────────────▼───────────────────────────────────┐
   REST path         │            FastAPI  (app/main.py)                    │
   ──────────────►  │  session middleware → 7-node LangGraph state machine  │
                     └───────────────────┬───────────────────────────────────┘
                                         │
        ┌────────────────────────────────┼────────────────────────────┐
        │                                │                            │
┌───────▼────────┐            ┌──────────▼─────────┐         ┌────────▼────────┐
│ PayPal REST    │            │  Constraint engine  │         │  LLM intent     │
│ Orders v2      │            │  (agent/            │         │  parser         │
│ (app/          │            │   constraints.py)   │         │ (agent/         │
│  paypal_client)│            │  pure function      │         │  llm_intent.py) │
└────────────────┘            │  89 unit tests      │         │ OpenAI protocol │
                             └──────────┬──────────┘         └─────────────────┘
                                        │
                         ┌──────────────▼──────────────┐
                         │  Three-layer Mandate         │
                         │  Intent → Cart → Payment     │
                         │  (agent/store.py, PG/SQLite) │
                         └─────────────────────────────┘
```

### The seven-node state machine

| Node | Role | AI? |
|---|---|---|
| `parse_node` | Natural language → structured `PurchaseRequest` | ✅ LLM function calling |
| `validate_node` | Evaluate against the constraint engine | ❌ **deterministic** |
| `insight_node` | "You've spent $170 of $200 this month; this $10 fits, $20 left" | ✅ LLM |
| `explain_node` | "Blocked: over your $50 threshold — raise it or split the order" | ✅ LLM |
| `approve_node` | Decide AUTO_APPROVE / REQUIRE_HUMAN / REJECT | ❌ **deterministic** |
| `pay_node` | Create the real sandbox order via PayPal Agent Toolkit | ❌ |
| `verify_node` | Confirm the order state | ❌ |

> **Hard rule: constraint checking and approval decisions are deterministic code, never
> the LLM.** The model decides *what the user meant*; the engine decides *whether it's
> allowed*. Both AI nodes have pure-template fallbacks, so the demo never breaks even
> if the endpoint is down.

---

## LLM endpoint: fully swappable

Intent parsing speaks the standard **OpenAI protocol**, so **any OpenAI-compatible
endpoint works** — change three environment variables, zero business-code changes.

| Endpoint | `LLM_BASE_URL` | `LLM_MODEL` |
|---|---|---|
| **Z.ai** (used here, free tier, overseas sign-up) | `https://api.z.ai/api/paas/v4` | `glm-4.7-flash` |
| Tencent MaaS | `https://tokenhub.tencentmaas.com/v1` | `glm-5.3-flash` |
| DeepSeek | `https://api.deepseek.com` | `deepseek-chat` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` |

**Measured on Z.ai `glm-4.7-flash`: 20 sentences × 3 runs = 60/60 correct, 20/20 stable
across runs, 0 silent degradations.** The 20-sentence set covers all three decision
scenarios plus threshold boundaries ($50 vs $50.01) and two reasoning cases (price
comparison across the catalog, and disambiguation by product category).

The expected outcomes are **hand-enumerated and frozen in the script's `CASES` table** —
not model self-assessment, not back-filled after the fact — and the decisions come from
the same `evaluate()` the production server uses. Full report:
[LLM_INTENT_VERIFICATION.md](LLM_INTENT_VERIFICATION.md).

**Engineering note — free-tier 429s are normal.** Free endpoints randomly return `429`
with code `1305` (overloaded) or `1302` (rate limited); measured success rate at peak
was ~20%. That's endpoint capacity, not a code bug. Three layers handle it:
client-side rate limiting (`LLM_MIN_INTERVAL`), exponential backoff with ±30% jitter
(8→16→32→45s, 6 attempts), and **observable degradation** — `agent/llm_intent.py`
maintains counters (`calls`/`success`/`rate_limited`/`retries`/`degraded`/`no_tool_call`)
so a report can never claim "verified" while the AI path was silently down.

---

## Three ways to reach PayPal — and why you need all three

| Path | Used for | What it covers that the others don't |
|---|---|---|
| **REST Orders v2** | The web checkout page | The canonical API every integration starts with. Verified end-to-end with a real completed sandbox order |
| **PayPal Agent Toolkit** | The LangGraph agent | The official agent-native toolkit — same sandbox account, different SDK shape (note: its `create_order` payload differs from REST's `purchase_units[].amount.value`) |
| **PayPal MCP Server** | Capability extension | Tool-call protocol for agent frameworks. Complements rather than replaces the other two |

All three talk to the **same sandbox account and the same MandatePay constraint engine**
— the guarantee that money stays in bounds does not depend on which integration path
a purchase came in through.

---

## AG Studio: dashboards defined by code, decisions still in tested code

The `/budget` page is rendered by [AG Studio](https://www.ag-grid.com/ag-studio/) —
but with a deliberate split:

1. **The dashboard is pre-defined in code.** `createStudio()` + `setState()` in
   `static/budget.html` describe the KPIs, charts and layout programmatically. No one
   drags widgets in an editor; the dashboard is reviewable in a diff.
2. **Rendering and decision-making are separated.** AG Studio owns *presentation* —
   KPI tiles, charts, cross-filtering. The **budget arithmetic and every constraint
   decision stay in our own testable Python**. The 650-judgement stress report
   ([STRESS_REPORT.md](STRESS_REPORT.md)) is the reason we're comfortable doing it that way.

The backend was **not modified** to adopt AG Studio — `/budget` reads the same
`/api/budget/summary` endpoint it always did, and the page shapes the data client-side.
AG Studio is an npm commercial product with a 45-day free trial (fully functional, no
UI watermark). The page probes for a local runtime and **falls back to jsDelivr CDN**
if it isn't installed — so a fresh clone works with zero extra setup, and if the
runtime ever fails to load the page degrades to a hand-built view instead of a blank page.

---

## A real vulnerability we found and fixed

The constraint engine is stress-tested against **three independent oracle
implementations** (a spec-literal one, one reproducing the pre-fix behaviour, and one
derived from user intuition) over 50 random constraints × 13 requests = **650
judgements**, 550 of them adversarial.

One finding mattered:

> **Class F — category allowlist silently skipped when the category is unknown.**
> 170 of 650 cases (**26.15%**). When a constraint restricted the allowed *categories*
> but the incoming request carried no category field, the category check was skipped
> entirely — the constraint was effectively void. A security hole, not a cosmetic one.

The fix: an unknown category with a category allowlist set now returns
`REQUIRE_HUMAN` instead of silently passing — not a hard reject (the field is often
legitimately absent, and rejecting would break legitimate purchases), but never a
silent allow. After the fix: **0 false rejects, 0 false allows.**

The other findings were over-strict rejections — `notion` vs `Notion`, a stray space
from copy-paste — now fixed by normalising the comparison.

> ⚠️ **Precise wording matters here:** 26.15% is the **pre-fix** figure. The
> vulnerability is **found and closed**. This is a closed-loop story, not a live hole.
> See [STRESS_REPORT.md](STRESS_REPORT.md) for the full breakdown.

**The methodology point worth stealing:** the expected results are hand-enumerated and
frozen into the repository — *"N attack cases, expectations written by a human, not
model self-assessment."* The LLM only ever explains *why* something is a vulnerability;
it never enters the decision chain.

---

## API authentication

`/api/*` requires a **signed session cookie**. The page routes are exempt and issue
the cookie on visit, so **the front-end needs zero changes** — same-origin `fetch`
carries it automatically.

```bash
curl -i https://<host>/api/mandates            # → 401 (no cookie)
curl -c cookies.txt https://<host>/audit        # get a session
curl -b cookies.txt https://<host>/api/mandates # → 200
```

Exempt: the five page routes, `/healthz`, `/api/health`, and **`/api/webhook`**
(PayPal calls that endpoint server-to-server and cannot carry a browser cookie — it has
official PayPal signature verification instead). Details:
[W08_API_AUTH.md](W08_API_AUTH.md).

---

## Webhook — optional, and the main flow does not depend on it

**The webhook is not required.** The core demo loop is *"agent orders → human approves
on PayPal → click 'I paid, capture it' → the server captures and marks the Mandate
COMPLETED."* That works without a webhook.

The webhook exists so that PayPal can *push* status changes (`PAYMENT.CAPTURE.COMPLETED`,
`CHECKOUT.ORDER.APPROVED`, `PAYMENT.CAPTURE.DENIED`, `PAYMENT.CAPTURE.PENDING`) instead
of the server polling. When configured, `/api/webhook` verifies PayPal's official
signature (`PAYPAL-TRANSMISSION-ID` / `-SIG` / `-TIME` / `-CERT-URL` / `-AUTH-ALGO`
plus `PAYPAL_WEBHOOK_ID`) against PayPal's own verification endpoint and **only then
processes the event**. Events are stored idempotently by `event_id`.

Skip it if you just want to look around. `python scripts/setup_webhook.py --create --url
<your-public-url>/api/webhook --force` registers it if you want the full loop.

---

## Deployment (free tier, no credit card)

```bash
# 1. Database — Neon free PostgreSQL (console.neon.tech → create project → copy URI)
#    Free projects only ever sleep; they are never auto-deleted.
# 2. App — Azure App Service F1 via Azure for Students (no card required for students)
#    → Runtime Python 3.11, Plan Free (F1), Deployment Center → GitHub
#    → Startup Command: bash startup.sh
#    → Environment variables: DATABASE_URL + PayPal creds + LLM key + SESSION_SECRET
# 3. Initialise the database
DATABASE_URL=<neon-uri> bash scripts/deploy_init.sh
```

Full platform-selection rationale (including the platforms we ruled out and why),
F1 quota accounting, and the acceptance checklist: **[W06_DEPLOY.md](W06_DEPLOY.md)**.

> **Azure F1 does not support Always On** — an idle instance may be reclaimed, so the
> first visit pays a cold start. But F1 has a hard **60 CPU-minutes/day** limit, so
> the keep-warm probe has to be *low frequency* (30 min ≈ 1.5 CPU min/day, 2.5% of
> budget) and it has to run **outside** the F1 instance. Tooling:
> [W07_KEEP_WARM.md](W07_KEEP_WARM.md).

---

## Known limitations

Stated plainly, because a demo that oversells itself is worse than one that doesn't.

1. **No account system.** Any visitor who opens a page gets a session. The goal is to
   stop casual scripted scraping, **not** to stop a determined attacker. There is no
   rate limiting and sessions aren't per-user. This is a demo, not a payment product.
2. **The agent cannot complete a payment on its own** — by design (see above).
3. **AG Studio runs in trial mode** (45 days, fully functional, no UI watermark). The
   page falls back to a hand-built view if the runtime fails to load.
4. **Free-tier LLM endpoints are unreliable under load** (measured ~20% success at
   peak). Handled with retries and observable degradation, but it is a real constraint.
5. **The LLM is a single point of failure for intent parsing** — mitigated by a
   rule-based fallback, which is why the demo never hard-stops.
6. **The MCP integration is minimal** — one capability, deliberately, to avoid
   disturbing the two verified paths.

---

## Repository layout

```
mandatepay/
├── app/
│   ├── config.py           # reads .env (credentials, sandbox/live switch)
│   ├── main.py             # FastAPI: pages + all API + session middleware
│   ├── paypal_client.py    # PayPal REST (token / create order / capture) — web path
│   └── session.py          # signed session cookie (issue + verify)
├── agent/
│   ├── models.py           # Constraint / PurchaseRequest / MandateRecord
│   ├── constraints.py      # constraint engine (pure function, unit-tested)
│   ├── intent.py           # intent entry point (LLM first, rules as fallback)
│   ├── llm_intent.py       # LLM function-calling parser (OpenAI protocol)
│   ├── ai_insight.py       # the two AI decision nodes (insight / explain)
│   ├── paypal_tools.py     # PayPal Agent Toolkit wrapper — agent path
│   ├── graph.py            # 7-node state machine
│   ├── store.py            # persistence (dual backend: PostgreSQL / SQLite)
│   ├── e2e_check.py        # payment loop verification script
│   └── demo.py             # four-scenario end-to-end demo
├── scripts/
│   ├── init_db.py                # idempotent schema creation
│   ├── seed_catalog_local.py     # local catalog seed (no PayPal credentials needed)
│   ├── setup_catalog.py          # create real products in the PayPal sandbox
│   ├── setup_webhook.py          # register webhook subscriptions via REST
│   ├── deploy_init.sh            # one-shot: schema → seed → catalog → clean
│   ├── reset_demo_data.py        # clear demo data (keeps constraints)
│   ├── seed_demo_scenarios.py    # rebuild the 3 demo transactions (one per decision)
│   ├── keep_warm.py              # keep-warm / pre-warm / cold-start probe
│   ├── stress_constraints.py     # 650-judgement stress test, 3 oracles
│   ├── verify_llm_zai.py         # 20-sentence intent verification + degradation stats
│   ├── scan_secrets.py           # wide-criteria key scan (tree + full history + self-check)
│   └── migrate_sqlite_to_pg.py   # SQLite → PostgreSQL migration
├── static/
│   ├── index.html          # PayPal checkout page (web path)
│   ├── constraints.html    # natural-language constraint setup
│   ├── approvals.html      # approval desk
│   ├── audit.html          # three-layer Mandate audit + human confirmation
│   ├── budget.html         # AG Studio dashboard (with classic fallback inside)
│   └── budget.orig.html    # ARCHIVED pre-AG-Studio snapshot (no route, not a live page)
├── tests/                  # 113 tests: constraints + session auth + keep-warm + UI English
├── scripts/
│   ├── check_ui_english.py     # static check: no pure-Chinese visible blocks
│   ├── check_ui_rendered.py    # headless-browser check: renders each page, verifies the same
│   └── …                       # (see the full list above)
├── Dockerfile / Procfile / startup.sh / render.yaml   # deployment assets
├── LLM_INTENT_VERIFICATION.md   # 20 × 3 intent parsing report
├── STRESS_REPORT.md             # 650-judgement constraint stress report
├── W05_ZAI_MIGRATION.md         # endpoint migration + 429 handling
├── W06_DEPLOY.md                # deployment record + platform rationale
├── W07_KEEP_WARM.md             # F1 keep-warm / pre-warm handbook
├── W08_API_AUTH.md              # session auth design + DoD
├── STARTUP.md                   # local dev quick reference (start / verify / troubleshoot)
└── LICENSE                      # MIT (Copyright 2026 Niu Zisen)
```

---

## UI language

The five pages are **bilingual, English-first** — every visible string carries English,
with the Chinese kept alongside it where it helps (this is a Chinese-voiceover demo with
English subtitles, so the two are meant to sit together). **No visible block is
Chinese-only.**

Two checks enforce it, and both are cheap to run:

```bash
python scripts/check_ui_english.py     # static — reads the HTML source
python scripts/check_ui_rendered.py    # headless Chrome — renders each page and checks the result
```

The second one exists because static analysis misses a real case: **Chinese that only
appears after JavaScript runs** — e.g. AG Studio's KPI labels are pre-defined in
`createStudio`/`setState`, not in HTML text nodes, and a `<span>` around the Chinese
splits it into a separate text node that looks Chinese-only in the DOM but renders as
one bilingual line. Rendering is the only way to be sure.

`python -m pytest tests/test_ui_english.py` runs the static half as part of the
normal suite.

---

## Rules we held ourselves to

- Sandbox only — no real money, ever
- `.env` never enters git; no secret in code, docs, screenshots or commit history
- `scripts/scan_secrets.py` (working tree + full history + self-check) before every commit
- MIT licensed, signed `Niu Zisen`

---

*PayPal AI Hackathon 2026 · MandatePay — A Company Card for AI Agents*
