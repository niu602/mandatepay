"""DeepSeek function-calling 意图解析器。

DeepSeek 兼容 OpenAI 协议，用 ChatOpenAI 指到 api.deepseek.com 即可。
两个 tool schema：set_constraint / create_purchase_request。

降级策略（重要）：
没配 DEEPSEEK_API_KEY、或调用失败时，parse_with_llm 返回 None，
由 intent.parse() 自动回退到规则解析，保证 demo 永远可跑。
"""
from __future__ import annotations

import json
import os

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from .models import Constraint, PurchaseRequest

_SYSTEM_PROMPT = """你是 MandatePay 的支付约束解析器，负责把用户的自然语言转成结构化调用。

规则：
1. 用户描述预算/限额/白名单/阈值 → 调 set_constraint
2. 用户要买东西/订阅服务 → 调 create_purchase_request
3. 金额一律转成数字（美元），"200美元" → 200
4. 提取不到供应商时，从商品名推断（"Notion Pro" → "Notion"）
5. 判断不了就调 unknown

只调用一个工具，不要编造用户没说的信息。"""

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "set_constraint",
            "description": "用户设定 Agent 的花钱约束（预算、阈值、白名单）",
            "parameters": {
                "type": "object",
                "properties": {
                    "monthly_budget_usd": {"type": "number", "description": "月度预算上限，美元"},
                    "single_tx_threshold_usd": {"type": "number", "description": "单笔超过此金额需人工确认，美元"},
                    "vendor_whitelist": {"type": "array", "items": {"type": "string"}, "description": "允许的供应商列表"},
                    "category_whitelist": {"type": "array", "items": {"type": "string"}, "description": "允许的品类列表"},
                },
                "required": ["monthly_budget_usd", "single_tx_threshold_usd"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_purchase_request",
            "description": "用户要求采购/订阅某项商品或服务",
            "parameters": {
                "type": "object",
                "properties": {
                    "vendor": {"type": "string", "description": "供应商名称"},
                    "item": {"type": "string", "description": "商品或服务名"},
                    "amount_usd": {"type": "number", "description": "金额，美元"},
                    "recurring": {"type": "boolean", "description": "是否为周期性订阅"},
                },
                "required": ["vendor", "item", "amount_usd"],
            },
        },
    },
]


def _client() -> ChatOpenAI | None:
    api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    return ChatOpenAI(
        model="deepseek-chat",
        api_key=api_key,
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        temperature=0.0,
    )


def parse_with_llm(text: str) -> tuple[str, Constraint | PurchaseRequest | None]:
    """用 DeepSeek function calling 解析一句话。

    返回 (intent, payload)；失败或无 key 时返回 ("", None)，由调用方降级。
    """
    client = _client()
    if client is None:
        return "", None

    try:
        resp = client.invoke(
            [SystemMessage(content=_SYSTEM_PROMPT), HumanMessage(content=text)],
            tools=_TOOLS,
            tool_choice="auto",
        )
    except Exception:
        return "", None

    calls = getattr(resp, "tool_calls", None) or []
    if not calls:
        return "", None

    call = calls[0]
    name = call["name"]
    args = call.get("args") or {}

    if name == "set_constraint":
        try:
            return "SET_CONSTRAINT", Constraint(
                monthly_budget_usd=float(args.get("monthly_budget_usd") or 0) or None,
                single_tx_threshold_usd=float(args.get("single_tx_threshold_usd") or 0) or None,
                vendor_whitelist=args.get("vendor_whitelist") or [],
                category_whitelist=args.get("category_whitelist") or [],
            )
        except Exception:
            return "", None

    if name == "create_purchase_request":
        try:
            return "PURCHASE_REQUEST", PurchaseRequest(
                vendor=args.get("vendor") or "",
                item=args.get("item") or "",
                amount_usd=float(args.get("amount_usd") or 0),
                recurring=bool(args.get("recurring", False)),
            )
        except Exception:
            return "", None

    return "", None
