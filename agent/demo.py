"""端到端演示：设定约束 → 小额自动采购 → 大额需审批 → 越界拒绝。

运行：.venv/Scripts/python.exe -m agent.demo
"""
from __future__ import annotations

from langchain_core.messages import HumanMessage

from .graph import graph


def run_step(thread_id: str, text: str) -> dict:
    """跑一轮状态机，返回最终 state。"""
    final = None
    for event in graph.stream(
        {"messages": [HumanMessage(content=text)]},
        {"configurable": {"thread_id": thread_id}},
    ):
        for node_name, node_out in event.items():
            print(f"  [{node_name}] → {_brief(node_out)}")
        final = event
    # 取最后一个节点的输出作为本步结果
    return final


def _brief(node_out: dict) -> str:
    """把节点输出压缩成一行可读摘要。"""
    if not isinstance(node_out, dict):
        return str(node_out)
    parts = []
    for k in ("constraint", "request", "decision", "error"):
        if k in node_out and node_out[k] is not None:
            parts.append(f"{k}={node_out[k]}")
    if "mandate" in node_out and node_out["mandate"]:
        m = node_out["mandate"]
        parts.append(f"mandate[{m.status.value}]")
    if "payment_result" in node_out and node_out["payment_result"]:
        p = node_out["payment_result"]
        parts.append(f"payment={p.get('status')}")
    return "; ".join(parts) if parts else "(无输出)"


def main():
    tid = "mandatepay-demo"

    print("=" * 60)
    print("Step 1 · 设定约束（规则解析）")
    print("=" * 60)
    run_step(tid, "每月预算 200 美元，单笔超过 50 美元需要我确认，只允许 Notion 和 Figma 供应商")

    print("\n" + "=" * 60)
    print("Step 2 · 小额自动采购（Notion Pro $10）")
    print("=" * 60)
    run_step(tid, "订阅 Notion Pro，10 美元每月")

    print("\n" + "=" * 60)
    print("Step 3 · 大额需审批（Figma 企业版 $80，在白名单内但超单笔阈值）")
    print("=" * 60)
    run_step(tid, "购买 Figma 企业版，80 美元每月")

    print("\n" + "=" * 60)
    print("Step 4 · 越界拒绝（不在白名单的 Slack $15）")
    print("=" * 60)
    run_step(tid, "订阅 Slack Pro，15 美元每月")

    print("\n✅ 三场景（含越界拒绝）端到端跑通")


if __name__ == "__main__":
    main()
