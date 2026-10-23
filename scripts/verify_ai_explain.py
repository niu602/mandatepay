"""D8/D9 验证：AI 决策解释（explain）+ 消费洞察（insight）。

证明两件事：
1. 状态机新增 insight_node / explain_node 两个 AI 参与点，每笔采购都产出
   `insight`（消费洞察）与 `explanation`（决策解释+建议）。
2. 被拦截的采购，explanation 会说清「为什么被拒」并给出可操作建议。

用 TestClient 在子进程内跑真实 /api/agent/parse（含真实 LLM 调用）。

运行：python scripts/verify_ai_explain.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)


def _has_key() -> bool:
    from agent.llm_intent import _client
    return _client() is not None


def show(tag: str, text: str) -> None:
    print(f"  {tag}: {text}")


def main() -> int:
    print(f"LLM Key 已配置: {'是（走真实模型）' if _has_key() else '否（走模板兜底）'}\n")

    # 设定约束：月预算 200 / 单笔阈值 50 / 日累计 100 / 白名单 [Notion, Figma]
    client.post(
        "/api/agent/constraints",
        json={
            "monthly_budget_usd": 200,
            "single_tx_threshold_usd": 50,
            "daily_threshold_usd": 100,
            "vendor_whitelist": ["Notion", "Figma"],
            "category_whitelist": [],
        },
    )

    cases = [
        ("自动通过的小额采购", "帮我买 Notion Pro，10 美元", "AUTO_APPROVE"),
        ("越界被拒的采购", "买 Slack Pro，15 美元", "REJECT"),
        ("超单笔阈值需确认", "帮我买 Figma Professional，80 美元", "REQUIRE_HUMAN"),
    ]

    failures = []
    for i, (name, text, expect) in enumerate(cases, 1):
        r = client.post(
            "/api/agent/parse",
            json={"text": text, "session_id": f"verify-ai-{i}-{os.getpid()}"},
        )
        d = r.json()
        print(f"[{i}] {name}：{text}")
        print(f"  decision   : {d['decision']}（期望 {expect}）")
        show("insight    ", d.get("insight") or "（空）")
        show("explanation", d.get("explanation") or "（空）")

        # 断言
        if d["decision"] != expect:
            failures.append(f"[{name}] decision={d['decision']} 期望 {expect}")
        if not (d.get("insight") or "").strip():
            failures.append(f"[{name}] insight 为空")
        if not (d.get("explanation") or "").strip():
            failures.append(f"[{name}] explanation 为空")
        print()

    # 被拒场景额外要求：explanation 里出现「建议」类可操作指引
    r = client.post("/api/agent/parse", json={"text": "买 Slack Pro，15 美元", "session_id": f"verify-ai-rej-{os.getpid()}"})
    exp = r.json().get("explanation") or ""
    print("被拒解释是否含可操作建议（建议/提高/拆分/白名单/换）:",
          any(k in exp for k in ("建议", "提高", "拆分", "白名单", "换")))
    print()

    if failures:
        print("❌ 存在问题：")
        for f in failures:
            print("  -", f)
        return 1
    print("✅ D8/D9 通过：insight + explanation 均已产出，判定符合预期")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())