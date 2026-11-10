# MandatePay — A Company Card for AI Agents

> **Devpost submission copy.** Five sections, one per judging criterion.
> Every factual claim below is traceable to a file in this repository — the
> "Source" line at the end of each section points at it.
>
> Deadline: 2026-11-13 04:00 Beijing time.

**Live instance:** https://mandatepay-zisen-dph6eucmf4gjckct.eastasia-01.azurewebsites.net
(Azure App Service (B1, East Asia) · sandbox mode · public URL, no login required)

**Repository:** https://github.com/niu602/mandatepay

**Target awards:** Best Use of Agentic Commerce ($5,000) · AG Grid sponsor prize

**Video:** <!-- VIDEO_URL -->

**Sandbox buyer** (for judges who want to complete the loop themselves):
`sb-mtet153479750@personal.example.com` — a PayPal *sandbox* buyer, so no real
money can move. This account pays from its sandbox balance directly, which is
what keeps the checkout on the sandbox buyer path instead of triggering PayPal's
new-account creation flow. Use it from the PayPal Developer Dashboard → Sandbox
→ Accounts if you need to complete the "Approve & pay" step yourself.

---

## Technological Implementation

MandatePay is a working system, not a prototype. It runs end-to-end on PayPal's
sandbox, and the whole loop — *natural-language constraint → agent decision →
PayPal order → human approval → capture → auditable record* — has been exercised
against the live sandbox, including a real completed order visible in the PayPal
Developer Dashboard.

**Three PayPal integration paths, one sandbox account, one constraint engine.**

1. **REST Orders v2** — the web checkout page. Verified end-to-end with a real
   completed sandbox order.
2. **PayPal Agent Toolkit** — the LangGraph agent. The official agent-native
   toolkit; note its `create_order` payload shape differs from REST's
   `purchase_units[].amount.value`.
3. **PayPal MCP Server** — the official tool-call protocol. One capability wired
   up end-to-end (`invoices.list`), speaking MCP's stdio JSON-RPC 2.0 directly
   from Python.

The third path is the interesting one, because it is also the dangerous one, and
we treat that as a design constraint rather than a footnote. PayPal's MCP server
exposes **28 tools**, and many of them move money directly: `orders.capture`,
`payments.createRefund`, `subscriptions.cancel`, `pay_order`, `invoices.send`.
MCP is a *tool-call protocol* — it executes what the tool says, immediately, with
no policy layer in between. So an agent handed raw MCP access could capture an
order or cancel a subscription without the constraint engine ever seeing it,
which is precisely the failure mode this project exists to prevent.

So MCP sits behind a gate (`agent/mcp_guarded.py`) with a **read-only allowlist**
(`invoices.list`) and an explicit **money-moving denylist** covering nine tools,
each annotated with why it is blocked. A blocked tool is rejected *before* any
PayPal API call is made — the MCP process is never even started. Meanwhile REST
and the Toolkit both route every money movement through the same deterministic
`evaluate()` decision function. That is the substantive reason MCP complements
those two paths rather than replacing them: **the other two cannot skip the
constraint check; raw MCP can, so we don't let it.**

**Constraint decisions are deterministic code. The LLM is never in the decision
chain.** It parses intent and explains decisions; the verdict itself comes from a
pure function with a fixed check order (vendor allowlist → category allowlist →
monthly budget → per-transaction threshold → daily cumulative → unknown category).
Two independent test suites assert against the *same* engine rather than
re-implementing it, so test logic can never drift from production logic.

**The full stack is deliberately boring and fully open:** FastAPI + LangGraph +
PostgreSQL (Neon) on Azure App Service, with an AG Grid **AG Studio** dashboard
on the budget page. The dashboard is defined in code (`createStudio()` +
`setState()`), so it is reviewable in a diff rather than rebuilt by hand in a
drag-and-drop editor, and it ships with an automatic fallback to a classic view
if the runtime fails to load.

*Source: `README.md`, `app/main.py`, `agent/mcp_client.py`, `agent/mcp_guarded.py`,
`agent/constraints.py`, `scripts/demo_mcp.py`*

---

## Design

The design starts from a single observation: **an AI agent's payment authority is
currently binary.** Either the agent asks you for every transaction — useless at
any volume — or it holds a card and you have no idea what it will do. Both failure
modes come from the same missing piece: there is no artifact that says *what was
allowed, what was bought, and how the money left*, in a form a human can check.

MandatePay's answer is a **three-layer Mandate record**. Every transaction
produces three rows sharing one `trace_id`:

| Layer | The question it answers |
|---|---|
| **Intent** | What is this agent allowed to buy? (constraints in force at decision time) |
| **Cart** | What is it buying this time, for how much? |
| **Payment** | How did the money leave, under whose approval? |

Splitting one record into three is what makes the chain auditable *per layer* —
each has its own status and timestamp, so a reviewer can see not just "it bought
Figma" but "it was allowed to buy Figma, it decided to buy Figma Enterprise at
$80, and a human released that $80." The `/audit` page renders all three layers
side by side.

**The decision surface is a constraint language, not a prompt.** The user states
limits in plain English — *"two hundred a month for SaaS, over fifty ask me
first, only Notion and Figma"* — and the agent parses that into a structured
constraint set: monthly rolling budget, per-transaction threshold, vendor
allowlist, category allowlist. Every subsequent purchase is judged against those
four fields by a pure function returning exactly one of three verdicts:
`AUTO_APPROVE`, `REQUIRE_HUMAN`, `REJECT`.

**A rejection is a product surface, not an error message.** When the agent says
no, it produces two distinct things: an *insight* (where your budget stands right
now) and an *explanation* (which rule blocked this, and what to change). The
explanation is generated from the actual constraint and the actual request, and it
ends in an actionable fix — *"add Slack to the allowlist and it can charge the
company card."* That last property matters more than it sounds: a refusal the
user cannot act on is a dead end, and the whole point of granting an agent
authority is to keep the funnel moving.

**Human approval is a first-class state, not a modal.** Over the threshold, the
verdict is `REQUIRE_HUMAN`: the request becomes a pending item on the approval
desk showing all three Mandate layers, and no order reaches PayPal until a
person clicks through. The page states the pending status in the product's own
vocabulary (`PAYER_ACTION_REQUIRED`), so the safety mechanism is legible in the
UI rather than hidden in a log.

**Failure is designed for, not bolted on.** The LLM endpoint is fully swappable
and the agent degrades to rule-based parsing when it is unreachable — and the
degradation is *observable* (counters for calls / successes / rate limits /
retries / degradations), so a verification run can never report success while
the AI path was silently down. Session-scoped cookie auth on `/api/*` keeps the
raw API from being scraped without a login system.

*Source: `README.md` (§"What each page shows", §"Why the agent stops and asks",
§"Architecture"), `agent/models.py`, `agent/constraints.py`, `agent/ai_insight.py`,
`agent/graph.py`, `app/main.py`*

---

## Potential Impact

**Everyone else is building "an AI that buys things." We are building "an AI that
is allowed to buy things."**

That gap is a trust gap, and trust gaps do not close because a model got better.
An agent that can spend money is currently unusable in practice, not because
the AI cannot decide what to buy — modern models are perfectly good at that —
but because *no one can verify what it decided, why, and under what authority*.
Our own stress testing showed the real numbers behind that gap, and they are not
marginal.

**We found a live security hole in our own constraint engine and closed it.** The
engine is checked against **three independent oracle implementations** — a
spec-literal one that verifies the implementation matches the written spec, one
that reproduces the pre-fix semantics to quantify what users had actually been
experiencing, and one derived from what a user would reasonably expect — over
50 random constraints × 13 requests = **650 judgements**, 550 of them
adversarial.

**A note on "independent", since a judge may read the code:** the oracles are
written against the *constraint specification* rather than against the engine's
code — the engine under test and the oracle share no evaluation logic, which is
the whole point. The three run as three different readings of that
specification — spec-literal, pre-fix, and user-intuition — so they can disagree
with each other as well as with the engine.

> **Class F — the category allowlist was silently skipped when the request
> carried no category field.** 170 of 650 cases (**26.15%** — the pre-fix
> figure). When a constraint restricted the allowed *categories* but the incoming
> request carried no category, the category check was skipped entirely. **The
> constraint was effectively void — a security hole, not a cosmetic bug.**
> It was **found and fixed**: an unknown category with a category allowlist set
> now returns `REQUIRE_HUMAN` instead of silently passing — never a hard reject
> (the field is legitimately absent in many valid purchases), but never a silent
> allow. After the fix: **0 false rejects, 0 false allows.**

The other findings were over-strict rejections — `notion` vs `Notion`, a stray
space from a copy-paste — now fixed by normalising the comparison.

**Why this matters beyond our own codebase.** The 26.15% figure is not a
statistic about MandatePay; it is a statistic about *the class of systems being
built right now*. Any agent that enforces policy from structured fields will
have edge cases where the field is missing — and the default behaviour of
almost every implementation is to skip the check. When the missing field is a
policy boundary, skipping it is a security failure that no test written after
the fact would have caught. Evaluation was three-layer: **650 randomized stress cases** checked
against three independent hand-written oracles, **20 human-authored attack fixtures** with
expectations fixed before the engine ever ran, and **20 intent utterances** with hand-written ground
truth — so "the engine agrees with itself" could never be mistaken for "the engine is right."

**Impact if this pattern is adopted.** A spend cage is a reusable primitive.
The same shape — plain-English constraints, a deterministic decision function, a
human checkpoint above the threshold, and a per-layer audit trail — applies to
anything an agent is allowed to do that costs money or consumes a limited
resource: cloud credits, API quotas, ad budgets, database capacity, licence
seats. The three-layer Mandate is the generalisation: Intent = what is permitted,
Cart = what was chosen, Payment = what was consumed and under whose approval. The
implementation here is specific to PayPal; the contract is not.

**And it is verifiable, not asserted.** The same discipline that caught the
vulnerability is what lets a judge check our claims: 20 natural-language
sentences × 3 runs = **60/60 correct verdicts with zero degradations**, against
an assertion set whose expected results were **hand-enumerated and frozen into
the repository before the runs** — not model self-assessment, not back-filled
after the fact.

*Source: `STRESS_REPORT.md`, `README.md` (§"A real vulnerability we found and
fixed"), `scripts/stress_constraints.py`, `scripts/verify_llm_zai.py`,
`LLM_INTENT_VERIFICATION.md`, `tests/fixtures/attack_cases.json`*

---

## Innovation

The novelty is not a new model, a new payment rail, or a new agent framework. It
is a **contract between an agent and its principal** that is explicit enough to
execute automatically and inspectable enough to trust afterwards — and the
specific way we arrived at it, which we think is the reusable part.

**The insight that shaped it:** the interesting question is not "can the AI buy
software?" but "what would make it safe to let the AI buy software?" The
answer is not better AI and not more guardrails bolted around a black box. It is
giving the principal a *legible policy object* — budget, threshold, allowlists —
that the agent can read, the engine can evaluate deterministically, and the human
can audit afterwards. Once that object exists, the AI's role narrows to what it
is uniquely good at (understanding intent, choosing among options, explaining
itself), and the parts where correctness matters (does this exceed the budget? is
this vendor allowed?) move into code a human wrote and a test suite can attack.

**What makes it more than a wrapper.** The constraint engine is a pure function
with a fixed check order and three-valued output, and we treat it as the part
that must be right. Everything else — intent parsing, the two explanation nodes,
even which catalog to query — is allowed to be fuzzy, because it can only ever
*propose*. A rule-based parser would have been dramatically simpler to build, and
it is the wrong trade: the demo's centrepiece is the agent discovering that
*"Notion and Figma — buy the cheaper one"* requires querying two catalogs before
it can decide, and then executing a three-step *query → compare → decide* loop
through tool calls. That is reasoning, not classification, and the tool trace on
screen is the evidence.

**Three integrations, deliberately not unified.** We wired REST Orders v2, the
PayPal Agent Toolkit, *and* PayPal's MCP server — and kept all three, because
collapsing them would have destroyed the property that makes the system
trustworthy. Each path has a different failure surface, and the constraint check
has to sit in front of all of them. The MCP path is where this becomes concrete:
because MCP executes tool calls immediately with no policy layer, we built an
explicit allowlist/denylist gate with nine money-moving tools annotated and
blocked, and a read-only allowlist for everything else. The result is a design
where *adding a capability* and *adding risk* are separate, reviewable decisions.

**The dashboard is code, and it degrades gracefully.** The AG Studio budget page
is defined programmatically — `createStudio()` plus `setState()` — so the
dashboard's shape is reviewable in a diff instead of being reconstructed by hand
in an editor, and the data decisions stay in the tested engine rather than in
chart configuration. If the AG Studio runtime fails to load, the page falls back
automatically to a classic view that still shows the same numbers. A dashboard
that breaks the demo is worse than no dashboard.

*Source: `README.md` (§"Three ways to reach PayPal", §"AG Studio: dashboards
defined by code", §"Architecture"), `agent/constraints.py`, `agent/graph.py`,
`agent/llm_intent.py`, `agent/mcp_guarded.py`, `static/budget.html`*

---

## Presentation

**MandatePay in one line:** a company card for AI agents — rules in plain
English, spending in bounds, every dollar audited.

**The demo is one continuous story, under three minutes, in five shots.**

1. **A one-sentence constraint becomes executable policy.** Type "two hundred a
   month for SaaS, over fifty ask me first, only Notion and Figma" and watch it
   parse into a structured rule set — budget, threshold, vendor allowlist,
   category allowlist. The narration names the mechanism: function calling, not
   keyword matching.
2. **The agent reasons instead of classifying.** Give it "Notion and Figma — buy
   the cheaper one". A rule parser dies here, because "the cheaper one" needs
   both prices first. The tool trace shows `query_catalog(Notion)` →
   `query_catalog(Figma)` → `create_purchase_request`, and the agent concludes
   on its own.
3. **A refusal explains itself.** Ask for Slack Pro at $15. The verdict is
   `REJECT` — but the agent does not just say no. It shows where your budget
   stands, which rule blocked the purchase, and the fix: add Slack to the
   allowlist and it can charge the company card.
4. **A human holds the last key.** The Figma request exceeds the threshold, so
   the verdict is `REQUIRE_HUMAN` and the request waits on the approval desk
   showing all three Mandate layers. The approval releases it, PayPal completes
   the payment, and the shot cuts to the PayPal Developer Dashboard where the
   *same* order shows `COMPLETED` — external proof that the loop is real, not
   self-reported.
5. **Everything is auditable.** The audit page shows every transaction as its
   Intent / Cart / Payment triple under one `trace_id`, and the budget dashboard
   shows used, remaining, and month-end projection — all computed, all
   visible.

**Why the numbers land.** We put our own failures on screen, not just our
successes: 650 judgements against three independent oracle implementations, and
the one that mattered — a category allowlist that was silently skipped in 26.15%
of cases, a security hole, **found and fixed**, now 0 false rejects and 0 false
allows. Then the verification that the fix holds: 60/60 correct verdicts on
hand-enumerated expectations with zero degradations, plus a live, public URL that
judges can open themselves.

**Everything is reproducible in fifteen minutes.** Clone the repository, copy
`.env.example`, fill in PayPal sandbox credentials, and run — no credit card, no
paid service, no hidden setup. The AG Studio dashboard renders from code with an
automatic fallback, and the whole system is one command away from a stranger's
laptop.

**Judges who want to try it themselves** can complete the loop with the PayPal
sandbox buyer credentials above, or just open the public live instance — the
data there already covers all three verdicts (`AUTO_APPROVE`, `REQUIRE_HUMAN`,
`REJECT`) so each screen shows a real case, not an empty state.

*Source: `DEMO_SCRIPT.md`, `DEMO_SCRIPT_EN.md`, `DEMO_SCRIPT_EN.srt`,
`STRESS_REPORT.md`, `LLM_INTENT_VERIFICATION.md`, `README.md`*

---

## Appendix — quick reference for judges

| Item | Value |
|---|---|
| Live instance | `https://mandatepay-zisen-dph6eucmf4gjckct.eastasia-01.azurewebsites.net` |
| Repository | `https://github.com/niu602/mandatepay` |
| Local setup | 15 minutes — copy `.env.example`, fill sandbox credentials, run |
| Sandbox buyer | `sb-mtet153479750@personal.example.com` (sandbox balance; no password needed) |
| Intent verification | 20 sentences × 3 runs = 60/60 correct, 0 degradations |
| Constraint stress test | 650 requests (550 adversarial) × 3 independent oracle readings |
| Security finding | Class F (silently-skipped category allowlist) — **found and fixed** |
| Post-fix accuracy | 0 false rejects, 0 false allows |
| PayPal paths | REST Orders v2 · Agent Toolkit · MCP (read-only allowlist + 9-tool denylist) |
| Video | <!-- VIDEO_URL --> |
