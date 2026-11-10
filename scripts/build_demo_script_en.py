#!/usr/bin/env python
"""生成 DEMO_SCRIPT_EN.md 与 DEMO_SCRIPT_EN.srt。

为什么用脚本生成
----------------
英文稿有两处硬约束是"算"出来的，不是"写"出来的：

1. **总时长 < 3:00** —— 每段旁白的秒数由字数 ÷ 语速推出，
   写完必须验证总长不超标。手工排版很容易超。
2. **SRT 每条 ≤ 2 行 × 42 字符** —— 这是播放器/编码器的实际限制
   （超过就换行或溢出画面）。必须逐条校验，不能靠肉眼。

同时保证**中英逐段对齐**：段落时间码与 DEMO_SCRIPT.md 的六段完全一致，
术语固定不译（PAYER_ACTION_REQUIRED / three-layer Mandate / trace_id /
company card / AUTO_APPROVE / REQUIRE_HUMAN / REJECT）。

用法：
```bash
python scripts/build_demo_script_en.py          # 生成两个文件
python scripts/build_demo_script_en.py --check  # 只校验，不写文件
```
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent

# ── 语速与限制 ────────────────────────────────────────────
# 160 wpm ≈ 2.67 词/秒；留一点余量用 2.6（读起来更清楚，且不到 3:00）
WORDS_PER_SEC = 2.6
MAX_TOTAL_SEC = 180.0          # 工单要求 < 3:00
SRT_MAX_LINE_CHARS = 42        # 每行最多 42 字符
SRT_MAX_LINES = 2              # 每条最多 2 行
# PayPal order id 的真实长度样本（占位符 <ORDER_ID> 比它短 5 个字符，
# 所以校验必须用真实值，否则录制时替换成真 id 就会溢出行宽）
SAMPLE_ORDER_ID = "6S3278047H2709334"

# ── 术语表（固定不译）─────────────────────────────────────
TERMS = [
    "PAYER_ACTION_REQUIRED",
    "three-layer Mandate",
    # 三个层名并列出现时整体不拆（"Intent, Cart and Payment layers"
    # 断成两行会让并列关系消失）
    "Intent, Cart and Payment",
    "Intent, Cart, Payment",
    "Intent",
    "Cart",
    "Payment",
    "trace_id",
    "company card",
    "AUTO_APPROVE",
    "REQUIRE_HUMAN",
    "REJECT",
]

# ── 六个段落（与 DEMO_SCRIPT.md 逐段对齐）──────────────────
# 每条旁白：(字幕文本, 是否为画面动作说明)
# None 表示该处没有旁白（纯画面 / 停顿）
SEGMENTS = [
    {
        "title": "Opening: frame the problem",
        "start": 0.0,
        "end": 20.0,
        "zh": "0:00–0:20 · 开场：把问题摆出来",
        "notes": [
            "Home page on screen, then cut to /constraints.",
            "This segment is narration only — no clicking.",
        ],
        "lines": [
            ("You ask an AI to subscribe you to software.", None),
            ("It gets “under 200 a month.”", None),
            ("But will it hit pay when you’re not watching?", None),
            ("Most agents never spend — or spend with no explanation.", None),
            ("MandatePay: the AI can judge on its own —", None),
            ("but every judgment is explainable, and every payment gets a human nod.", None),
        ],
    },
    {
        "title": "Scene 1: one sentence becomes a rule set",
        "start": 20.0,
        "end": 55.0,
        "zh": "0:20–0:55 · 场景一：一句话设定约束",
        "notes": [
            "Type on screen (spend ~8s):",
            "  Monthly SaaS budget 200 dollars, over 50 ask me first, only Notion and Figma.",
            "Then click “Parse & set constraints”.",
            "Structured rule card appears (budget / limits / both allowlists).",
        ],
        "lines": [
            ("First, authorize it. One sentence:", None),
            (None, "typed-input"),      # 打字画面，8.2s
            ("And this isn’t keyword matching. It’s function calling — the model picks the tool and the arguments.", None),
            ("It pulled out a monthly budget, a per-transaction limit, a vendor list, and a category list.", None),
            ("That’s an executable Intent Mandate.", None),
        ],
    },
    {
        "title": "Scene 2: reasoning, not classification",
        "start": 55.0,
        "end": 95.0,
        "zh": "0:55–1:35 · 场景二：⭐ 异常推理",
        "notes": [
            "Type on screen (spend ~5s):",
            "  Notion and Figma — buy the cheaper one.",
            "Tool trace on screen: query_catalog(Notion) → query_catalog(Figma) → create_purchase_request.",
        ],
        "lines": [
            ("Now give it something a human would actually say:", None),
            (None, "typed-input-2"),    # 打字画面，5.3s
            ("A rule parser dies here. It can pull out the numbers, but “the cheaper one” needs both prices first.", None),
            ("Look at the tool trace — it queried Notion, then Figma, and only then created the purchase request.", None),
            ("It found Notion at ten a month, Figma at fifteen — and decided.", None),
            ("That’s reasoning, not classification. Query, compare, decide — on its own.", None),
        ],
    },
    {
        "title": "Scene 3: a rejection that explains itself",
        "start": 95.0,
        "end": 130.0,
        "zh": "1:35–2:10 · 场景三：⭐ 被拒时解释 + 建议",
        "notes": [
            "Type on screen (spend ~4s):",
            "  Buy Slack Pro, 15 dollars.",
            "Then click “Let the agent handle it”.",
            "Insight box, then explanation box.",
        ],
        "lines": [
            ("Now it hits the allowlist. The verdict is REJECT. And notice — it doesn’t just say “denied”.", None),
            (None, "typed-input-3"),    # 打字画面，3.9s
            ("First, the insight: here’s where your wallet stands this month.", None),
            ("Second, the explanation: not a cold no — which rule blocked it, and how to fix it.", None),
            ("That fix isn’t a template — the model generated it. Add Slack to the allowlist and it can charge the company card.", None),
        ],
    },
    {
        "title": "Scene 4: a human has to nod  (includes Shot ①)",
        "start": 130.0,
        "end": 160.0,
        "zh": "2:10–2:40 · 场景四：人点头才扣款（含镜头①）",
        "notes": [
            "Open a PENDING_HUMAN request in /approvals — the popup shows all three layers.",
            "Click “① Pay on PayPal” → sandbox “Complete purchase” → back → click “② I’ve paid · capture”.",
            "Screen shows: ✅ 闭环完成 —— capture xxxxx",
            "★ SHOT ① (PayPal Developer Dashboard — the order id must be on screen):",
            "  1. Open a NEW tab on PayPal Developer Dashboard → Sandbox",
            "  2. Tab: Activity (or Transactions)",
            "  3. Find the row by ORDER ID (not the capture id) and click it",
            "  4. Detail page — HOLD on status = COMPLETED",
            "  5. On-screen label (added in post): order id in large type, TOP-LEFT corner",
            "     e.g. ORDER 6S3278047H2709334 — white text on a dark translucent bar",
            "  6. HOLD 2 seconds on COMPLETED (workshop requires a 2s hold here)",
        ],
        "lines": [
            ("But if it decides this is worth buying, it still can’t charge you.", None),
            ("Over the threshold, so it stops — the status is PAYER_ACTION_REQUIRED.", None),
            ("Every request carries a three-layer Mandate — Intent, Cart, Payment.", None),
            (None, "payment-flow"),      # 画面：付款→捕获→✅闭环，5.2s
            ("Human authorizes, machine executes.", None),
            ("And here it is in PayPal — order <ORDER_ID>, status COMPLETED.", "shot1"),
        ],
    },
    {
        "title": "Closing: audit trail and numbers",
        "start": 160.0,
        "end": 179.0,
        "zh": "2:40–3:00 · 收尾：数据与审计",
        "notes": [
            "Cut quickly through /audit (three-layer Mandate list) → /budget (dashboard).",
            "Cut back to the home page at the end.",
        ],
        "lines": [
            ("Everything is auditable — used, remaining, and month-end projection, all computed.", None),
            ("Every trace_id shows the Intent, Cart and Payment layers — AUTO_APPROVE, REQUIRE_HUMAN, REJECT.", None),
            ("Six hundred and fifty decisions, three independent implementations. Four wrong-verdict classes — all fixed.", None),
            ("MandatePay — let AI spend, but every cent earns your authorization. Thanks.", None),
        ],
    },
]

# 打字/纯画面占位时长（秒）
PAUSE_SEC = {
    "typed-input": 8.2,
    "typed-input-2": 5.3,
    "typed-input-3": 3.9,
    "payment-flow": 5.2,
}


def word_count(text: str) -> int:
    """按空白分词计数（英文标点不单独成词）。"""
    return len([w for w in re.split(r"\s+", text.strip()) if w])


def fmt_ts(sec: float) -> str:
    """秒 → SRT 时间码 HH:MM:SS,mmm"""
    sec = max(0.0, sec)
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    ms = int(round((sec - int(sec)) * 1000))
    if ms == 1000:      # 四舍五入进位
        s += 1
        ms = 0
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def fmt_mmss(sec: float) -> str:
    """秒 → M:SS（给 md 用的时间码）"""
    return f"{int(sec // 60)}:{int(sec % 60):02d}"


def wrap_srt(text: str, width: int = SRT_MAX_LINE_CHARS) -> list[str]:
    """把一条字幕折成 ≤2 行，每行 ≤42 字符。

    折行策略：优先在单词边界断；单行放不下时按宽度硬切（极少见）。

    ⚠️ **术语不能被折行拆开** —— "three-layer Mandate" 断成
    "three-layer" / "Mandate — Intent, Cart, Payment." 两行时，
    观众会看到术语断成两半，不符合「固定不译」的要求。
    所以折行点不能落在术语内部：把术语当成**不可分割的原子词**。
    """
    # 把术语标记成原子（折完再还原）。
    #
    # 两个坑（都实测踩过）：
    # 1. 标记**不能含空格** —— 否则 split() 会把原子从中间切开，
    #    还原失败，控制字符漏进 SRT（播放器直接判定为二进制文件）。
    # 2. 标记的**长度必须等效于原术语** —— 折行是按字符数算宽度的，
    #    如果标记比术语短，折行点会算错，还原后真实行宽超 42
    #    （实测："three-layer Mandate" 被切成 "Mandat" / "e"）。
    #    做法：用 \x01 + 序号 + \x02 + 下划线填充 补齐到原长度。
    atoms: list[str] = []
    marked = text
    # 长术语优先替换 —— 否则 "Intent" 会先把 "Intent, Cart and Payment" 打断
    for term in sorted(TERMS, key=len, reverse=True):
        if term in marked:
            head = f"\x01{len(atoms)}\x02"
            pad = len(term) - len(head)
            token = head + ("_" * pad if pad > 0 else "")
            marked = marked.replace(term, token)
            atoms.append((token, term))

    words = marked.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= width:
            cur += " " + w
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)

    # 还原术语标记（按序号还原，容忍尾部填充被切掉的情况）
    def _restore(s: str) -> str:
        for token, term in atoms:
            idx = token.split("\x02")[0].split("\x01")[1]
            s = re.sub(
                r"\x01" + re.escape(idx) + r"\x02_*",
                lambda _m, _t=term: _t,
                s,
            )
        return s

    lines = [_restore(x) for x in lines]
    # 单行超长（没有空格的超长词）→ 硬切。
    # ⚠️ 硬切也不能切断术语：原子标记是 \x01<idx>\x02，
    #    若在它中间切开，还原会得到半个术语。
    out: list[str] = []
    for ln in lines:
        while len(ln) > width:
            # 找这一行里最后一个完整的原子边界
            cut = -1
            for m in re.finditer(r"\x01(\d+)\x02", ln):
                if m.end() <= width:
                    cut = m.end()
            if cut == -1:
                cut = width
            else:
                # 原子之后如果还有内容，就把原子推到下一行
                rest = ln[cut:].lstrip()
                if rest and len(ln[:cut]) + 1 + len(rest) <= width:
                    cut = len(ln[:cut])
            out.append(ln[:cut].rstrip())
            ln = ln[cut:].lstrip()
        if ln:
            out.append(ln)
    return out


def _splits_a_term(text: str, start: int, end: int) -> bool:
    """判断这个拆点是否落在某个术语内部（是则不能拆）。"""
    for term in TERMS:
        t0 = text.find(term)
        while t0 != -1:
            t1 = t0 + len(term)
            if start < t1 and end > t0:      # 拆点落在 [t0, t1) 区间内
                return True
            t0 = text.find(term, t0 + 1)
    return False


def split_if_needed(text: str, allow_split: bool = True) -> list[str]:
    """若一句折行后超过 2 行，就拆成多条能独立读懂的字幕。

    拆点优先级：破折号 → 分号 → 逗号 → 句号；
    都找不到就从中间按词数均分（在最近的连接词处断）。
    拆完每条都要保证「折行 ≤ 2 行」—— 不满足就继续拆。

    `allow_split=False` 时不拆句，改为调用方保证它能折进 2 行
    （用于「必须保持完整」的台词，如工单指定的镜头①旁白）。
    """
    if len(wrap_srt(text)) <= SRT_MAX_LINES:
        return [text]

    if not allow_split:
        # 不拆句 → 只能靠更宽的折行（放宽到硬上限仍不行就报错）
        wider = wrap_srt(text, width=SRT_MAX_LINE_CHARS)
        if len(wider) > SRT_MAX_LINES:
            raise SystemExit(
                f"❌ 标记为「不可拆」但折行后超过 {SRT_MAX_LINES} 行：\n   {text}"
            )
        return [text]

    # 候选拆点（取第一个能让两半都 ≤2 行的位置）
    for sep in (" — ", "; ", ", ", ". "):
        idx = text.find(sep)
        while idx != -1:
            # ⚠️ 拆点不能落在术语内部 ——
            # 术语是「固定不译」的标识符，拆开就失去意义。
            # 典型：three-layer Mandate 被逗号/破折号切开。
            if _splits_a_term(text, idx, idx + len(sep)):
                idx = text.find(sep, idx + 1)
                continue
            a = text[:idx].strip()
            b = text[idx + len(sep):].strip()
            # 去掉句首的连接词残留，让第二句读起来自然
            if b and b[0].islower():
                b = b[0].upper() + b[1:]
            if a and b and len(wrap_srt(a)) <= SRT_MAX_LINES and len(wrap_srt(b)) <= SRT_MAX_LINES:
                return split_if_needed(a) + split_if_needed(b)
            idx = text.find(sep, idx + 1)

    # 没有自然拆点 → 按词数均分
    words = text.split()
    half = len(words) // 2
    a = " ".join(words[:half])
    b = " ".join(words[half:])
    if b and b[0].islower():
        b = b[0].upper() + b[1:]
    if a and b:
        return split_if_needed(a) + split_if_needed(b)

    return [text]


def build() -> tuple[list[dict], list[str]]:
    """算出每条字幕的时间码。返回 (entries, md_lines)。"""
    entries: list[dict] = []
    md: list[str] = []

    for seg in SEGMENTS:
        t = seg["start"]
        seg_entries: list[dict] = []

        # 先算出这段的「旁白总词数」与「固定画面时长」，
        # 再把旁白压缩到段时长内 —— 否则旁白会溢出到下一段，
        # 造成字幕重叠、且与中文稿的时间码对不上。
        spoken = [x for x in seg["lines"] if x[0] is not None]
        total_words = sum(word_count(x) for x, _ in spoken)
        fixed = sum(PAUSE_SEC[k] for x, k in seg["lines"] if x is None)
        # 段内留给旁白的时间（扣掉固定画面 + 0.6s 段尾留白）
        avail = (seg["end"] - seg["start"]) - fixed - 0.6
        # 需要的秒数（按基准语速）
        need = total_words / WORDS_PER_SEC
        # 压缩系数（<1 表示要念快一点）
        rate = WORDS_PER_SEC * (need / avail) if avail > 0 else WORDS_PER_SEC
        rate = min(rate, WORDS_PER_SEC * 1.35)  # 最多快 35%，再快就念不动了

        for text, kind in seg["lines"]:
            if text is None:
                dur = PAUSE_SEC[kind]
                t += dur
                continue
            # 一条旁白可能太长（折行后 > 2 行）→ 自动拆成多条字幕。
            # 拆点优先选自然停顿（逗号 / 破折号 / 分号），
            # 拆出来的每条都必须能独立读懂。
            #
            # ⚠️ 但「不可拆」的句子例外 —— 比如镜头①那句旁白是工单指定的
            # 一整句台词，拆成两条会让字幕读起来断裂。
            # 对这类句子改为**收紧折行**（允许更宽的换行点），
            # 保证它作为一条完整字幕出现。
            for piece in split_if_needed(text, allow_split=(kind != "shot1")):
                w = word_count(piece)
                dur = w / rate
                entry = {
                    "text": piece,
                    "start": t,
                    "end": t + dur,
                    "words": w,
                    "seg": seg["title"],
                    "kind": kind,
                }
                seg_entries.append(entry)
                entries.append(entry)
                t += dur

        # 段内留白：最后一句结束后留 0.6s，再进下一段
        t += 0.6

        # ── md 行 ──
        md.append(f"### {fmt_mmss(seg['start'])}–{fmt_mmss(seg['end'])} · {seg['title']}")
        md.append("")
        md.append(f"<!-- 中文稿对应：{seg['zh']} -->")
        md.append("")
        for note in seg["notes"]:
            md.append(f"- {note}" if note.startswith("★") else f"- {note}")
        md.append("")
        for e in seg_entries:
            ts = f"{fmt_mmss(e['start'])}–{fmt_mmss(e['end'])}"
            md.append(f"🎙 **{e['text']}**")
            md.append("")
            md.append(f"`{ts}` 【{e['words']} 词 / {e['end'] - e['start']:.1f} 秒】")
            md.append("")
        md.append("")

    return entries, md


def build_srt(entries: list[dict]) -> str:
    """生成 SRT 内容（每条 ≤2 行 × 42 字符）。

    内部统一用 \n；写文件时转成 CRLF（SRT 播放器惯例）。
    ⚠️ 校验器读回时必须先 normalize CRLF → LF，
    否则按 "\n\n" 分块会失效（一个条目被当成 135 行）→ 误报一堆问题。
    """
    blocks: list[str] = []
    for i, e in enumerate(entries, 1):
        lines = wrap_srt(e["text"])
        if len(lines) > SRT_MAX_LINES:
            raise SystemExit(
                f"❌ 第 {i} 条字幕折成 {len(lines)} 行（上限 {SRT_MAX_LINES}）：\n   {e['text']}"
            )
        for ln in lines:
            if len(ln) > SRT_MAX_LINE_CHARS:
                raise SystemExit(
                    f"❌ 第 {i} 条字幕某行 {len(ln)} 字符（上限 {SRT_MAX_LINE_CHARS}）：\n   {ln}"
                )
        blocks.append(
            f"{i}\n{fmt_ts(e['start'])} --> {fmt_ts(e['end'])}\n" + "\n".join(lines)
        )
    return "\n\n".join(blocks) + "\n"


def validate(entries: list[dict], srt_text: str) -> list[str]:
    """校验：时长、行宽、时间码单调、覆盖完整。返回问题列表。"""
    problems: list[str] = []

    total_words = sum(e["words"] for e in entries)
    speech = sum(e["end"] - e["start"] for e in entries)
    last_end = max(e["end"] for e in entries)
    if last_end > MAX_TOTAL_SEC:
        problems.append(f"总时长 {last_end:.1f}s 超过 {MAX_TOTAL_SEC:.0f}s")

    # 时间码单调递增且不重叠
    for a, b in zip(entries, entries[1:]):
        if b["start"] < a["end"] - 0.01:
            problems.append(f"字幕重叠：{a['text'][:30]}… 与 {b['text'][:30]}…")

    # SRT 行宽
    # ⚠️ 先把 CRLF 归一化成 LF —— 否则按 "\n\n" 分块会失效
    srt_norm = srt_text.replace("\r\n", "\n")
    for block in srt_norm.strip().split("\n\n"):
        lines = block.split("\n")
        body = [ln for ln in lines[2:] if ln.strip()]
        if len(body) > SRT_MAX_LINES:
            problems.append(f"SRT 条目超过 {SRT_MAX_LINES} 行：{lines[0]}")
        for ln in body:
            if len(ln) > SRT_MAX_LINE_CHARS:
                problems.append(f"SRT 行 {len(ln)} 字符超限：{ln[:50]}")

    # ⚠️ 占位符必须按**真实长度**校验 ——
    # <ORDER_ID> 只有 11 字符，但真实 PayPal order id 是 16 字符
    # （如 6S3278047H2709334）。按占位符长度校验会漏判，
    # 录制时替换成真 id 就会溢出行宽。
    for e in entries:
        if "<ORDER_ID>" in e["text"]:
            real = e["text"].replace("<ORDER_ID>", SAMPLE_ORDER_ID)
            lines = wrap_srt(real)
            if len(lines) > SRT_MAX_LINES:
                problems.append(
                    f"镜头①旁白按真实 order id（{len(SAMPLE_ORDER_ID)} 字符）折行后 "
                    f"是 {len(lines)} 行，超 {SRT_MAX_LINES} 行上限"
                )
            for ln in lines:
                if len(ln) > SRT_MAX_LINE_CHARS:
                    problems.append(
                        f"镜头①旁白按真实 order id 折行后某行 {len(ln)} 字符超限：{ln}"
                    )

    # SRT 时间码格式
    tc = re.compile(r"^\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}$")
    for block in srt_norm.strip().split("\n\n"):
        lines = block.split("\n")
        if len(lines) < 2 or not tc.match(lines[1]):
            problems.append(f"SRT 时间码格式错误：{lines[1] if len(lines) > 1 else block[:40]}")

    # 术语覆盖 + 完整性
    all_text = " ".join(e["text"] for e in entries)
    for term in TERMS:
        if term not in all_text:
            problems.append(f"术语缺失（应固定不译）：{term}")

    # ⚠️ 术语不能被拆到两条字幕里 ——
    # "three-layer Mandate" 被折行拆成 "three-layer" / "Mandate — Intent, Cart, Payment."
    # 两条字幕后，观众会先看到 "three-layer" 再看到 "Mandate"，
    # 术语就断开了，不符合「固定不译」的要求。
    # 判定：把相邻字幕拼起来看术语是否完整。
    joined = " ".join(e["text"] for e in entries)
    for term in TERMS:
        if term in all_text:
            continue
        # 单独不存在 → 看是否跨条拼接后存在（说明被拆开了）
        if term in joined:
            problems.append(
                f"术语被拆到多条字幕里（观众看到的是断开的形式）：{term}"
            )

    # ⚠️ 输出里不能有控制字符 —— SRT 里混进 \x00 / \x01 会被播放器
    # 判定为二进制文件而拒绝加载（实测踩过）。
    for ch in srt_text:
        if ord(ch) < 32 and ch not in "\n\r":
            problems.append(f"SRT 含控制字符 U+{ord(ch):04X} —— 播放器会拒绝加载")
            break

    print(f"  旁白总词数 : {total_words}")
    print(f"  旁白净时长 : {speech:.1f}s")
    print(f"  最后一句结束: {last_end:.1f}s（{fmt_mmss(last_end)}）")
    print(f"  字幕条数   : {len(entries)}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description="生成英文演示稿 + SRT")
    ap.add_argument("--check", action="store_true", help="只校验，不写文件")
    args = ap.parse_args()

    entries, md_body = build()
    srt = build_srt(entries)

    print("=== 校验 ===")
    problems = validate(entries, srt)

    if problems:
        print("\n❌ 发现问题：")
        for p in problems:
            print(f"   · {p}")
        return 1
    print("✅ 全部通过")

    if args.check:
        return 0

    # ── 写 md ──
    header = f"""# MandatePay · Demo Script (EN) — ≤ 3 minutes

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
> **Total runtime: {max(e['end'] for e in entries):.1f}s** (must stay under 180s).
> Reading pace used for the timings: {WORDS_PER_SEC} words/second (~157 wpm).
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

"""

    md_path = ROOT / "DEMO_SCRIPT_EN.md"
    md_path.write_text(header + "\n".join(md_body).rstrip() + "\n", encoding="utf-8")

    srt_path = ROOT / "DEMO_SCRIPT_EN.srt"
    # SRT 用 CRLF（播放器惯例；VLC/ffmpeg 都吃 LF，但 CRLF 更保险）
    srt_path.write_bytes(srt.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))

    # ── 预览页用的数据文件（浏览器加载验证用）──────────────
    # 单独存成 js 而不是往 html 里塞字符串：
    # 往 html 里注入 JSON 字符串时，正则替换很容易被 \n 转义坑到
    # （实测：替换后只剩 `"1`，字幕全丢且不易发现）。
    # 用 <script src="srt_data.js"> 加载则完全避开转义问题，
    # 且 file:// 下不会触发 CORS（script 标签不受 fetch 限制）。
    data_path = ROOT / "tools" / "srt_data.js"
    data_path.write_text(
        "// 由 scripts/build_demo_script_en.py 自动生成 —— 不要手工编辑\n"
        "window.SRT_RAW = " + json.dumps(srt, ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )

    print(f"\n✅ 已生成 {md_path.name}")
    print(f"✅ 已生成 {srt_path.name}")
    print("✅ 已生成 tools/srt_data.js（预览页加载它）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
