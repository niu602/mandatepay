"""规则意图解析器：把自然语言拆成「设定约束」或「采购请求」。

为什么先用规则而不是 LLM：
1. 状态机骨架阶段要能「零依赖跑通」，不绑 LLM key / 网络；
2. 规则解析可离线、可单测，先锁定数据流与状态机结构；
3. MandatePay 的核心卖点是「自然语言设定约束」，这一步之后会替换为
   LLM function calling（parse_node 里调用 bound_llm），但接口保持不变，
   所以状态机代码无需改动。

设计约定：parse() 返回 ParsedIntent，其中 constraint / request 至多一个非空。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from .llm_intent import parse_with_llm
from .models import Constraint, PurchaseRequest


class Intent(str, Enum):
    SET_CONSTRAINT = "SET_CONSTRAINT"
    PURCHASE_REQUEST = "PURCHASE_REQUEST"
    UNKNOWN = "UNKNOWN"


@dataclass
class ParsedIntent:
    intent: Intent
    constraint: Constraint | None = None
    request: PurchaseRequest | None = None
    note: str = ""
    reasoning: str = ""                    # 模型给出的自然语言推理（Demo 展示用）
    trace: list[str] = field(default_factory=list)  # 工具调用轨迹


# 常见 SaaS / 供应商名，用于从商品名里抽出 vendor
_VENDOR_HINTS = [
    "Notion", "Figma", "Adobe", "Slack", "GitHub", "OpenAI", "Anthropic",
    "Dropbox", "Zoom", "Canva", "Linear", "Vercel", "AWS", "GCP", "Azure",
    "Netflix", "Spotify", "Miro", "Asana", "Jira", "Trello", "1Password",
]

_AMOUNT_RE = re.compile(r"(?:\$|￥|¥)?\s*(\d+(?:\.\d+)?)\s*(?:美元|美金|元|USD|块钱)?")

_SET_TRIGGERS = ["预算", "白名单", "约束", "设定", "限制", "上限", "需要确认", "允许", "额度", "只能", "仅限"]
_BUY_TRIGGERS = ["订阅", "购买", "买", "订购", "采购", "续费", "下单"]


def _amounts(text: str) -> list[float]:
    return [float(m.group(1)) for m in _AMOUNT_RE.finditer(text)]


def _vendors(text: str) -> list[str]:
    found = []
    for v in _VENDOR_HINTS:
        if v.lower() in text.lower():
            found.append(v)
    return found


def _extract_vendor(text: str, item: str) -> str:
    """优先从文本里匹配已知供应商；否则取商品名第一个 token。"""
    known = _vendors(text)
    if known:
        return known[0]
    return item.split()[0] if item.split() else "unknown"


def parse(text: str) -> ParsedIntent:
    """解析一句话。

    策略：优先 LLM function calling（配了 key 才生效），支持多轮工具调用
    （模型可先查供应商目录再决策），失败或无 key 时自动降级到规则解析，
    保证 demo 永远可跑。

    把「当前生效约束」喂给模型，让"预算看着办"这类模糊表述有据可依。
    """
    from agent import store

    try:
        current = store.load_latest_constraint()
    except Exception:
        current = None

    res = parse_with_llm(text, current_constraint=current)
    if res is not None:
        # 可能同时给出约束与采购请求 —— 采购优先（它是动作），约束一并保留待落库
        if res.request is not None:
            note = "LLM(function calling + 工具循环)解析为采购请求"
            if res.constraint is not None:
                note += "（同时含约束更新）"
            if res.trace:
                note += f"｜工具轨迹: {' → '.join(res.trace)}"
            return ParsedIntent(
                Intent.PURCHASE_REQUEST,
                constraint=res.constraint,
                request=res.request,
                note=note,
                reasoning=res.reasoning,
                trace=list(res.trace),
            )
        if res.constraint is not None:
            note = "LLM(function calling)解析为约束"
            if res.trace:
                note += f"｜工具轨迹: {' → '.join(res.trace)}"
            return ParsedIntent(
                Intent.SET_CONSTRAINT,
                constraint=res.constraint,
                note=note,
                reasoning=res.reasoning,
                trace=list(res.trace),
            )

    # 降级：规则解析
    text = text.strip()
    amounts = _amounts(text)
    is_set = any(k in text for k in _SET_TRIGGERS)
    is_buy = any(k in text for k in _BUY_TRIGGERS) and amounts

    # 设定约束：出现约束触发词，且通常带有两个金额（预算 + 阈值）
    if is_set and not is_buy:
        monthly = amounts[0] if amounts else 0.0
        threshold = amounts[1] if len(amounts) > 1 else monthly
        vendors = _vendors(text)
        constraint = Constraint(
            monthly_budget_usd=monthly or 200.0,
            single_tx_threshold_usd=threshold or monthly or 50.0,
            vendor_whitelist=vendors,
            category_whitelist=[],
        )
        return ParsedIntent(Intent.SET_CONSTRAINT, constraint=constraint, note="规则解析为约束")

    # 采购请求：出现购买触发词且有金额
    if is_buy:
        amount = amounts[0]
        item = _extract_item(text)
        vendor = _extract_vendor(text, item)
        recurring = ("每月" in text) or ("订阅" in text) or ("按月" in text)
        request = PurchaseRequest(
            vendor=vendor,
            item=item,
            amount_usd=amount,
            recurring=recurring,
        )
        return ParsedIntent(Intent.PURCHASE_REQUEST, request=request, note="规则解析为采购请求")

    return ParsedIntent(Intent.UNKNOWN, note=f"无法识别意图: {text[:60]}")


def _extract_item(text: str) -> str:
    """粗略提取商品名：去掉触发词与金额后的剩余文本，做简单清洗。"""
    cleaned = text
    for w in ["订阅", "购买", "买", "订购", "采购", "续费", "下单", "每月", "美元", "美金", "元", "USD"]:
        cleaned = cleaned.replace(w, " ")
    cleaned = _AMOUNT_RE.sub(" ", cleaned)
    # 去掉数字残留、标点残留与多余空格
    cleaned = re.sub(r"\d+", " ", cleaned)
    cleaned = re.sub(r"[，,。.、；;：:！!？?]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or "未知商品"
