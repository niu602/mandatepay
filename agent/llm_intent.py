"""LLM function-calling 意图解析器（OpenAI 协议兼容）。

当前接入：腾讯 MaaS `glm-5.3-flash`（`LLM_BASE_URL=https://tokenhub.tencentmaas.com/v1`）。
任何 OpenAI 兼容端点都能直接换（DeepSeek / Moonshot / 通义），改 `.env` 三个变量即可：
`LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL`。

两个 tool schema：set_constraint / create_purchase_request。

降级策略（重要）：
没配 Key、或调用失败时，parse_with_llm 返回 None，
由 intent.parse() 自动回退到规则解析，保证 demo 永远可跑。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from .models import Constraint, PurchaseRequest

# 必须与 paypal_tools / store 保持一致：模块自包含地加载 .env。
# 否则「只有恰好先 import 了别的模块才拿得到 Key」，LLM 路径会静默降级 ——
# 表现是 demo 全绿但根本没走模型，极难察觉。
# 用包根绝对路径（而非 cwd），保证从任意目录启动都能读到配置。
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

_SYSTEM_PROMPT = """你是 MandatePay 的支付约束解析器，负责把用户的自然语言转成结构化调用。

规则：
1. 用户描述预算/限额/白名单/阈值 → 调 set_constraint
2. 用户要买东西/订阅服务 → 调 create_purchase_request
3. 金额一律转成数字（美元），"200美元" → 200
4. 提取不到供应商时，从商品名推断（"Notion Pro" → "Notion"）
5. 判断不了就调 unknown
6. **用户没提单笔阈值时，single_tx_threshold_usd 取月度预算的 50%**（例：预算 200 → 阈值 100）。
   不要因为缺这个字段就放弃调用工具。
7. 用户提到品类（如 SaaS）但没有明确供应商时，填 category_whitelist，vendor_whitelist 留空数组。

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
                # 注意：不要把 single_tx_threshold_usd 设为 required —— 用户常常只说预算，
                # 模型填不出必填字段时会直接放弃调用工具（实测 3 次里 1 次返回无 tool call），
                # 导致整句降级到规则解析。缺值由代码按「预算的 50%」兜底。
                "required": ["monthly_budget_usd"],
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
    """构造 OpenAI 协议兼容客户端。

    变量名统一用 LLM_*（当前实际接的是腾讯 MaaS 的 glm-5.3-flash），
    同时保留 DEEPSEEK_* / OPENAI_* 作为兼容别名 —— 早期文档写的是 DeepSeek。
    """
    api_key = (
        os.getenv("LLM_API_KEY")
        or os.getenv("DEEPSEEK_API_KEY")
        or os.getenv("OPENAI_API_KEY")
    )
    if not api_key:
        return None
    return ChatOpenAI(
        model=os.getenv("LLM_MODEL") or os.getenv("DEEPSEEK_MODEL") or "deepseek-chat",
        api_key=api_key,
        base_url=os.getenv("LLM_BASE_URL")
        or os.getenv("DEEPSEEK_BASE_URL")
        or "https://api.deepseek.com",
        temperature=0.0,
        timeout=float(os.getenv("LLM_TIMEOUT", "60")),
        max_retries=1,
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
            budget = float(args.get("monthly_budget_usd") or 0)
            if budget <= 0:
                return "", None
            # 阈值兜底：模型没给或给 0 时，取预算的 50%（与 system prompt 规则一致）
            threshold = float(args.get("single_tx_threshold_usd") or 0)
            if threshold <= 0:
                threshold = budget / 2
            return "SET_CONSTRAINT", Constraint(
                monthly_budget_usd=budget,
                single_tx_threshold_usd=threshold,
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
