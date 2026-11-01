# -*- coding: utf-8 -*-
"""Z.ai 意图解析重测 —— 20 句 × N 次，覆盖 AUTO_APPROVE / REQUIRE_HUMAN / REJECT。

背景（W-05）：LLM 从腾讯 MaaS 切到 Z.ai（海外可注册，海外评委拿得到 key），
原「6 句 × 3 次」样本量太小，且腾讯端点对海外评审不可达，故重做测试集。

**期望结果全部由人工枚举并固化在本文件的 CASES 表里**（不是模型自评、
不是运行后回填），因此这是一份可复现的断言集。

判定口径（与 agent/constraints.py 引擎完全一致，测试脚本直接复用引擎，
不做二次实现，避免"测试逻辑与生产逻辑不一致"）：
- 固定约束：月度预算 $200 / 单笔阈值 $50 / 日累计阈值不限
  / 供应商白名单 [Notion, Figma] / 品类白名单空
- 固定花费快照：monthly=0, daily=0（不读 DB，保证可复现）
- 引擎校验顺序：供应商白名单 → 品类白名单 → 月度预算 → 单笔阈值 → 日累计 → 品类未知
  ⇒ 供应商不在白名单 → REJECT；金额 > 预算 → REJECT；金额 > 单笔阈值 → REQUIRE_HUMAN

**降级必须可观测**：脚本统计每次 run 是否掉到规则降级（parse_with_llm 返回 None）
与底层 429/1305 重试次数。若降级数 > 0，脚本返回非 0 退出码 —— 防止
"AI 路径没生效但验证显示通过"的假绿。

用法：
    python scripts/verify_llm_zai.py                     # 20 句 × 3 次（默认）
    python scripts/verify_llm_zai.py --repeat 5
    python scripts/verify_llm_zai.py --thinking disabled  # 关闭思维链
    python scripts/verify_llm_zai.py --only 1,3,5
    python scripts/verify_llm_zai.py --md out.md         # 额外输出 markdown 表格
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

logger = logging.getLogger("mandatepay.verify")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

# 必须在 import agent.llm_intent **之前**设置：模块在加载时就把
# LLM_MIN_INTERVAL / LLM_RETRY_MAX 读成了模块级常量，事后改 os.environ 无效。
# 批量验证比 Demo 更吃速率（Demo 一笔采购 3 次调用，这里要打 60+ 次），
# 免费档过载时（实测高峰期成功率仅 20%）主动限速能显著提高成功率。
os.environ.setdefault("LLM_MIN_INTERVAL", "6")
os.environ.setdefault("LLM_RETRY_MAX", "6")

from agent.constraints import SpendContext, evaluate  # noqa: E402
from agent.llm_intent import (  # noqa: E402
    llm_stats,
    parse_with_llm,
    reset_llm_stats,
)
from agent.models import Constraint  # noqa: E402

# ── 固定测试约束与花费快照（保证可复现，不读 DB）─────────────────────
TEST_CONSTRAINT = Constraint(
    monthly_budget_usd=200.0,
    single_tx_threshold_usd=50.0,
    daily_threshold_usd=0.0,
    vendor_whitelist=["Notion", "Figma"],
    category_whitelist=[],
)
TEST_SPEND = SpendContext(monthly_usd=0.0, daily_usd=0.0)

# 目录里的真实价格（供人工推定期望值）：Notion Plus $10 / Figma Professional $15

# 端点过载（429）导致的降级 → 等多久重跑、最多重跑几次。
# 与"模型判错"严格区分：过载是端点侧容量问题，重跑是合理的；
# 模型判错则直接算失败，不给重跑机会。
# 过载是**持续状态**（实测连续 20 次请求只有 2 次成功），所以重试要等得久、
# 次数要多 —— 60s × 5 次 = 最多等 5 分钟换一次机会。
_OVERLOAD_RETRY_DELAY = float(os.getenv("VERIFY_OVERLOAD_DELAY", "60"))
_OVERLOAD_RETRY_MAX = int(os.getenv("VERIFY_OVERLOAD_RETRY_MAX", "5"))


@dataclass
class Case:
    """一个测试用例。

    expected_decision 由人工按固定约束 + 引擎校验顺序枚举，不看模型输出。
    expected_vendor / expected_amount 为 None 表示该项不作断言
    （例如推理类句式的金额依赖模型查目录后的选择，只断言判定结果）。
    """

    text: str
    expected_decision: str
    expected_vendor: str | None = None
    expected_amount: float | None = None
    note: str = ""


CASES: list[Case] = [
    # ── AUTO_APPROVE（10 句）：白名单内供应商 + 金额 ≤ $50 ──────────
    Case("订阅 Notion Pro，10 美元每月", "AUTO_APPROVE", "Notion", 10.0, "标准订阅句"),
    Case("买 Figma Professional，15 美元一个月", "AUTO_APPROVE", "Figma", 15.0, "标准订阅句"),
    Case("帮我订阅 Notion Plus，10 美元/月", "AUTO_APPROVE", "Notion", 10.0, "斜杠写法"),
    Case("订 Figma，15 美元每月", "AUTO_APPROVE", "Figma", 15.0, "省略商品名"),
    Case("买 Notion，50 美元", "AUTO_APPROVE", "Notion", 50.0, "边界：等于阈值不触发人工确认"),
    Case("Notion 和 Figma 挑便宜的买", "AUTO_APPROVE", "Notion", 10.0, "推理：须查目录比价，选 $10 的 Notion"),
    Case("先订那个设计工具", "AUTO_APPROVE", "Figma", 15.0, "推理：须查目录消歧，Figma=design tool"),
    Case("买 Notion Pro，10 美元，月付", "AUTO_APPROVE", "Notion", 10.0, "末尾补充计费周期"),
    Case("购买 Figma，15 美元每月", "AUTO_APPROVE", "Figma", 15.0, "同义动词"),
    Case("订阅 Notion Plus，10美元", "AUTO_APPROVE", "Notion", 10.0, "金额无空格"),

    # ── REQUIRE_HUMAN（5 句）：白名单内但金额超单笔阈值 ──────────────
    Case("订阅 Figma 企业版，80 美元每月", "REQUIRE_HUMAN", "Figma", 80.0, "超阈值但未超预算"),
    Case("买 Notion 企业版，120 美元", "REQUIRE_HUMAN", "Notion", 120.0, "超阈值但未超预算"),
    Case("订 Figma，60 美元一个月", "REQUIRE_HUMAN", "Figma", 60.0, "略超阈值"),
    Case("买 Notion，50.01 美元", "REQUIRE_HUMAN", "Notion", 50.01, "边界：刚好越过阈值 1 分"),
    Case("采购 Notion 团队版，99 美元每月", "REQUIRE_HUMAN", "Notion", 99.0, "超阈值但未超预算"),

    # ── REJECT（5 句）：供应商不在白名单 / 金额超预算 ─────────────────
    Case("订阅 Slack Pro，15 美元每月", "REJECT", "Slack", 15.0, "供应商不在白名单"),
    Case("买 Zoom Pro，20 美元每月", "REJECT", "Zoom", 20.0, "供应商不在白名单"),
    Case("买 Adobe Photoshop，60 美元每月", "REJECT", "Adobe", 60.0, "供应商不在白名单（校验顺序早于阈值）"),
    # 注意（用例 19）：模型把「Jira Cloud」识别成 vendor=Atlassian（Jira 的母公司）。
    # 这是**正确**的识别，且 Atlassian 同样不在白名单 → 判定仍是 REJECT，与期望一致。
    # 所以这里只断言判定结果，不锁死供应商名 —— 供应商字段在 REJECT 场景下
    # 不影响判定，而不同模型/版本可能给出 Jira 或 Atlassian 两种合理答案。
    Case("订阅 Jira Cloud，30 美元", "REJECT", None, 30.0, "供应商不在白名单（识别为 Atlassian/Jira 均可）"),
    Case("买 Notion 企业版，250 美元", "REJECT", "Notion", 250.0, "金额超月度预算 $200"),
]


def _judge(case: Case) -> tuple[str, str | None, float | None, str | None]:
    """跑一次 LLM 解析 + 引擎判定。

    返回 (decision, vendor, amount, error)。error 非 None 表示这次降级了。
    """
    # 用**增量**判断本次是否因端点过载而失败：
    # _STATS 是累计计数器，直接看 "degraded > 0" 会把之前的过载算到本次头上，
    # 误判成"模型问题"。必须比较调用前后的差值。
    before = llm_stats()
    res = parse_with_llm(case.text, current_constraint=TEST_CONSTRAINT)
    after = llm_stats()
    degraded_delta = after["degraded"] - before["degraded"]
    no_tool_delta = after["no_tool_call"] - before["no_tool_call"]
    rate_limited_delta = after["rate_limited"] - before["rate_limited"]

    if res is None or res.request is None:
        # 区分三种降级原因，意义完全不同：
        # 1) overload：429 重试全部耗尽 → 端点容量问题，与模型能力无关
        # 2) degraded_response：端点返回了 HTTP 200 但**响应残缺**（无 tool_calls），
        #    实测过载高峰期会出现这种"半截响应"。它同样是端点问题 ——
        #    正常端点在同样的 prompt 下必然给 tool_calls（同一句话已实测多次成功）。
        # 3) no_tool_call：端点正常响应但模型确实没调工具 → 真实的模型问题，算失败
        #
        # 判定 2 与 3 的依据：本次调用是否撞过 429。
        # 撞过 429 说明端点正在过载，此时的残缺响应应归因于端点。
        if degraded_delta > 0:
            return ("", None, None, "端点过载(429 重试耗尽)导致降级")
        if no_tool_delta > 0 and rate_limited_delta > 0:
            return ("", None, None, "端点过载(429 后返回残缺响应)导致降级")
        return ("", None, None, "模型未产出采购请求（无 tool_call）")

    req = res.request
    decision = evaluate(req, TEST_CONSTRAINT, spend=TEST_SPEND).decision.value
    return (decision, req.vendor, req.amount_usd, None)


def _judge_with_overload_retry(case: Case) -> tuple[str, str | None, float | None, str | None, int]:
    """跑一次判定；若因**端点过载**降级，则等一会儿重跑。

    为什么要区分：端点过载是 Z.ai 免费档的容量问题（实测过载高峰成功率仅 20%），
    与「模型判断是否正确」是两件事。若把过载直接算作模型失败，测的就不是模型；
    若把过载直接忽略，就等于掩盖了"AI 路径当时确实没生效"。

    处理：过载导致的降级 → 等待后重跑（默认最多 3 次），并**单独计数上报**；
    模型自身问题（无 tool_call / 判定错误）→ 直接计为失败，不重跑。
    """
    overload_retries = 0
    for attempt in range(_OVERLOAD_RETRY_MAX + 1):
        decision, vendor, amount, err = _judge(case)
        if err is None or "端点过载" not in err:
            return decision, vendor, amount, err, overload_retries
        if attempt >= _OVERLOAD_RETRY_MAX:
            return decision, vendor, amount, err, overload_retries
        overload_retries += 1
        logger.info("用例因端点过载降级，%.0fs 后重跑 (%d/%d)",
                    _OVERLOAD_RETRY_DELAY, attempt + 1, _OVERLOAD_RETRY_MAX)
        time.sleep(_OVERLOAD_RETRY_DELAY)
    return decision, vendor, amount, err, overload_retries


def main() -> int:
    ap = argparse.ArgumentParser(description="Z.ai 意图解析重测（20 句 × N 次）")
    ap.add_argument("--repeat", type=int, default=3, help="每句重复次数")
    ap.add_argument("--only", default="", help="只跑指定用例序号，如 1,3,5")
    ap.add_argument(
        "--thinking",
        choices=["enabled", "disabled"],
        default="",
        help="是否关闭思维链（默认沿用 .env 的 LLM_THINKING）",
    )
    ap.add_argument("--md", default="", help="额外输出 markdown 表格到指定文件")
    ap.add_argument(
        "--state",
        default="",
        help="断点续跑的状态文件（默认 .verify_state.json）：逐句落盘，"
             "中途失败可续跑，不必从头再来",
    )
    ap.add_argument("--fresh", action="store_true", help="忽略已有状态文件，从头重跑")
    args = ap.parse_args()

    if args.thinking:
        os.environ["LLM_THINKING"] = args.thinking

    if not (os.getenv("LLM_API_KEY") or os.getenv("DEEPSEEK_API_KEY")):
        print("❌ 未配置 LLM_API_KEY —— 请在 .env 配置后重试（当前会走规则降级，验证无意义）")
        return 1

    print("=" * 78)
    print(f"端点  : {os.getenv('LLM_BASE_URL')}")
    print(f"模型  : {os.getenv('LLM_MODEL')}")
    print(f"思维链: {os.getenv('LLM_THINKING') or '默认(端点自身行为)'}")
    print(f"约束  : 预算 ${TEST_CONSTRAINT.monthly_budget_usd:.0f} / 阈值 "
          f"${TEST_CONSTRAINT.single_tx_threshold_usd:.0f} / 白名单 {TEST_CONSTRAINT.vendor_whitelist}")
    print(f"样本  : {len(CASES)} 句 × {args.repeat} 次")
    print("=" * 78)

    only = {int(x) for x in args.only.split(",") if x.strip()} if args.only else None
    cases = [(i, c) for i, c in enumerate(CASES, 1) if only is None or i in only]

    # ── 断点续跑 ──────────────────────────────────────────────────────
    # 免费档过载时单轮跑完 20 句可能要 40+ 分钟，中途被打断就得从头再来。
    # 逐句落盘后，即使被 kill 也能接着跑 —— 结果是累积的，不浪费已付出的额度。
    state_path = Path(args.state or ".verify_state.json")
    state: dict = {}
    if state_path.exists() and not args.fresh:
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            state = {}

    reset_llm_stats()

    total_runs = 0
    correct_runs = 0
    degraded_runs = 0
    unstable_cases = 0
    wrong_cases = 0
    overload_count = 0
    rows: list[dict] = []

    for idx, case in cases:
        # 断点续跑：这一句已完成且次数够，直接复用历史结果
        prev = state.get(str(idx))
        if prev and not args.fresh and len(prev.get("decisions", [])) >= args.repeat:
            decisions = prev["decisions"]
            vendors = prev["vendors"]
            amounts = prev["amounts"]
            errors = prev.get("errors", [])
            overload_retries_total = prev.get("overload_retries", 0)
            print(f"\n[{idx:>2}] {case.text}\n     （复用已有结果，跳过）")
        else:
            decisions = []
            vendors = []
            amounts = []
            errors = []
            overload_retries_total = 0
            for _ in range(args.repeat):
                decision, vendor, amount, err, overload_retries = _judge_with_overload_retry(case)
                decisions.append(decision)
                vendors.append(vendor or "")
                amounts.append(amount if amount is not None else 0.0)
                if err:
                    errors.append(err)
                overload_retries_total += overload_retries
            state[str(idx)] = {
                "decisions": decisions,
                "vendors": vendors,
                "amounts": amounts,
                "errors": errors,
                "overload_retries": overload_retries_total,
            }
            state_path.write_text(
                json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        stable = len(set(decisions)) == 1
        hit = all(d == case.expected_decision for d in decisions)
        field_hit = True
        if case.expected_vendor is not None:
            field_hit = field_hit and all(v == case.expected_vendor for v in vendors)
        if case.expected_amount is not None:
            field_hit = field_hit and all(abs(a - case.expected_amount) < 0.005 for a in amounts)

        # 正确率统计必须覆盖**全部**用例（含断点续跑复用来的），
        # 否则汇总里的 "正确率" 只反映本轮新跑的部分，会低估/高估真实结果。
        for d in decisions:
            total_runs += 1
            if d == "":
                degraded_runs += 1
            elif d == case.expected_decision:
                correct_runs += 1

        if not hit:
            wrong_cases += 1
        if not stable:
            unstable_cases += 1
        overload_count += overload_retries_total

        flag = "✅" if (hit and stable and field_hit and not errors) else "❌"
        print(f"\n[{idx:>2}] {case.text}")
        print(f"     期望判定 : {case.expected_decision}   （{case.note}）")
        print(f"     实际判定 : {', '.join(decisions)}")
        print(f"     解析结果 : vendor={vendors[0]!r} amount={amounts[0]:.2f}")
        if errors:
            print(f"     ⚠️ 降级   : {errors[0]}（本句 {len(errors)}/{args.repeat} 次）")
        print(f"     稳定性   : {'一致' if stable else f'{len(set(decisions))} 种不同判定'}"
              f"｜字段匹配: {'是' if field_hit else '否'}")
        print(f"     判定     : {flag}")

        rows.append({
            "idx": idx,
            "text": case.text,
            "expected": case.expected_decision,
            "actual": decisions,
            "stable": stable,
            "field_ok": field_hit,
            "degraded": len(errors),
            "overload": overload_retries_total,
            "vendor": vendors[0],
            "amount": amounts[0],
            "note": case.note,
        })

    stats = llm_stats()

    print("\n" + "=" * 78)
    print("汇总")
    print("=" * 78)
    print(f"用例总数        : {len(cases)}")
    print(f"判定正确率      : {correct_runs}/{total_runs} 次运行"
          f"（逐句全对: {len(cases) - wrong_cases}/{len(cases)}）")
    print(f"判定稳定        : {len(cases) - unstable_cases}/{len(cases)} 句三次一致")
    print(f"字段匹配(供应商/金额): {sum(1 for r in rows if r['field_ok'])}/{len(cases)} 句")
    print(f"降级次数(LLM 未生效): {degraded_runs}/{total_runs} 次运行")
    print(f"  ├ 因端点过载重跑   : {overload_count} 次（429 耗尽重试后重跑，"
          f"已计入上面的降级统计之外）")
    print(f"  ├ LLM 调用次数 : {stats['calls']}")
    print(f"  ├ 成功         : {stats['success']}")
    print(f"  ├ 撞 429/1305  : {stats['rate_limited']}")
    print(f"  ├ 退避重试     : {stats['retries']}")
    print(f"  ├ 重试用尽降级 : {stats['degraded']}")
    print(f"  └ 无 tool_call : {stats['no_tool_call']}")

    ok = (
        wrong_cases == 0
        and unstable_cases == 0
        and all(r["field_ok"] for r in rows)
        and degraded_runs == 0
    )
    print(f"\n结论: {'✅ 全部通过' if ok else '❌ 存在未通过项'}")
    if overload_count:
        print(f"      （本轮有 {overload_count} 次因端点 429 过载重跑，端点侧容量问题，非模型缺陷）")

    if args.md:
        md = _markdown(rows, args, stats, ok)
        Path(args.md).write_text(md, encoding="utf-8")
        print(f"markdown 已写入: {args.md}")

    return 0 if ok else 1


def _markdown(rows, args, stats, ok) -> str:
    lines = [
        "| # | 测试句 | 期望判定 | 实际判定（×%d） | 供应商 | 金额 | 稳定 | 降级 | 说明 |"
        % args.repeat,
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        actual = " / ".join(r["actual"]) if r["actual"] else "(空)"
        lines.append(
            f"| {r['idx']} | {r['text']} | {r['expected']} | {actual} | "
            f"{r['vendor']} | ${r['amount']:.2f} | {'✅' if r['stable'] else '❌'} | "
            f"{r['degraded']} | {r['note']} |"
        )
    lines += [
        "",
        f"- 端点：`{os.getenv('LLM_BASE_URL')}`　模型：`{os.getenv('LLM_MODEL')}`",
        f"- 思维链：`{os.getenv('LLM_THINKING') or '默认'}`",
        f"- LLM 调用 {stats['calls']} 次，成功 {stats['success']} 次，"
        f"429/1305 {stats['rate_limited']} 次，退避重试 {stats['retries']} 次，"
        f"重试用尽降级 {stats['degraded']} 次，无 tool_call {stats['no_tool_call']} 次",
        f"- 结论：{'✅ 全部通过' if ok else '❌ 存在未通过项'}",
    ]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
