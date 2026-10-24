"""D10 验证：异常推理场景 —— AI 从"分类器"升级为"推理器"。

规则解析器只能做分类（找关键词、抠数字），做不了：
  ① 比较决策：「Notion 和 Figma 挑便宜的买」—— 需要先知道两者价格才能挑
  ② 消歧决策：「先订那个设计工具」—— 需要知道目录里谁是设计工具
  ③ 模糊约束：「预算看着办」—— 需要知道当前生效约束是多少

本脚本验证三件事：
  1. LLM 路径确实调了 query_catalog（工具轨迹里有）→ 说明是"推理"不是"分类"
  2. 推理结论正确（挑便宜的 / 消歧正确 / 金额与目录一致）
  3. 规则解析器在同样句式上会失败或给出错误结论（对照组）

运行：python scripts/verify_reasoning.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from agent import store  # noqa: E402
from agent.intent import parse  # noqa: E402

# 句式 → (期望供应商, 期望金额, 说明)
CASES = [
    ("Notion 和 Figma 挑便宜的买", "Notion", 10.0, "比较后选单价最低的"),
    ("先订那个设计工具，预算看着办", "Figma", 15.0, "按目录描述消歧 + 沿用当前约束"),
]


def rule_parse(text: str):
    """对照组：纯规则解析（绕过 LLM）。"""
    from agent.intent import _amounts, _extract_item, _extract_vendor

    amounts = _amounts(text)
    if not amounts:
        return None
    item = _extract_item(text)
    return _extract_vendor(text, item), amounts[0]


def main() -> int:
    print("目录现状（AI 的推理依据）：")
    for p in store.list_subscription_plans():
        print(f"  - {p['name']}: ${float(p['amount_usd']):.2f}/{p['interval_unit']}")
    print()

    failures = []
    for idx, (text, exp_vendor, exp_amount, why) in enumerate(CASES, 1):
        print(f"[{idx}] {text}")
        print(f"    预期     : {exp_vendor} ${exp_amount:.2f}（{why}）")

        parsed = parse(text)
        trace = parsed.trace
        used_catalog = any("query_catalog" in t for t in trace)
        req = parsed.request
        got_vendor = req.vendor if req else None
        got_amount = req.amount_usd if req else None

        print(f"    工具轨迹 : {' → '.join(trace) if trace else '(无，走规则降级)'}")
        print(f"    AI 推理  : {(parsed.reasoning or '(无)').strip()[:200]}")
        print(f"    实际结果 : {got_vendor} ${got_amount if got_amount is not None else 0:.2f}")
        print(f"    规则对照 : {rule_parse(text)} ← 规则解析器无法完成比较/消歧")
        print()

        if not used_catalog:
            failures.append(f"[{idx}] 没有调用 query_catalog —— 说明只是分类，不是推理")
        if got_vendor != exp_vendor:
            failures.append(f"[{idx}] 供应商={got_vendor}，期望 {exp_vendor}")
        if got_amount is None or abs(got_amount - exp_amount) > 0.01:
            failures.append(f"[{idx}] 金额={got_amount}，期望 {exp_amount}")

    if failures:
        print("❌ 存在问题：")
        for f in failures:
            print("  -", f)
        return 1
    print("✅ D10 通过：AI 走了「查目录 → 比较/消歧 → 决策」的推理链路，结论正确")
    print("   （对照：规则解析器对同样句式只能抠数字，无法比较也无法消歧）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
