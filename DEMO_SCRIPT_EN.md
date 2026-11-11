# MandatePay · Demo Script (EN) — ≤ 2:45

> **Read this, then record.** The Chinese script is [`DEMO_SCRIPT.md`](DEMO_SCRIPT.md);
> this file is the same demo in English, on a **compressed timeline**.
>
> - **This file owns the timecodes.** The Chinese script has been re-timed to the same
>   2:40 timeline, so every segment below lines up with its Chinese counterpart.
>   The Chinese narration is longer — it is reference text, not the recorded track:
>   **record the English** (`DEMO_SCRIPT_EN.srt` is the subtitle file to load).
> - **Shot ①** (PayPal Developer Dashboard) lives inside scene 4 — see the click path
>   in the segment notes; you can follow it without guessing.
> - **SRT subtitles** live in [`DEMO_SCRIPT_EN.srt`](DEMO_SCRIPT_EN.srt) — every cue is
>   ≤ 2 lines × 42 characters.
>
> **Total runtime: 159.1s** (must stay under 165s / 2:45).
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

### 0:00–0:18 · Opening: frame the problem

<!-- 中文稿对应：0:00–0:18 · 开场：把问题摆出来 -->

- Home page on screen, then cut to /constraints.
- This segment is narration only — no clicking.

🎙 **You ask an AI to subscribe you to software.**

`0:00–0:02` 【9 词 / 2.8 秒】

🎙 **It gets “under 200 a month.”**

`0:02–0:04` 【6 词 / 1.9 秒】

🎙 **But will it hit pay when you’re not watching?**

`0:04–0:07` 【9 词 / 2.8 秒】

🎙 **Most agents never spend — or spend with no explanation.**

`0:07–0:10` 【10 词 / 3.2 秒】

🎙 **MandatePay: the AI can judge on its own —**

`0:10–0:13` 【9 词 / 2.8 秒】

🎙 **but every judgment is explainable, and every payment gets a human nod.**

`0:13–0:17` 【12 词 / 3.8 秒】


### 0:18–0:49 · Scene 1: one sentence becomes a rule set

<!-- 中文稿对应：0:18–0:49 · 场景一：一句话设定约束 -->

- Type on screen (spend ~8s):
-   Monthly SaaS budget 200 dollars, over 50 ask me first, only Notion and Figma.
- Then click “Parse & set constraints”.
- Structured rule card appears (budget / limits / both allowlists).

🎙 **First, authorize it. One sentence:**

`0:18–0:20` 【5 词 / 2.6 秒】

🎙 **And this isn’t keyword matching. It’s function calling**

`0:28–0:32` 【8 词 / 4.1 秒】

🎙 **The model picks the tool and the arguments.**

`0:32–0:37` 【8 词 / 4.1 秒】

🎙 **It pulled out a monthly budget**

`0:37–0:40` 【6 词 / 3.1 秒】

🎙 **A per-transaction limit, a vendor list, and a category list.**

`0:40–0:45` 【10 词 / 5.2 秒】

🎙 **That’s an executable Intent Mandate.**

`0:45–0:47` 【5 词 / 2.6 秒】


### 0:49–1:27 · Scene 2: reasoning, not classification

<!-- 中文稿对应：0:49–1:27 · 场景二：⭐ 异常推理 -->

- Type on screen (spend ~5s):
-   Notion and Figma — buy the cheaper one.
- Tool trace on screen: query_catalog(Notion) → query_catalog(Figma) → create_purchase_request.

🎙 **Now give it something a human would actually say:**

`0:49–0:53` 【9 词 / 4.1 秒】

🎙 **A rule parser dies here. It can pull out the numbers**

`0:58–1:03` 【11 词 / 5.0 秒】

🎙 **But “the cheaper one” needs both prices first.**

`1:03–1:07` 【8 词 / 3.7 秒】

🎙 **Look at the tool trace**

`1:07–1:09` 【5 词 / 2.3 秒】

🎙 **It queried Notion, then Figma, and only then created the purchase request.**

`1:09–1:14` 【12 词 / 5.5 秒】

🎙 **It found Notion at ten a month, Figma at fifteen — and decided.**

`1:14–1:20` 【13 词 / 6.0 秒】

🎙 **That’s reasoning, not classification. Query, compare, decide — on its own.**

`1:20–1:25` 【11 词 / 5.0 秒】


### 1:27–1:59 · Scene 3: a rejection that explains itself

<!-- 中文稿对应：1:27–1:59 · 场景三：⭐ 被拒时解释 + 建议 -->

- Type on screen (spend ~4s):
-   Buy Slack Pro, 15 dollars.
- Then click “Let the agent handle it”.
- Insight box, then explanation box.

🎙 **Now it hits the allowlist. The verdict is REJECT. And notice**

`1:27–1:31` 【11 词 / 4.6 秒】

🎙 **It doesn’t just say “denied”.**

`1:31–1:33` 【5 词 / 2.1 秒】

🎙 **First, the insight: here’s where your wallet stands this month.**

`1:37–1:41` 【10 词 / 4.2 秒】

🎙 **Second, the explanation: not a cold no — which rule blocked it, and how to fix it.**

`1:41–1:48` 【17 词 / 7.1 秒】

🎙 **That fix isn’t a template — the model generated it**

`1:48–1:52` 【10 词 / 4.2 秒】

🎙 **Add Slack to the allowlist and it can charge the company card.**

`1:52–1:57` 【12 词 / 5.0 秒】


### 1:59–2:27 · Scene 4: a human has to nod  (includes Shot ①)

<!-- 中文稿对应：1:59–2:27 · 场景四：人点头才扣款（含镜头①） -->

- Open a PENDING_HUMAN request in /approvals — the popup shows all three layers.
- Click “① Pay on PayPal” (opens the sandbox in a NEW tab) → click “Complete purchase”
- → the browser auto-returns to our `/paypal/return` page, which captures the order
  itself and shows green `Payment COMPLETED` + the real `capture_id`.
  (Do NOT click “② I've paid · capture” — the order is already captured; a second
  capture returns HTTP 400.)
- ★ SHOT ① (PayPal Developer Dashboard — the order id must be on screen):
-   1. Open a NEW tab on PayPal Developer Dashboard → Sandbox
-   2. Tab: Activity (or Transactions)
-   3. Find the row by ORDER ID (not the capture id) and click it
-   4. Detail page — HOLD on status = COMPLETED
-   5. On-screen label (added in post): order id in large type, TOP-LEFT corner
-      e.g. ORDER 6S3278047H2709334 — white text on a dark translucent bar
-   6. HOLD 2 seconds on COMPLETED (workshop requires a 2s hold here)

🎙 **But if it decides this is worth buying, it still can’t charge you.**

`1:59–2:04` 【13 词 / 5.9 秒】

🎙 **Over the threshold, so it stops — the status is PAYER_ACTION_REQUIRED.**

`2:04–2:09` 【11 词 / 5.0 秒】

🎙 **Every request carries a three-layer Mandate**

`2:09–2:12` 【6 词 / 2.7 秒】

🎙 **Intent, Cart, Payment.**

`2:12–2:13` 【3 词 / 1.4 秒】

🎙 **Human authorizes, machine executes.**

`2:19–2:20` 【4 词 / 1.8 秒】

🎙 **And here it is in PayPal — order <ORDER_ID>, status COMPLETED.**

`2:20–2:25` 【11 词 / 5.0 秒】


### 2:27–2:40 · Closing: audit trail and numbers

<!-- 中文稿对应：2:27–2:40 · 收尾：数据与审计 -->

- Cut quickly through /audit (three-layer Mandate list) → /budget (dashboard).
- Cut back to the home page at the end.

🎙 **Everything is auditable — used, remaining, and month-end projection, all computed.**

`2:27–2:30` 【11 词 / 3.8 秒】

🎙 **Every trace_id shows the Intent, Cart and Payment layers**

`2:30–2:33` 【9 词 / 3.1 秒】

🎙 **AUTO_APPROVE, REQUIRE_HUMAN, REJECT.**

`2:33–2:34` 【3 词 / 1.0 秒】

🎙 **MandatePay — let AI spend, but every cent earns your authorization. Thanks.**

`2:34–2:39` 【12 词 / 4.1 秒】
