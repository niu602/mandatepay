# MandatePay · Demo Script (EN) — ≤ 3 minutes

> **Read this, then record.** The Chinese script is [`DEMO_SCRIPT.md`](DEMO_SCRIPT.md);
> this file is the same demo, same timecodes, in English.
>
> - **Segment timecodes are identical** to the Chinese script — same second, same thing
>   happening on screen.
> - **Shot ①** (PayPal Developer Dashboard) lives inside scene 4 — see the click path
>   in the segment notes; you can follow it without guessing.
> - **SRT subtitles** live in [`DEMO_SCRIPT_EN.srt`](DEMO_SCRIPT_EN.srt) — every cue is
>   ≤ 2 lines × 42 characters.
>
> **Total runtime: 178.0s** (must stay under 180s).
> Reading pace used for the timings: 2.6 words/second (~157 wpm).
>
> **Terms that are NOT translated** — keep them exactly as written, they are
> identifiers in the product and in the code:
>
> | Term | Where |
> |---|---|
> | `PAYER_ACTION_REQUIRED` | scene 4 — the status shown while waiting for you |
> | `three-layer Mandate` (Intent / Cart / Payment) | scene 4 — the three audit records |
> | `trace_id` | closing — what ties the three layers together |
> | `company card` | scene 3 — the card the agent is allowed to charge |
> | `AUTO_APPROVE` / `REQUIRE_HUMAN` / `REJECT` | scene 3 & closing — the three verdicts |

---

### 0:00–0:20 · Opening: frame the problem

<!-- 中文稿对应：0:00–0:20 · 开场：把问题摆出来 -->

- Home page on screen, then cut to /constraints.
- This segment is narration only — no clicking.

🎙 **You ask an AI to subscribe you to software.**

`0:00–0:03` 【9 词 / 3.2 秒】

🎙 **It gets “under 200 a month.”**

`0:03–0:05` 【6 词 / 2.1 秒】

🎙 **But will it hit pay when you’re not watching?**

`0:05–0:08` 【9 词 / 3.2 秒】

🎙 **Most agents never spend — or spend with no explanation.**

`0:08–0:11` 【10 词 / 3.5 秒】

🎙 **MandatePay: the AI can judge on its own —**

`0:11–0:15` 【9 词 / 3.2 秒】

🎙 **but every judgment is explainable, and every payment gets a human nod.**

`0:15–0:19` 【12 词 / 4.2 秒】


### 0:20–0:55 · Scene 1: one sentence becomes a rule set

<!-- 中文稿对应：0:20–0:55 · 场景一：一句话设定约束 -->

- Type on screen (spend ~8s):
-   Monthly SaaS budget 200 dollars, over 50 ask me first, only Notion and Figma.
- Then click “Parse & set constraints”.
- Structured rule card appears (budget / limits / both allowlists).

🎙 **First, authorize it. One sentence:**

`0:20–0:23` 【5 词 / 3.0 秒】

🎙 **And this isn’t keyword matching. It’s function calling**

`0:31–0:36` 【8 词 / 4.9 秒】

🎙 **The model picks the tool and the arguments.**

`0:36–0:40` 【8 词 / 4.9 秒】

🎙 **It pulled out a monthly budget**

`0:40–0:44` 【6 词 / 3.7 秒】

🎙 **A per-transaction limit, a vendor list, and a category list.**

`0:44–0:50` 【10 词 / 6.1 秒】

🎙 **That’s an executable Intent Mandate.**

`0:50–0:53` 【5 词 / 3.0 秒】


### 0:55–1:35 · Scene 2: reasoning, not classification

<!-- 中文稿对应：0:55–1:35 · 场景二：⭐ 异常推理 -->

- Type on screen (spend ~5s):
-   Notion and Figma — buy the cheaper one.
- Tool trace on screen: query_catalog(Notion) → query_catalog(Figma) → create_purchase_request.

🎙 **Now give it something a human would actually say:**

`0:55–0:59` 【9 词 / 4.4 秒】

🎙 **A rule parser dies here. It can pull out the numbers**

`1:04–1:10` 【11 词 / 5.4 秒】

🎙 **But “the cheaper one” needs both prices first.**

`1:10–1:13` 【8 词 / 3.9 秒】

🎙 **Look at the tool trace**

`1:13–1:16` 【5 词 / 2.4 秒】

🎙 **It queried Notion, then Figma, and only then created the purchase request.**

`1:16–1:22` 【12 词 / 5.8 秒】

🎙 **It found Notion at ten a month, Figma at fifteen — and decided.**

`1:22–1:28` 【13 词 / 6.3 秒】

🎙 **That’s reasoning, not classification. Query, compare, decide — on its own.**

`1:28–1:33` 【11 词 / 5.4 秒】


### 1:35–2:10 · Scene 3: a rejection that explains itself

<!-- 中文稿对应：1:35–2:10 · 场景三：⭐ 被拒时解释 + 建议 -->

- Type on screen (spend ~4s):
-   Buy Slack Pro, 15 dollars.
- Then click “Let the agent handle it”.
- Insight box, then explanation box.

🎙 **Now it hits the allowlist. The verdict is REJECT. And notice**

`1:35–1:40` 【11 词 / 5.1 秒】

🎙 **It doesn’t just say “denied”.**

`1:40–1:42` 【5 词 / 2.3 秒】

🎙 **First, the insight: here’s where your wallet stands this month.**

`1:46–1:50` 【10 词 / 4.6 秒】

🎙 **Second, the explanation: not a cold no — which rule blocked it, and how to fix it.**

`1:50–1:58` 【17 词 / 7.9 秒】

🎙 **That fix isn’t a template — the model generated it**

`1:58–2:03` 【10 词 / 4.6 秒】

🎙 **Add Slack to the allowlist and it can charge the company card.**

`2:03–2:08` 【12 词 / 5.5 秒】


### 2:10–2:40 · Scene 4: a human has to nod  (includes Shot ①)

<!-- 中文稿对应：2:10–2:40 · 场景四：人点头才扣款（含镜头①） -->

- Open a PENDING_HUMAN request in /approvals — the popup shows all three layers.
- Click “① Pay on PayPal” → sandbox “Complete purchase” → back → click “② I’ve paid · capture”.
- Screen shows: ✅ 闭环完成 —— capture xxxxx
- ★ SHOT ① (PayPal Developer Dashboard — the order id must be on screen):
-   1. Open a NEW tab on PayPal Developer Dashboard → Sandbox
-   2. Tab: Activity (or Transactions)
-   3. Find the row by ORDER ID (not the capture id) and click it
-   4. Detail page — HOLD on status = COMPLETED
-   5. On-screen label (added in post): order id in large type, TOP-LEFT corner
-      e.g. ORDER 6S3278047H2709334 — white text on a dark translucent bar
-   6. HOLD 2 seconds on COMPLETED (workshop requires a 2s hold here)

🎙 **But if it decides this is worth buying, it still can’t charge you.**

`2:10–2:16` 【13 词 / 6.4 秒】

🎙 **Over the threshold, so it stops — the status is PAYER_ACTION_REQUIRED.**

`2:16–2:21` 【11 词 / 5.4 秒】

🎙 **Every request carries a three-layer Mandate**

`2:21–2:24` 【6 词 / 3.0 秒】

🎙 **Intent, Cart, Payment.**

`2:24–2:26` 【3 词 / 1.5 秒】

🎙 **Human authorizes, machine executes.**

`2:31–2:33` 【4 词 / 2.0 秒】

🎙 **And here it is in PayPal — order <ORDER_ID>, status COMPLETED.**

`2:33–2:38` 【11 词 / 5.4 秒】


### 2:40–2:59 · Closing: audit trail and numbers

<!-- 中文稿对应：2:40–3:00 · 收尾：数据与审计 -->

- Cut quickly through /audit (three-layer Mandate list) → /budget (dashboard).
- Cut back to the home page at the end.

🎙 **Everything is auditable — used, remaining, and month-end projection, all computed.**

`2:40–2:44` 【11 词 / 4.0 秒】

🎙 **Every trace_id shows the Intent, Cart and Payment layers**

`2:44–2:47` 【9 词 / 3.3 秒】

🎙 **AUTO_APPROVE, REQUIRE_HUMAN, REJECT.**

`2:47–2:48` 【3 词 / 1.1 秒】

🎙 **Six hundred and fifty decisions**

`2:48–2:50` 【5 词 / 1.8 秒】

🎙 **Three independent implementations. Four wrong-verdict classes — all fixed.**

`2:50–2:53` 【9 词 / 3.3 秒】

🎙 **MandatePay — let AI spend, but every cent earns your authorization. Thanks.**

`2:53–2:58` 【12 词 / 4.4 秒】
