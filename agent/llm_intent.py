"""LLM function-calling 意图解析器（OpenAI 协议兼容）。

当前接入：**Z.ai** `glm-4.7-flash`（`LLM_BASE_URL=https://api.z.ai/api/paas/v4`）。
任何 OpenAI 兼容端点都能直接换（腾讯 MaaS / DeepSeek / Moonshot / 通义 / OpenAI），
改 `.env` 三个变量即可：`LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL`。

三个 tool schema：set_constraint / create_purchase_request / **query_catalog**。

**为什么要有 query_catalog**：规则解析器只能做"分类"，做不了"推理"。
「Notion 和 Figma 挑便宜的买」这类句式，模型必须先查目录拿到价格再比较才能下单。
所以这里不是一次 function calling 就结束，而是**多轮 tool loop**：
模型可以先调 query_catalog 拿数据，把结果喂回去，再基于数据做决策。

降级策略（重要）：
没配 Key、或调用失败时，parse_with_llm 返回 None，
由 intent.parse() 自动回退到规则解析，保证 demo 永远可跑。
**降级必须可观测** —— 见 `_STATS`：每次降级都会 +1 并打 warning 日志，
验证脚本靠它统计降级次数，防止"AI 路径没生效但验证显示通过"的假绿。

**429 是免费档常态**：Z.ai 免费额度下会随机返回 429 / 错误码 1305（服务过载）。
实测 4 次请求就撞 1 次。因此所有 LLM 调用都走 `_invoke_with_retry`：
遇到 429 按 8 秒间隔退避重试（默认 3 次），用尽后才降级。
"""
from __future__ import annotations

import json
import logging
import os
import random
import time
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI

from .models import Constraint, PurchaseRequest

logger = logging.getLogger("mandatepay.llm")

# 必须与 paypal_tools / store 保持一致：模块自包含地加载 .env。
# 否则「只有恰好先 import 了别的模块才拿得到 Key」，LLM 路径会静默降级 ——
# 表现是 demo 全绿但根本没走模型，极难察觉。
# 用包根绝对路径（而非 cwd），保证从任意目录启动都能读到配置。
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

_MAX_TOOL_ROUNDS = 4  # 工具循环最大轮数，防止模型死循环

# ── 429 退避重试参数（Z.ai 免费档过载是常态，不是异常）────────────────
_RETRY_BASE_DELAY = float(os.getenv("LLM_RETRY_DELAY", "8"))  # 秒
_RETRY_MAX_ATTEMPTS = int(os.getenv("LLM_RETRY_MAX", "6"))     # 退避重试次数（不含首次）
# 重试间隔带**随机抖动**（±30%）：免费档高峰期大量请求同时被 429，
# 若所有客户端按同一节奏重试会互相踩（实测退避反而加重过载）。
_RETRY_JITTER = float(os.getenv("LLM_RETRY_JITTER", "0.3"))

# 客户端节流：两次 LLM 调用之间的最小间隔（秒）。
# 免费档同时有「速率限制」(1302) 与「服务过载」(1305) 两种 429。
# 实测（过载高峰）：无节流时 13 次请求只有 3 次成功；按固定节奏猛打会持续触发
# 1302，反而把退避变成"越退越堵"。主动限速比被动重试更有效。
# 默认 4s：一次采购要走 parse+insight+explain 三次调用，节流再大 Demo 就没法看了。
# 跑批量验证脚本时可用 LLM_MIN_INTERVAL 调大（验证脚本按 6s 跑，更稳）。
_MIN_INTERVAL = float(os.getenv("LLM_MIN_INTERVAL", "4"))

_last_call_at: float = 0.0


def _throttle() -> None:
    """客户端侧限速：保证两次调用之间至少间隔 `_MIN_INTERVAL` 秒。"""
    global _last_call_at
    if _MIN_INTERVAL <= 0:
        return
    wait = _MIN_INTERVAL - (time.monotonic() - _last_call_at)
    if wait > 0:
        time.sleep(wait)
    _last_call_at = time.monotonic()

# ── 可观测统计（防止"静默降级"）────────────────────────────────────
_STATS: dict[str, int] = {
    "calls": 0,          # 发起的 LLM 调用次数（含重试）
    "success": 0,        # 成功拿到响应的次数
    "rate_limited": 0,   # 撞到 429 / 1305 的次数
    "retries": 0,        # 退避重试的总次数
    "degraded": 0,       # 用尽重试后仍失败 → 降级到规则解析的次数
    "no_tool_call": 0,   # 模型没给 tool_call（也算一种降级前兆）
}


def llm_stats() -> dict[str, int]:
    """返回 LLM 调用统计的副本（验证脚本用它统计降级次数）。"""
    return dict(_STATS)


def reset_llm_stats() -> None:
    """清零统计（验证脚本每轮测试前调用）。"""
    for k in _STATS:
        _STATS[k] = 0

_SYSTEM_PROMPT_BASE = """你是 MandatePay 的支付约束解析器，负责把用户的自然语言转成结构化调用。

规则：
1. 用户描述预算/限额/白名单/阈值 → 调 set_constraint
2. 用户要买东西/订阅服务 → 调 create_purchase_request
3. 金额一律转成数字（美元），"200美元" → 200
4. 提取不到供应商时，从商品名推断（"Notion Pro" → "Notion"）
5. 判断不了就调 unknown
6. **用户没提单笔阈值时，single_tx_threshold_usd 取月度预算的 50%**（例：预算 200 → 阈值 100）。
   不要因为缺这个字段就放弃调用工具。
7. 用户**显式按品类限制**时（如"只允许买 SaaS 类的""只买设计工具"）才填 category_whitelist。
   **不要**把"每月 SaaS 预算 200"这类说法当成品类白名单 —— 那是在描述预算的用途，
   不是在说"只许买 SaaS"。这种情况只填 monthly_budget_usd，category_whitelist 留空。
8. 采购时若用户用词能判断品类，就填 category（如"设计工具"→Design、"协作工具"→Productivity）；
   判断不了就留空字符串，不要瞎猜。

**推理能力（重要）**：遇到需要"比较 / 选择 / 查价 / 消歧"的句式，不要猜，
先调 `query_catalog` 拿到真实的供应商与订阅计划价格，再基于返回结果做决定：
8. "Notion 和 Figma 挑便宜的买" → 先 query_catalog，看清价格后选**单价最低**的那个下单
9. "先订那个设计工具" → 先 query_catalog，再**在返回的"说明"里逐字匹配用户的品类词**
    （"设计工具"→ 说明含 "design tool" 的那个供应商 = Figma）。
    **匹配的是目录说明里的描述，不是商品名，也不是列表第一个** ——
    目录里 Notion 排在前面，但它是 "All-in-one workspace"，不是设计工具。
    判断依据只能来自 query_catalog 的返回内容，不要凭印象猜。
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
                    "category": {
                        "type": "string",
                        "description": "品类（如 SaaS / Design / Productivity）；用户用词能判断才填，判断不了留空",
                    },
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

    变量名统一用 LLM_*（当前实际接的是 Z.ai 的 glm-4.7-flash），
    同时保留 DEEPSEEK_* / OPENAI_* 作为兼容别名 —— 早期文档写的是 DeepSeek。

    **thinking 参数**：GLM 系列默认会输出思维链，token 消耗大且慢（实测一次
    简单调用多花 11 个 token）。Z.ai 支持 `"thinking": {"type": "disabled"}` 关闭。
    这里通过 `extra_body` 透传 —— 这是 OpenAI SDK 标准的「附加请求体字段」通道，
    非 Z.ai 端点若不认这个字段会报 400，所以做成**开关**：
    `LLM_THINKING=disabled` 才发送，默认不发送（保证换任何端点都不炸）。
    """
    api_key = (
        os.getenv("LLM_API_KEY")
        or os.getenv("DEEPSEEK_API_KEY")
        or os.getenv("OPENAI_API_KEY")
    )
    if not api_key:
        return None

    extra_body: dict | None = None
    if (os.getenv("LLM_THINKING") or "").strip().lower() == "disabled":
        extra_body = {"thinking": {"type": "disabled"}}

    return ChatOpenAI(
        model=os.getenv("LLM_MODEL") or os.getenv("DEEPSEEK_MODEL") or "deepseek-chat",
        api_key=api_key,
        base_url=os.getenv("LLM_BASE_URL")
        or os.getenv("DEEPSEEK_BASE_URL")
        or "https://api.deepseek.com",
        temperature=0.0,
        timeout=float(os.getenv("LLM_TIMEOUT", "60")),
        # 429 退避由我们自己控制（_invoke_with_retry），关掉 SDK 自带重试避免叠加
        max_retries=0,
        extra_body=extra_body,
    )


def _is_rate_limited(exc: Exception) -> bool:
    """判断异常是否为「服务过载/限流」——需要退避重试而不是直接放弃。

    Z.ai 免费档的过载有两种表现：
    1. HTTP 429 + code 1305（"service may be temporarily overloaded"）—— 服务过载
    2. HTTP 429 + code 1302（"Rate limit reached for requests"）—— 请求速率超限
    两种都要当作可重试。
    """
    text = f"{type(exc).__name__}: {exc}".lower()
    if "429" in text or "rate limit" in text or "too many requests" in text:
        return True
    if "1305" in text or "1302" in text or "overload" in text or "temporarily" in text:
        return True
    # openai 异常对象上带 status_code
    status = getattr(exc, "status_code", None)
    if status == 429:
        return True
    body = getattr(exc, "body", None)
    if isinstance(body, (str, bytes)) and b"1305" in str(body).encode():
        return True
    if isinstance(body, dict) and str(body.get("code")) == "1305":
        return True
    return False


def _invoke_with_retry(client: ChatOpenAI, messages, **kwargs):
    """带退避重试的调用（专治 429 / 1305 服务过载）。

    Z.ai 免费档 429 频率很高（实测高峰期 10 次里有 8 次失败），不重试会让 demo
    和验证脚本频繁掉到规则降级路径。策略：**指数退避 + 随机抖动**：
    第 n 次重试等 `base * 2^(n-1)` 秒，再叠加 ±30% 抖动。
    抖动是必需的 —— 固定间隔会让所有并发重试在同一秒打回去（实测线性退避
    反而让过载持续，重试 3 次全挂）。

    非 429 错误直接抛出（重试也没用）。
    """
    last_exc: Exception | None = None
    for attempt in range(_RETRY_MAX_ATTEMPTS + 1):
        _STATS["calls"] += 1
        _throttle()
        try:
            resp = client.invoke(messages, **kwargs)
            _STATS["success"] += 1
            return resp
        except Exception as exc:
            last_exc = exc
            if not _is_rate_limited(exc):
                raise
            _STATS["rate_limited"] += 1
            if attempt >= _RETRY_MAX_ATTEMPTS:
                break
            _STATS["retries"] += 1
            # 指数退避但设上限：纯指数到第 6 次要等 8+16+32+64+128+256 ≈ 8 分钟，
            # 单句阻塞过久。封顶 45s，配合客户端节流更实用。
            delay = min(_RETRY_BASE_DELAY * (2 ** attempt), 45.0)
            delay *= 1 + random.uniform(-_RETRY_JITTER, _RETRY_JITTER)
            logger.warning(
                "LLM 服务过载(429/1305)，%.1fs 后重试 (%d/%d): %s",
                delay, attempt + 1, _RETRY_MAX_ATTEMPTS, exc,
            )
            time.sleep(delay)

    assert last_exc is not None
    _STATS["degraded"] += 1
    logger.warning("LLM 重试 %d 次仍失败，降级到规则解析: %s", _RETRY_MAX_ATTEMPTS, last_exc)
    raise last_exc


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
        # 供应商描述要一并给出：模型靠它做品类消歧
        #（如 Figma 的 "Collaborative design tool" 才能支撑「订那个设计工具」这句）。
        # 计划描述常常是「Figma 月度订阅」这种无信息量的模板，只给计划描述会让消歧失败。
        vendor_desc = v.get("description") or ""
        if vplans:
            for pl in vplans:
                plan_desc = pl.get("description") or ""
                desc = "；".join(d for d in (vendor_desc, plan_desc) if d)
                # 供应商描述单独标出：它是模型做品类消歧的唯一依据
                # （"设计工具" → 说明里含 design tool 的那家）
                lines.append(
                    f"- 供应商 {v.get('name')}｜计划 {pl.get('name')}｜"
                    f"${float(pl.get('amount_usd') or 0):.2f} {pl.get('currency', 'USD')}"
                    f"/{pl.get('interval_count', 1)}{pl.get('interval_unit', 'MONTH')}"
                    f"｜供应商用途: {vendor_desc or '无'}"
                    f"｜计划说明: {plan_desc or '无'}"
                )
        else:
            lines.append(
                f"- 供应商 {v.get('name')}｜暂无订阅计划｜说明：{vendor_desc or '无'}"
            )
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
            # 品类：模型能判断才填，判断不了留空（空 = 品类未知，引擎会要求人工确认）
            category=(args.get("category") or "").strip(),
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
            resp = _invoke_with_retry(client, messages, tools=_TOOLS, tool_choice="auto")
        except Exception:
            break

        reasoning = (getattr(resp, "content", "") or "").strip()
        if reasoning:
            result.reasoning = reasoning

        calls = getattr(resp, "tool_calls", None) or []
        if not calls:
            # 模型没给 tool_call：不算失败，但结果会是「什么都没解析出来」，
            # 最终落到规则降级。计数以便验证时区分「模型不听话」与「服务过载」。
            _STATS["no_tool_call"] += 1
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
