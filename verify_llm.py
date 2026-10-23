# -*- coding: utf-8 -*-
"""验证 LLM 路径是否真正生效（而非静默降级到规则解析）。"""
import os

os.environ["LLM_API_KEY"] = "***REMOVED***"
os.environ["LLM_BASE_URL"] = "https://tokenhub.tencentmaas.com/v1"
os.environ["LLM_MODEL"] = "glm-5.3-flash"

from agent.intent import parse

cases = [
    "每月预算 200 美元，单笔超 50 要确认，只允许 Notion 和 Figma",
    "订阅 Notion Pro，10 美元每月",
    "购买 Figma 企业版，80 美元每月",
    "订阅 Slack Pro，15 美元每月",
]
for t in cases:
    p = parse(t)
    print(f"[{p.intent}] note={p.note}")
