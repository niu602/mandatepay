"""DeepSeek function-calling 意图解析验证脚本 —— D3 完成标志「输出稳定 JSON」的落点。

验证两件事：
1. **正确性**：自然语言能否转成预期结构（set_constraint / create_purchase_request）
2. **稳定性**：同一句跑 N 次结果是否一致（temperature=0，理论上应完全一致）

用法：
    python scripts/verify_llm_intent.py                # 默认每句跑 3 次
    python scripts/verify_llm_intent.py --repeat 5     # 加大稳定性样本
    python scripts/verify_llm_intent.py --only 1,3     # 只跑指定用例
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from agent.intent import Intent, parse  # noqa: E402
from agent.llm_intent import parse_with_llm  # noqa: E402

# (输入, 期望意图) —— 期望意图为 None 表示只观察不断言
CASES: list[tuple[str, str | None]] = [
    ("每月 SaaS 预算 $200", "SET_CONSTRAINT"),
    ("每月给 Notion 500 但超过 100 要问我", "SET_CONSTRAINT"),
    ("只允许买 Notion 和 Figma，单笔超过 50 需要我确认，月度预算 200", "SET_CONSTRAINT"),
    ("帮我买 Notion Pro，10 美元", "PURCHASE_REQUEST"),
    ("订阅 Figma 企业版，80 美元一个月", "PURCHASE_REQUEST"),
    ("买 Slack Pro 15 美元", "PURCHASE_REQUEST"),
]


def _dump(obj) -> str:
    if obj is None:
        return "None"
    return json.dumps(obj.model_dump(), ensure_ascii=False)


def main() -> int:
    ap = argparse.ArgumentParser(description="DeepSeek 意图解析验证")
    ap.add_argument("--repeat", type=int, default=3, help="每句重复次数（稳定性样本）")
    ap.add_argument("--only", default="", help="只跑指定用例序号，如 1,3")
    args = ap.parse_args()

    import os

    if not (os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")):
        print("❌ 未配置 DEEPSEEK_API_KEY，无法验证 LLM 路径（当前实际走规则降级）")
        print("   在 .env 里加一行：DEEPSEEK_API_KEY=sk-xxxx")
        return 1

    only = {int(x) for x in args.only.split(",") if x.strip()} if args.only else None
    cases = [(i, c) for i, c in enumerate(CASES, 1) if only is None or i in only]

    print(f"DeepSeek 意图解析验证 · {len(cases)} 个用例 × {args.repeat} 次\n" + "=" * 72)

    ok_count = 0
    stable_count = 0

    for idx, (text, expect) in cases:
        results: list[str] = []
        intents: list[str] = []
        for _ in range(args.repeat):
            intent, payload = parse_with_llm(text)
            intents.append(intent)
            results.append(_dump(payload))

        stable = len(set(results)) == 1
        hit = (expect is None) or (intents[0] == expect)
        ok_count += 1 if hit else 0
        stable_count += 1 if stable else 0

        flag = "✅" if (hit and stable) else ("⚠️ 结果不符" if not hit else "⚠️ 不稳定")
        print(f"\n[{idx}] {text}")
        print(f"    意图   : {intents[0] or '(无)'}" + (f"  期望 {expect}" if expect else ""))
        print(f"    JSON   : {results[0]}")
        print(f"    稳定性 : {'一致' if stable else f'{len(set(results))} 种不同结果'}")
        print(f"    判定   : {flag}")
        if not stable:
            for r in sorted(set(results)):
                print(f"       - {r}")

        # 对照：规则降级路径会给出什么（用于比较两条路径差异）
        parsed = parse(text)
        print(f"    规则降级对照: intent={parsed.intent.value}")

    print("\n" + "=" * 72)
    print(f"正确性: {ok_count}/{len(cases)}    稳定性: {stable_count}/{len(cases)}")
    return 0 if (ok_count == len(cases) and stable_count == len(cases)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
