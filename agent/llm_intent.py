"""LLM function-calling 意图解析器（OpenAI 协议兼容）。

当前接入：腾讯 MaaS `glm-5.3-flash`（`LLM_BASE_URL=https://tokenhub.tencentmaas.com/v1`）。
任何 OpenAI 兼容端点都能直接换（DeepSeek / Moonshot / 通义），改 `.env` 三个变量即可：
`LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL`。

三个 tool schema：set_constraint / create_purchase_request / **query_catalog**。

**为什么要有 query_catalog**：规则解析器只能做"分类"，做不了"推理"。
「Notion 和 Figma 挑便宜的买」这类句式，模型必须先查目录拿到价格再比较才能下单。
所以这里不是一次 function calling 就结束，而是**多轮 tool loop**：
模型可以先调 query_catalog 拿数据，把结果喂回去，再基于数据做决策。

降级策略（重要）：
没配 Key、或调用失败时，parse_with_llm 返回 None，
由 intent.parse() 自动回退到规则解析，保证 demo 永远可跑。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI

from .models import Constraint, PurchaseRequest

# 必须与 paypal_tools / store 保持一致：模块自包含地加载 .env。
# 否则「只有恰好先 import 了别的模块才拿得到 Key」，LLM 路径会静默降级 ——
# 表现是 demo 全绿但根本没走模型，极难察觉。
# 用包根绝对路径（而非 cwd），保证从任意目录启动都能读到配置。
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

_MAX_TOOL_ROUNDS = 4  # 工具循环最大轮数，防止模型死循环

_SYSTEM_PROMPT_BASE = """你是 MandatePay 的支付约束解析器，负责把用户的自然语言转成结构化调用。

规则：
1. 用户描述预算/限额/白名单/阈值 → 调 set_constraint
2. 用户要买东西/订阅服务 → 调 create_purchase_request
3. 金额一律转成数字（美元），"200美元" → 200
4. 提取不到供应商时，从商品名推断（"Notion Pro" → "Notion"）
5. 判断不了就调 unknown
6. **用户没提单笔阈值时，single_tx_threshold_usd 取月度预算的 50%**（例：预算 200 → 阈值 100）。
   不要因为缺这个字段就放弃调用工具。
7. 用户提到品类（如 SaaS）但没有明确供应商时，填 category_whitelist，vendor_whitelist 留空数组。

**推理能力（重要）**：遇到需要"比较 / 选择 / 查价 / 消歧"的句式，不要猜，
先调 `query_catalog` 拿到真实的供应商与订阅计划价格，再基于返回结果做决定：
8. "Notion 和 Figma 挑便宜的买" → 先 query_catalog，看清价格后选**单价最低**的那个下单
9. "先订那个设计工具" → 先 query_catalog，按目录里的描述消歧（如 Figma 是设计工具）
10. "预算看着办" → 参考下面给出的「当前生效约束」，不要凭空编造预算数字；
    若用户没要求改约束，就**不要**调 set_constraint，直接下单即可
11. 需要同时设约束又下单时，两个工具可以一次都调用

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
    {
        "type": "function",
        "function": {
            "name": "query_catalog",
            "description": (
                "查询可购买的供应商目录与 SaaS 订阅计划（含真实价格）。"
                "需要在多个供应商之间比较价格、需要按描述消歧、或不确定某商品多少钱时，"
                "必须先调用它，不要凭记忆猜价格。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "vendor": {
                        "type": "string",
                        "description": "按供应商名过滤；留空表示返回全部供应商与计划",
                    }
                },
            },
        },
    },
]


@dataclass
class LLMParseResult:
    """一次 LLM 解析的完整结果（含推理轨迹，便于 Demo 展示"AI 怎么想的"）。"""

    constraint: Constraint | None = None
    request: PurchaseRequest | None = None
    reasoning: str = ""          # 模型输出的自然语言推理
    trace: list[str] = field(default_factory=list)  # 工具调用轨迹



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


def _query_catalog(vendor_filter: str | None = None) -> str:
    """query_catalog 工具的实现：返回供应商目录与订阅计划（含真实价格）。

    读本地目录（已由 PayPal 同步），避免每次推理都打 PayPal API。
    """
    from agent import store

    try:
        vendors = store.list_vendors()
        plans = store.list_subscription_plans()
    except Exception as exc:
        return f"目录查询失败: {exc}"

    if vendor_filter:
        key = vendor_filter.strip().lower()
        vendors = [v for v in vendors if key in (v.get("name") or "").lower()]
        ids = {v["paypal_product_id"] for v in vendors}
        plans = [p for p in plans if p.get("paypal_product_id") in ids]

    if not vendors and not plans:
        return "（目录为空，暂时没有可购买的供应商与订阅计划）"

    lines = ["可用供应商与订阅计划："]
    by_product: dict[str, list] = {}
    for p in plans:
        by_product.setdefault(p.get("paypal_product_id", ""), []).append(p)
    for v in vendors:
        pid = v.get("paypal_product_id", "")
        vplans = by_product.get(pid, [])
        if vplans:
            for pl in vplans:
                lines.append(
                    f"- 供应商 {v.get('name')}｜计划 {pl.get('name')}｜"
                    f"${float(pl.get('amount_usd') or 0):.2f} {pl.get('currency', 'USD')}"
                    f"/{pl.get('interval_count', 1)}{pl.get('interval_unit', 'MONTH')}"
                    f"｜说明：{pl.get('description') or v.get('description') or '无'}"
                )
        else:
            lines.append(f"- 供应商 {v.get('name')}｜暂无订阅计划｜说明：{v.get('description') or '无'}")
    return "\n".join(lines)


def _build_constraint(args: dict) -> Constraint | None:
    try:
        budget = float(args.get("monthly_budget_usd") or 0)
        if budget <= 0:
            return None
        # 阈值兜底：模型没给或给 0 时，取预算的 50%（与 system prompt 规则一致）
        threshold = float(args.get("single_tx_threshold_usd") or 0)
        if threshold <= 0:
            threshold = budget / 2
        return Constraint(
            monthly_budget_usd=budget,
            single_tx_threshold_usd=threshold,
            vendor_whitelist=args.get("vendor_whitelist") or [],
            category_whitelist=args.get("category_whitelist") or [],
        )
    except Exception:
        return None


def _build_request(args: dict) -> PurchaseRequest | None:
    try:
        amount = float(args.get("amount_usd") or 0)
        if amount <= 0:
            return None
        return PurchaseRequest(
            vendor=args.get("vendor") or "",
            item=args.get("item") or "",
            amount_usd=amount,
            recurring=bool(args.get("recurring", False)),
        )
    except Exception:
        return None


def _system_prompt(current_constraint: Constraint | None) -> str:
    """拼系统提示，并把「当前生效约束」喂进去 —— 让"预算看着办"有据可依。"""
    prompt = _SYSTEM_PROMPT_BASE
    if current_constraint is not None:
        prompt += (
            "\n\n当前生效约束（用户说\"预算看着办\"等模糊表述时，以它为准，不要编造）：\n"
            f"- 月度预算 ${current_constraint.monthly_budget_usd:.2f}\n"
            f"- 单笔阈值 ${current_constraint.single_tx_threshold_usd:.2f}\n"
            f"- 日累计阈值 "
            + (f"${current_constraint.daily_threshold_usd:.2f}" if current_constraint.daily_threshold_usd else "不限")
            + f"\n- 供应商白名单 {current_constraint.vendor_whitelist or '不限'}"
            + f"\n- 品类白名单 {current_constraint.category_whitelist or '不限'}"
        )
    return prompt


def parse_with_llm(
    text: str,
    current_constraint: Constraint | None = None,
) -> LLMParseResult | None:
    """用 LLM function calling 解析一句话（支持多轮工具调用）。

    与"一次 function calling 就返回"的区别：
    模型可以先调 `query_catalog` 拿目录数据，我们把结果作为 ToolMessage 喂回去，
    再问一轮 —— 这就是让 AI 能做**比较 / 消歧 / 查价**而不是只做分类。

    返回 LLMParseResult；完全失败或无 key 时返回 None，由调用方降级到规则解析。
    """
    client = _client()
    if client is None:
        return None

    messages = [
        SystemMessage(content=_system_prompt(current_constraint)),
        HumanMessage(content=text),
    ]
    result = LLMParseResult()

    for _ in range(_MAX_TOOL_ROUNDS):
        try:
            resp = client.invoke(messages, tools=_TOOLS, tool_choice="auto")
        except Exception:
            break

        reasoning = (getattr(resp, "content", "") or "").strip()
        if reasoning:
            result.reasoning = reasoning

        calls = getattr(resp, "tool_calls", None) or []
        if not calls:
            break

        need_catalog = False
        tool_messages: list[ToolMessage] = []
        for call in calls:
            name = call.get("name")
            args = call.get("args") or {}
            call_id = call.get("id") or ""

            if name == "query_catalog":
                need_catalog = True
                vf = (args.get("vendor") or "").strip() or None
                result.trace.append(f"query_catalog(供应商={vf or '全部'})")
                tool_messages.append(
                    ToolMessage(content=_query_catalog(vf), tool_call_id=call_id)
                )
                continue

            if name == "set_constraint":
                c = _build_constraint(args)
                if c is not None:
                    result.constraint = c
                    result.trace.append(
                        f"set_constraint(月预算=${c.monthly_budget_usd:.0f}, "
                        f"单笔阈值=${c.single_tx_threshold_usd:.0f}, "
                        f"白名单={c.vendor_whitelist or '不限'})"
                    )
                    tool_messages.append(
                        ToolMessage(content=f"约束已记录：{c.model_dump()}", tool_call_id=call_id)
                    )
                else:
                    tool_messages.append(
                        ToolMessage(content="约束参数无效，未记录", tool_call_id=call_id)
                    )
                continue

            if name == "create_purchase_request":
                r = _build_request(args)
                if r is not None:
                    result.request = r
                    result.trace.append(
                        f"create_purchase_request({r.vendor} · {r.item} · ${r.amount_usd:.2f})"
                    )
                    tool_messages.append(
                        ToolMessage(content=f"采购请求已记录：{r.model_dump()}", tool_call_id=call_id)
                    )
                else:
                    tool_messages.append(
                        ToolMessage(content="采购参数无效，未记录", tool_call_id=call_id)
                    )
                continue

            tool_messages.append(
                ToolMessage(content="未知工具，已忽略", tool_call_id=call_id)
            )

        # 只有当这一轮真的查了目录，才需要把工具结果喂回去再问一轮
        if need_catalog:
            messages.append(resp)
            messages.extend(tool_messages)
            continue
        break

    if result.constraint or result.request:
        return result
    return None
