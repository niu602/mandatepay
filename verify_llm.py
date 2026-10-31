# -*- coding: utf-8 -*-
"""验证 LLM 路径是否真正生效（而非静默降级到规则解析）。

配置来源：项目根目录的 .env（LLM_API_KEY / LLM_BASE_URL / LLM_MODEL）。
本文件不硬编码任何密钥，缺失时直接报错退出。
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# 从项目根目录 .env 读取（路径以本文件为准，不依赖当前工作目录）
load_dotenv(Path(__file__).resolve().parent / ".env")

if not os.getenv("LLM_API_KEY"):
    sys.exit("❌ 未配置 LLM_API_KEY —— 请配置 .env 中的 LLM_API_KEY 后重试。")
if not os.getenv("LLM_BASE_URL") or not os.getenv("LLM_MODEL"):
    sys.exit("❌ 未配置 LLM_BASE_URL / LLM_MODEL —— 请配置 .env 后重试。")

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
