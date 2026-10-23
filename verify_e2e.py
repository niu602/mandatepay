# -*- coding: utf-8 -*-
"""端到端跑 LangGraph 五节点，确认 LLM 路径 + 状态机都通。"""
import os

os.environ["LLM_API_KEY"] = "***REMOVED***"
os.environ["LLM_BASE_URL"] = "https://tokenhub.tencentmaas.com/v1"
os.environ["LLM_MODEL"] = "glm-5.3-flash"

from langchain_core.messages import HumanMessage
from agent.graph import graph

tid = "verify-e2e"
for t in ["每月预算 200 美元，单笔超 50 要确认，只允许 Notion 和 Figma", "订阅 Notion Pro，10 美元每月"]:
    print(f"\n=== {t} ===")
    try:
        final = None
        for event in graph.stream(
            {"messages": [HumanMessage(content=t)]},
            {"configurable": {"thread_id": tid}},
        ):
            for node, out in event.items():
                if out is not None:
                    print(f"  [{node}] {str(out)[:160]}")
            final = event
        print(f"  → 最终: {str(final.get(list(final.keys())[0], {}))[:200]}")
    except Exception as exc:
        print(f"  ERROR: {type(exc).__name__}: {exc}")
