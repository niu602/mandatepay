"""AI 决策解释与消费洞察（本周核心：让 AI 从"门铃"变成"每次决策都参与"）。

两个能力，都基于同一个 OpenAI 兼容端点（当前 glm-5.3-flash）：
- `generate_insight`：消费洞察。每笔采购前，结合本月已花费/预算/本笔金额，
  生成一句人话洞察（"本月已花 $170/$200，这笔 $10 会通过，但只剩 $20 余额"）。
- `generate_explanation`：决策解释。把约束引擎给出的 decision + reason，
  翻译成可行动的自然语言（"超出单笔阈值 $30，建议提高阈值或拆分订单"）。

两者都有**纯模板兜底**：无 Key / 调用失败 / 超时都返回可读的固定文案，
保证 demo 永远能跑完，不会因为一次 LLM 抖动就黑屏。
"""
from __future__ import annotations

import logging
import re

from .llm_intent import _client, _invoke_with_retry
from .models import Constraint, PurchaseRequest

logger = logging.getLogger(__name__)

# ── 界面语言（W-15）────────────────────────────────────────────
# 只有 en 模式切英文：both（双语 UI）/ zh / 未传都保持中文，既有调用零改动。
# 实测坑：LLM 即便被要求写英文，偶尔仍会吐中文；en 模式下这会直接破坏
# 页面语言一致性，所以下面加一道轻量语言守卫，命中就回退英文模板。
_CJK = re.compile(r"[\u4e00-\u9fff]")


def _is_en(lang: str) -> bool:
    return lang == "en"


def _language_mismatch(text: str, lang: str) -> bool:
    """en 模式下文本里出现汉字 → 语言不一致，应丢弃 LLM 输出。"""
    return _is_en(lang) and bool(_CJK.search(text or ""))


def _llm_text(system: str, user: str) -> str | None:
    """调一次 LLM 拿纯文本；任何异常返回 None，由调用方走兜底。

    走 `_invoke_with_retry`：Z.ai 免费档 429 是常态，不退避重试会让
    洞察/解释频繁掉到模板兜底，Demo 看起来像"AI 没参与"。
    """
    client = _client()
    if client is None:
        return None
    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        resp = _invoke_with_retry(
            client, [SystemMessage(content=system), HumanMessage(content=user)]
        )
        text = (getattr(resp, "content", "") or "").strip()
        return text or None
    except Exception:
        return None


# ── 一致性守卫：洞察不能和判定徽章打架 ───────────────────────────
# 实测坑：LLM 给一张 REJECT 的单子写洞察时，句子里冒出"会通过"——
# 而页面上同一行的徽章写着 REJECT。评委一眼就能看出矛盾，整段演示就废了。
#
# 所以 LLM 文本回来后做一次**轻量**校验：与判定矛盾 → 丢弃 LLM 文本，
# 改用下面的模板兜底（模板的 verdict 由判定映射而来，保证与徽章一致）。
#
# 只看"结论词"，不看整句语义 —— 这是刻意的：轻量、无 LLM、可测、零延迟。
_CONTRADICT = {
    # 判定为拒：文本却说"会通过" → 矛盾
    "REJECT": ("会通过", "可通过", "approved", "will pass"),
    # 判定为放行：文本却说"会被拦" → 矛盾
    "AUTO_APPROVE": ("会被拦截", "会被拒绝", "rejected", "will be blocked"),
    # 判定为需人工：两种结论词都不该出现
    "REQUIRE_HUMAN": (
        "会通过", "可通过", "approved", "will pass",
        "会被拦截", "会被拒绝", "rejected",
    ),
}

# 否定前缀：出现这些说明句子其实在否定（"不会通过" ≠ "会通过"）
_NEGATIONS = ("不", "非", "未", "没", "not ", "n't", "never ", "cannot ", "can't ")


def _contradicts_decision(text: str, decision: str) -> bool:
    """LLM 洞察文本是否与引擎判定矛盾。

    只在"肯定语气"下判定 —— "这笔不会通过"（REJECT 时是对的）不算矛盾。
    """
    needles = _CONTRADICT.get(decision, ())
    if not needles or not text:
        return False
    low = text.lower()
    for n in needles:
        nl = n.lower()
        idx = 0
        while True:
            i = low.find(nl, idx)
            if i < 0:
                break
            prefix = low[max(0, i - 4):i]
            if not any(neg in prefix for neg in _NEGATIONS):
                return True
            idx = i + len(nl)
    return False


# ── 消费洞察（D9）─────────────────────────────────────────────
_INSIGHT_SYSTEM = """你是 MandatePay 的消费洞察助手。基于给定的预算与已花费数据，
用一句简短的中文说出这笔消费的财务处境。要求：
- 只讲事实（已花/预算/本笔/余额/是否会通过），不要编造
- 语气简洁专业，一句话内说清，控制在 40 字以内
- 不要重复"本月已花"以外的冗余信息"""

_INSIGHT_SYSTEM_EN = """You are MandatePay's spending insight assistant. Given the budget and
spend figures, write ONE short sentence in ENGLISH describing the financial
position of this purchase. Rules:
- Facts only (spent / budget / this purchase / remaining / whether it passes)
- Concise and professional, one sentence, under 25 words
- Never invent figures; use exactly the numbers provided
- ENGLISH ONLY — do not use any Chinese characters"""


def generate_insight_with_source(
    request: PurchaseRequest,
    constraint: Constraint | None,
    monthly_spent: float,
    decision: str,
    lang: str = "zh",
) -> tuple[str, bool]:
    """生成一句消费洞察，**并标明这句是不是 LLM 写的**。

    返回 (文案, 来自_llm)：
    - `(LLM 文案, True)` —— 模型真的答了
    - `(模板兜底, False)` —— 无 Key / 调用失败 / 超时

    为什么需要这个标记：结构化快速路径（`/api/agent/requests`）要区分
    "这是 AI 的洞察" 与 "这是固定模板" —— 前者要标进响应，
    后者不能冒充 AI 输出（项目对"什么才算 AI 参与"很较真）。
    """
    budget = constraint.monthly_budget_usd if constraint else 0.0
    remaining = budget - monthly_spent
    en = _is_en(lang)
    # 兜底文案（无 LLM 时也能给出有信息量的一句）
    if en:
        verdict = {
            "AUTO_APPROVE": "will pass",
            "REQUIRE_HUMAN": "needs your approval",
            "REJECT": "will be blocked",
        }.get(decision, "is pending")
        fallback = (
            f"Spent ${monthly_spent:.0f} of ${budget:.0f} this month; "
            f"this ${request.amount_usd:.0f} {verdict}, ${remaining:.0f} left."
        )
    else:
        verdict = {"AUTO_APPROVE": "会通过", "REQUIRE_HUMAN": "需你确认", "REJECT": "会被拦截"}.get(decision, "待定")
        fallback = (
            f"本月已花 ${monthly_spent:.0f}/${budget:.0f}，这笔 ${request.amount_usd:.0f} {verdict}，"
            f"预算余额 ${remaining:.0f}"
        )

    if en:
        user = (
            f"Monthly budget: ${budget:.0f}\n"
            f"Spent this month: ${monthly_spent:.2f}\n"
            f"This request: {request.item} ({request.vendor}) ${request.amount_usd:.2f}\n"
            f"Engine decision: {decision}"
        )
    else:
        user = (
            f"月度预算：${budget:.0f}\n"
            f"本月已花费：${monthly_spent:.2f}\n"
            f"本笔请求：{request.item}（{request.vendor}）${request.amount_usd:.2f}\n"
            f"引擎判定：{decision}"
        )
    text = _llm_text(_INSIGHT_SYSTEM_EN if en else _INSIGHT_SYSTEM, user)
    if text:
        if _language_mismatch(text, lang):
            # en 模式下 LLM 仍吐中文 → 丢弃，回退英文模板（不冒充 AI 输出）
            logger.warning("[insight] en 模式下 LLM 返回中文，已回退英文模板：%s", text[:60])
        elif _contradicts_decision(text, decision):
            # 文本与判定徽章矛盾 → 丢弃，用模板（verdict 与徽章一致）
            logger.warning(
                "[insight] LLM 文本与判定 %s 矛盾，已回退模板：%s", decision, text[:60]
            )
        else:
            return text, True
    return fallback, False


def generate_insight(
    request: PurchaseRequest,
    constraint: Constraint | None,
    monthly_spent: float,
    decision: str,
    lang: str = "zh",
) -> str:
    """生成一句消费洞察。失败/无 Key 时回退到模板。"""
    return generate_insight_with_source(request, constraint, monthly_spent, decision, lang)[0]


# ── 决策解释（D8）─────────────────────────────────────────────
_EXPLAIN_SYSTEM = """你是 MandatePay 的约束解释助手。把引擎的判定与原因翻译成
一句可行动的中文。要求：
- 先说结论，再说原因，最后给**具体可操作的建议**（如"提高阈值""拆分订单""加入白名单""换供应商"）
- **只给确实需要改动的建议**：已经在白名单里的供应商不要再建议"加入白名单"；
  预算充足时不要说"提高预算"；金额远低于阈值时不要无中生有地提醒
- 用第二人称"你"，语气像一个靠谱的财务助理
- 控制在 60 字以内，不要罗列规则原文"""

_EXPLAIN_SYSTEM_EN = """You are MandatePay's constraint explanation assistant. Turn the engine's
decision and reason into ONE actionable sentence in ENGLISH. Rules:
- Verdict first, then the reason, then a concrete actionable suggestion
- Only suggest a change that is actually needed: never suggest allowlisting a
  vendor that is already allowlisted, nor raising a healthy budget
- Second person ("you"), like a reliable finance assistant
- Under 35 words, do not quote the raw rules
- ENGLISH ONLY — do not use any Chinese characters"""


def generate_explanation_with_source(
    request: PurchaseRequest,
    constraint: Constraint | None,
    decision: str,
    reason: str | None,
    monthly_spent: float = 0.0,
    lang: str = "zh",
) -> tuple[str, bool]:
    """生成决策解释，**并标明这句是不是 LLM 写的**（同 `generate_insight_with_source`）。"""
    budget = constraint.monthly_budget_usd if constraint else 0.0
    threshold = constraint.single_tx_threshold_usd if constraint else 0.0
    en = _is_en(lang)
    # 兜底：按 decision 给一个带建议的固定解释
    if en:
        if decision == "REJECT":
            fb = (f"Rejected: {reason or 'a constraint was violated'}. "
                  f"Adjust the constraints (raise the budget or threshold) or use an allowlisted vendor.")
        elif decision == "REQUIRE_HUMAN":
            fb = (f"{reason or 'The amount exceeds the per-transaction threshold'} "
                  f"Confirm it yourself, or split the order into smaller amounts.")
        elif decision == "AUTO_APPROVE":
            fb = (f"All constraints satisfied — this will run automatically. "
                  f"Spent ${monthly_spent:.0f} of ${budget:.0f} this month.")
        else:
            fb = reason or "Waiting for your next instruction."
        if not constraint:
            fb = "No constraints configured yet — the agent cannot spend autonomously. Set a budget and thresholds first."
    else:
        if decision == "REJECT":
            fb = f"这笔被拒：{reason or '违反约束'}。建议调整约束（提高预算/阈值）或改用白名单内的供应商。"
        elif decision == "REQUIRE_HUMAN":
            fb = f"{reason or '金额超过单笔阈值'}。金额偏大，建议你确认后再放行，或拆分订单降低单笔金额。"
        elif decision == "AUTO_APPROVE":
            fb = f"符合全部约束，将自动执行。本月已花 ${monthly_spent:.0f}/${budget:.0f}。"
        else:
            fb = reason or "等待你的下一步指令。"
        if not constraint:
            fb = "尚未设定约束，Agent 无法自主花钱。请先设置预算与阈值。"

    if en:
        user = (
            f"Engine decision: {decision}\n"
            f"Engine reason: {reason or '(none)'}\n"
            f"This request: {request.item} ({request.vendor}) ${request.amount_usd:.2f}\n"
            f"Active constraints: monthly budget ${budget:.0f}, per-transaction threshold ${threshold:.0f}"
            + (f", spent this month ${monthly_spent:.2f}" if monthly_spent else "")
        )
    else:
        user = (
            f"引擎判定：{decision}\n"
            f"引擎原因：{reason or '（无）'}\n"
            f"本笔请求：{request.item}（{request.vendor}）${request.amount_usd:.2f}\n"
            f"当前约束：月度预算 ${budget:.0f}、单笔阈值 ${threshold:.0f}"
            + (f"、本月已花 ${monthly_spent:.2f}" if monthly_spent else "")
        )
    text = _llm_text(_EXPLAIN_SYSTEM_EN if en else _EXPLAIN_SYSTEM, user)
    if text and not _language_mismatch(text, lang):
        return text, True
    if text:
        logger.warning("[explain] en 模式下 LLM 返回中文，已回退英文模板：%s", text[:60])
    return fb, False


def generate_explanation(
    request: PurchaseRequest,
    constraint: Constraint | None,
    decision: str,
    reason: str | None,
    monthly_spent: float = 0.0,
    lang: str = "zh",
) -> str:
    """生成决策解释（尤其擅长解释"为什么被拒"+ 给建议）。失败回退到模板。"""
    return generate_explanation_with_source(
        request, constraint, decision, reason, monthly_spent, lang
    )[0]