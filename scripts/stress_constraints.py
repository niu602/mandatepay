"""D12 约束引擎压测 + 失败 case 分类。

方法论：压测若拿引擎自己当标准答案，等于没测。
所以这里维护三份**独立参照实现（oracle）**：
  1. strict  —— 与当前引擎规格逐条对齐 → 检验「代码有没有写错」
  2. legacy  —— 还原**修复前**的判定口径 → 量化「用户此前实际遭遇什么」
  3. product —— 按**用户直觉**推导 → 暴露「用户会撞上的误拒/漏放」

规模：50 条随机约束 × 13 条请求/约束 = 650 次判定，
其中 550 条为对抗样本（金额恰在阈值/预算、大小写、带空格、缺品类、浮点尾数等），
这些边界正是真实事故高发区。

产出：
- 终端报告（修复前/后三层口径 + 失败分类）
- STRESS_REPORT.md（可直接作为 Potential Impact 的数据支撑）

运行：python scripts/stress_constraints.py [--seed 42]
"""
from __future__ import annotations

import argparse
import os
import random
import sys
from collections import Counter
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from agent.constraints import SpendContext, evaluate  # noqa: E402
from agent.models import Constraint, Decision, PurchaseRequest  # noqa: E402

VENDORS = ["Notion", "Figma", "Slack", "GitHub", "Zoom", "Linear", "Dropbox"]
CATEGORIES = ["SaaS", "Design", "Hardware", "Infra", "Productivity"]


def _norm(s: str) -> str:
    """名称归一化：去首尾空格 + 转小写（引擎口径）。"""
    return (s or "").strip().lower()


def _in_list(value: str, whitelist: list[str]) -> bool:
    return _norm(value) in {_norm(w) for w in whitelist}


def _differs_only_by_case(value: str, whitelist: list[str]) -> bool:
    """该值与白名单里某项**仅在大小写/空格上不同**（修复前的精确匹配会因此误拒）。"""
    v = value or ""
    for w in whitelist:
        if w != v and _norm(w) == _norm(v):
            return True
    return False


# ── 三份独立参照实现 ──────────────────────────────────────
def oracle(
    request: PurchaseRequest,
    c: Constraint,
    spend: SpendContext,
    mode: str = "strict",
) -> Decision:
    """按约束语义直接推导应有结论（独立于 agent.constraints.evaluate）。

    - "legacy"  修复前语义：精确匹配、金额不取整、品类未知直接跳过
    - "strict"  当前引擎规格：名称归一化、金额按分取整、品类未知→需人工确认
    - "product" 用户直觉：与 strict 相同，但「品类未知」判为直接拒绝
                （既然限定了品类，没说品类的东西就不该放行）
    """
    product = mode == "product"
    legacy = mode == "legacy"

    if legacy:
        amt = request.amount_usd
        v_hit = request.vendor in c.vendor_whitelist
        c_hit = request.category in c.category_whitelist
    else:
        amt = round(request.amount_usd, 2)
        v_hit = _in_list(request.vendor, c.vendor_whitelist)
        c_hit = _in_list(request.category, c.category_whitelist)

    # 1 供应商白名单（硬）
    if c.vendor_whitelist and not v_hit:
        return Decision.REJECT

    # 2 品类白名单（硬）；品类未知留到最后
    if c.category_whitelist and request.category and not c_hit:
        return Decision.REJECT

    # 3 月度滚动预算（硬）
    if spend.monthly_usd + amt > c.monthly_budget_usd:
        return Decision.REJECT

    # 4 单笔阈值（软）
    if amt > c.single_tx_threshold_usd:
        return Decision.REQUIRE_HUMAN

    # 5 日累计阈值（软）
    if c.daily_threshold_usd > 0 and spend.daily_usd + amt > c.daily_threshold_usd:
        return Decision.REQUIRE_HUMAN

    # 6 品类未知 + 约束限定了品类
    if c.category_whitelist and not request.category:
        if legacy:
            return Decision.AUTO_APPROVE   # 修复前：静默跳过品类校验
        return Decision.REJECT if product else Decision.REQUIRE_HUMAN

    return Decision.AUTO_APPROVE


# ── 用例生成 ──────────────────────────────────────────────
@dataclass
class Case:
    constraint: Constraint
    request: PurchaseRequest
    spend: SpendContext
    kind: str


def make_constraints(n: int, rng: random.Random) -> list[Constraint]:
    out = []
    for _ in range(n):
        budget = rng.choice([50, 100, 200, 500, 1000])
        threshold = min(rng.choice([10, 25, 50, 100, 200]), budget)
        daily = rng.choice([0, 0, 50, 100, 300])
        out.append(
            Constraint(
                monthly_budget_usd=float(budget),
                single_tx_threshold_usd=float(threshold),
                daily_threshold_usd=float(daily),
                vendor_whitelist=rng.sample(VENDORS, rng.randint(0, 4)),
                category_whitelist=rng.sample(CATEGORIES, rng.randint(0, 3)),
            )
        )
    return out


ADVERSARIAL_KINDS = (
    "at_threshold", "over_threshold", "at_budget", "over_budget",
    "daily_at", "daily_over", "no_category", "vendor_case",
    "vendor_space", "category_case", "float_boundary",
)


def make_cases(constraints: list[Constraint], rng: random.Random) -> list[Case]:
    cases: list[Case] = []
    for c in constraints:
        # 每条约束配 2 条常规请求 → 50×2=100
        for _ in range(2):
            cases.append(
                Case(
                    constraint=c,
                    request=PurchaseRequest(
                        vendor=rng.choice(VENDORS),
                        item=rng.choice(["Pro", "Team", "Basic"]),
                        amount_usd=round(rng.uniform(1, 300), 2),
                        category=rng.choice(CATEGORIES + [""]),
                    ),
                    spend=SpendContext(
                        monthly_usd=round(rng.uniform(0, 400), 2),
                        daily_usd=round(rng.uniform(0, 200), 2),
                    ),
                    kind="normal",
                )
            )
        # 再配 11 条对抗样本
        for kind in ADVERSARIAL_KINDS:
            threshold = c.single_tx_threshold_usd
            budget = c.monthly_budget_usd
            base_vendor = c.vendor_whitelist[0] if c.vendor_whitelist else VENDORS[0]
            base_cat = c.category_whitelist[0] if c.category_whitelist else CATEGORIES[0]

            if kind == "at_threshold":
                req = PurchaseRequest(vendor=base_vendor, item="Edge", amount_usd=threshold, category="")
            elif kind == "over_threshold":
                req = PurchaseRequest(vendor=base_vendor, item="Edge", amount_usd=threshold + 0.01, category="")
            elif kind == "at_budget":
                req = PurchaseRequest(vendor=base_vendor, item="Edge", amount_usd=budget, category="")
            elif kind == "over_budget":
                req = PurchaseRequest(vendor=base_vendor, item="Edge", amount_usd=budget + 0.01, category="")
            elif kind == "daily_at":
                req = PurchaseRequest(vendor=base_vendor, item="Edge", amount_usd=10.0, category="")
            elif kind == "daily_over":
                req = PurchaseRequest(vendor=base_vendor, item="Edge", amount_usd=10.0, category="")
            elif kind == "no_category":
                req = PurchaseRequest(vendor=base_vendor, item="Edge", amount_usd=10.0, category="")
            elif kind == "vendor_case":
                req = PurchaseRequest(vendor=base_vendor.lower(), item="Edge", amount_usd=10.0, category="")
            elif kind == "vendor_space":
                req = PurchaseRequest(vendor=f" {base_vendor} ", item="Edge", amount_usd=10.0, category="")
            elif kind == "category_case":
                req = PurchaseRequest(vendor=base_vendor, item="Edge", amount_usd=10.0, category=base_cat.lower())
            else:  # float_boundary：金额恰好等于阈值，但浮点表示可能略大于阈值
                req = PurchaseRequest(
                    vendor=base_vendor, item="Edge",
                    amount_usd=threshold + 0.1 + 0.2 - 0.3, category="",
                )

            spend = SpendContext(monthly_usd=0.0, daily_usd=0.0)
            if kind == "daily_at":
                spend = SpendContext(daily_usd=c.daily_threshold_usd - 10.0 if c.daily_threshold_usd else 0.0)
            elif kind == "daily_over":
                spend = SpendContext(daily_usd=c.daily_threshold_usd if c.daily_threshold_usd else 0.0)
            cases.append(Case(constraint=c, request=req, spend=spend, kind=kind))
    return cases


# ── 失败分类 ──────────────────────────────────────────────
_CATEGORY_UNKNOWN = "F. 漏放：品类未知却跳过了品类白名单校验"
_VENDOR_CASE = "A. 供应商大小写/空格导致的误拒"
_CATEGORY_CASE = "C. 品类大小写导致的误拒"
_FLOAT_TAIL = "H. 严重度错判（浮点尾数，恰好等于阈值被误判）"
_OTHER_REJECT = "E. 其他误拒（需人工排查）"
_OTHER_ALLOW = "G. 漏放（应拒未拒）"
_SEVERITY = "I. 其他严重度错判"

_CATEGORY_DESC = {
    _VENDOR_CASE: "用户写 `notion`、或复制粘贴带空格 ` Notion `，与白名单里的 `Notion` 不匹配 → **被冤枉地拒绝**。",
    _CATEGORY_CASE: "品类 `saas` 与白名单 `SaaS` 大小写不同 → 被拒。",
    _CATEGORY_UNKNOWN: "约束限定了品类，但请求没带品类字段 → 品类校验被**静默跳过**，约束形同虚设（安全风险）。",
    _FLOAT_TAIL: "金额 0.1+0.2 这类浮点尾数，把「恰好等于阈值」误判成超限 → 严重度错判。",
    _OTHER_REJECT: "以上都不是的误拒，需人工排查。",
    _OTHER_ALLOW: "违反硬上限却未被拒 —— 安全风险最高。",
    _SEVERITY: "其他严重度错判。",
}


def classify(case: Case, got: Decision, want: Decision) -> str:
    """把「实际行为 vs 用户直觉」的差异归因到具体类别。"""
    if got == want:
        return "一致"
    c, r = case.constraint, case.request

    if got == Decision.REJECT and want != Decision.REJECT:
        # 实际拒了、直觉认为该放行 → 误拒
        if c.vendor_whitelist and _differs_only_by_case(r.vendor, c.vendor_whitelist):
            return _VENDOR_CASE
        if c.category_whitelist and r.category and _differs_only_by_case(r.category, c.category_whitelist):
            return _CATEGORY_CASE
        return _OTHER_REJECT

    if got == Decision.AUTO_APPROVE and want == Decision.REJECT:
        # 静默放行了本该拒的 → 真漏放（安全风险最高）
        if c.category_whitelist and not r.category:
            return _CATEGORY_UNKNOWN
        return _OTHER_ALLOW

    if got == Decision.REQUIRE_HUMAN and want == Decision.REJECT:
        # 引擎没放行，而是升级给人确认 —— 是严重度差异，不是静默漏放
        if c.category_whitelist and not r.category:
            return "J. 严重度差异：品类未知 → 引擎判「需人工确认」，直觉判「直接拒」"
        return _SEVERITY

    if abs(r.amount_usd - round(r.amount_usd, 2)) > 1e-9:
        return _FLOAT_TAIL
    return _SEVERITY


def main() -> int:
    ap = argparse.ArgumentParser(description="约束引擎压测")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--constraints", type=int, default=50)
    ap.add_argument("--out", default="STRESS_REPORT.md")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    constraints = make_constraints(args.constraints, rng)
    cases = make_cases(constraints, rng)
    total = len(cases)

    got_dist: Counter[str] = Counter()
    strict_fails: list[tuple[Case, Decision, Decision, str]] = []
    before_fails: list[tuple[Case, Decision, Decision, str]] = []
    after_fails: list[tuple[Case, Decision, Decision, str]] = []

    for case in cases:
        got = evaluate(case.request, case.constraint, spend=case.spend).decision
        got_dist[got.value] += 1

        # 第一层：当前引擎 vs 引擎规格参照 → 实现正确性
        want_s = oracle(case.request, case.constraint, case.spend, "strict")
        if got != want_s:
            strict_fails.append((case, got, want_s, classify(case, got, want_s)))

        # 第二层：修复前语义 vs 用户直觉 → 用户此前实际遭遇什么
        want_p = oracle(case.request, case.constraint, case.spend, "product")
        legacy = oracle(case.request, case.constraint, case.spend, "legacy")
        if legacy != want_p:
            before_fails.append((case, legacy, want_p, classify(case, legacy, want_p)))

        # 第三层：当前引擎 vs 用户直觉 → 修复后还剩什么
        if got != want_p:
            after_fails.append((case, got, want_p, classify(case, got, want_p)))

    def stats(fails):
        """口径：误拒=该放行却拒；漏放=该拒却**静默放行**(AUTO_APPROVE)；
        严重度错判=引擎升级给人确认（未放行），与直觉的拒绝力度不同。"""
        fr = sum(1 for _, g, w, _ in fails if g == Decision.REJECT and w != Decision.REJECT)
        fa = sum(1 for _, g, w, _ in fails if g == Decision.AUTO_APPROVE and w == Decision.REJECT)
        return fr, fa, len(fails) - fr - fa

    s_fr, s_fa, s_sv = stats(strict_fails)
    b_fr, b_fa, b_sv = stats(before_fails)
    a_fr, a_fa, a_sv = stats(after_fails)

    def pct(n):
        return f"{n/total*100:.2f}%"

    print("=" * 76)
    print(f"约束引擎压测报告 · seed={args.seed}")
    print("=" * 76)
    print(f"约束条数 {len(constraints)}　判定次数 {total}　对抗样本 {sum(1 for c in cases if c.kind!='normal')}")
    print()
    print("【第一层】当前引擎 vs 引擎规格参照 —— 检验实现正确性")
    print(f"  一致率 {(total-len(strict_fails))/total*100:.2f}%　误拒 {s_fr} / 漏放 {s_fa} / 严重度 {s_sv}")
    for cat, n in Counter(c for *_, c in strict_fails).most_common():
        print(f"    - {cat}: {n} 例")
    print()
    print("【第二层】修复前语义 vs 用户直觉 —— 用户此前实际遭遇什么")
    print(f"  一致率 {(total-len(before_fails))/total*100:.2f}%　"
          f"误拒 {b_fr} ({pct(b_fr)}) / 漏放 {b_fa} ({pct(b_fa)}) / 严重度 {b_sv} ({pct(b_sv)})")
    for cat, n in Counter(c for *_, c in before_fails).most_common():
        print(f"    - {cat}: {n} 例")
    print()
    print("【第三层】当前引擎 vs 用户直觉 —— 修复后还剩什么")
    print(f"  一致率 {(total-len(after_fails))/total*100:.2f}%　"
          f"误拒 {a_fr} ({pct(a_fr)}) / 漏放 {a_fa} ({pct(a_fa)}) / 严重度 {a_sv} ({pct(a_sv)})")
    for cat, n in Counter(c for *_, c in after_fails).most_common():
        print(f"    - {cat}: {n} 例")
    print()
    print("判定分布（当前引擎）：", dict(got_dist))
    print()

    if before_fails:
        print("修复前的问题样例（前 6 条）：")
        for case, got, want, cat in before_fails[:6]:
            c, r = case.constraint, case.request
            print(f"  [{case.kind}] vendor={r.vendor!r} category={r.category!r} ${r.amount_usd} "
                  f"白名单={c.vendor_whitelist} 品类白名单={c.category_whitelist}")
            print(f"      用户直觉={want.value}  修复前={got.value}  → {cat}")
        print()

    # ── 写报告 ──
    lines = [
        "# 约束引擎压测报告（D12）",
        "",
        f"- 随机种子：`{args.seed}`",
        f"- 约束条数：**{len(constraints)}**（随机生成：月度滚动预算 / 单笔阈值 / 日累计阈值 / 供应商白名单 / 品类白名单）",
        f"- 请求条数：**{total}**（其中 **{sum(1 for c in cases if c.kind!='normal')}** 条为对抗样本）",
        f"- 判定总次数：**{total}**",
        "",
        "## 三层口径与核心指标",
        "",
        "| 口径 | 含义 | 一致率 | 误拒 | 漏放 | 严重度错判 |",
        "|---|---|---|---|---|---|",
        f"| 当前引擎 vs 引擎规格 | 检验**实现有没有写错** | {(total-len(strict_fails))/total*100:.2f}% | {s_fr} | {s_fa} | {s_sv} |",
        f"| 修复前语义 vs 用户直觉 | 量化**用户此前实际遭遇什么** | {(total-len(before_fails))/total*100:.2f}% | {b_fr} | {b_fa} | {b_sv} |",
        f"| 当前引擎 vs 用户直觉 | **修复后**还剩什么 | {(total-len(after_fails))/total*100:.2f}% | {a_fr} | {a_fa} | {a_sv} |",
        "",
        "## 发现的问题分类（修复前口径）",
        "",
    ]
    if before_fails:
        lines += ["| 类别 | 例数 | 占比 | 说明 |", "|---|---|---|---|"]
        for cat, n in Counter(c for *_, c in before_fails).most_common():
            lines.append(f"| {cat} | {n} | {n/total*100:.2f}% | {_CATEGORY_DESC.get(cat,'')} |")
    else:
        lines.append("本次随机种子下未发现问题。")

    lines += [
        "",
        "## 已应用的修复",
        "",
        "| 问题 | 修复 |",
        "|---|---|",
        "| 供应商大小写/空格误拒 | 白名单匹配改为**归一化**（去空格 + 忽略大小写） |",
        "| 品类大小写误拒 | 同上，品类白名单比较也归一化 |",
        "| 浮点尾数边界错判 | 金额**按分取整**后再与阈值/预算比较 |",
        "| 品类未知时静默放行 | 改为 **REQUIRE_HUMAN**：既不静默放行，也不硬阻断 |",
        "",
        "## 判定分布（当前引擎）",
        "",
        "| 判定 | 次数 |",
        "|---|---|",
    ]
    for k in ("AUTO_APPROVE", "REQUIRE_HUMAN", "REJECT"):
        lines.append(f"| {k} | {got_dist.get(k,0)} |")

    if before_fails:
        lines += ["", "### 问题样例（修复前）", "", "```"]
        for case, got, want, cat in before_fails[:10]:
            c, r = case.constraint, case.request
            lines.append(
                f"[{case.kind}] vendor={r.vendor!r} category={r.category!r} ${r.amount_usd} | "
                f"白名单={c.vendor_whitelist} 品类白名单={c.category_whitelist} | "
                f"直觉={want.value} 修复前={got.value} → {cat}"
            )
        lines.append("```")

    lines += [
        "",
        "## 方法论说明",
        "",
        "压测**不以引擎自身为标准答案**（那等于没测）。本报告维护三份独立参照实现：",
        "",
        "1. **引擎规格 oracle** —— 与当前实现逐条对齐，用来检验**代码有没有写错**；",
        "2. **修复前语义 oracle** —— 还原修复前的判定口径，量化**用户此前实际遭遇什么**；",
        "3. **用户直觉 oracle** —— 按真实用户预期推导（名称归一化、金额按分取整、"
        "品类未知即不放心），用来暴露**用户会撞上的误拒/漏放**。",
        "",
        "三层结果的差距，正是「测试全绿但用户仍觉得被冤枉」的地方。"
        "本次修复即由第 2/3 层的差异驱动。",
        "",
        "对抗样本覆盖：金额恰等于阈值/预算、阈值+0.01、供应商大小写、供应商带空格、"
        "品类大小写、请求缺品类、浮点尾数 —— 均为真实事故高发区。",
        "",
        "复现：`python scripts/stress_constraints.py --seed 42`",
        "",
    ]
    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"报告已写入 {args.out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
