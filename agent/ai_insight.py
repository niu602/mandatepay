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

from .llm_intent import _client, _invoke_with_retry
from .models import Constraint, PurchaseRequest


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


# ── 消费洞察（D9）─────────────────────────────────────────────
_INSIGHT_SYSTEM = """你是 MandatePay 的消费洞察助手。基于给定的预算与已花费数据，
用一句简短的中文说出这笔消费的财务处境。要求：
- 只讲事实（已花/预算/本笔/余额/是否会通过），不要编造
- 语气简洁专业，一句话内说清，控制在 40 字以内
- 不要重复"本月已花"以外的冗余信息"""


def generate_insight_with_source(
    request: PurchaseRequest,
    constraint: Constraint | None,
    monthly_spent: float,
    decision: str,
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
    # 兜底文案（无 LLM 时也能给出有信息量的一句）
    verdict = {"AUTO_APPROVE": "会通过", "REQUIRE_HUMAN": "需你确认", "REJECT": "会被拦截"}.get(decision, "待定")
    fallback = (
        f"本月已花 ${monthly_spent:.0f}/${budget:.0f}，这笔 ${request.amount_usd:.0f} {verdict}，"
        f"预算余额 ${remaining:.0f}"
    )

    user = (
        f"月度预算：${budget:.0f}\n"
        f"本月已花费：${monthly_spent:.2f}\n"
        f"本笔请求：{request.item}（{request.vendor}）${request.amount_usd:.2f}\n"
        f"引擎判定：{decision}"
    )
    text = _llm_text(_INSIGHT_SYSTEM, user)
    if text:
        return text, True
    return fallback, False


def generate_insight(
    request: PurchaseRequest,
    constraint: Constraint | None,
    monthly_spent: float,
    decision: str,
) -> str:
    """生成一句消费洞察。失败/无 Key 时回退到模板。"""
    return generate_insight_with_source(request, constraint, monthly_spent, decision)[0]


# ── 决策解释（D8）─────────────────────────────────────────────
_EXPLAIN_SYSTEM = """你是 MandatePay 的约束解释助手。把引擎的判定与原因翻译成
一句可行动的中文。要求：
- 先说结论，再说原因，最后给**具体可操作的建议**（如"提高阈值""拆分订单""加入白名单""换供应商"）
- **只给确实需要改动的建议**：已经在白名单里的供应商不要再建议"加入白名单"；
  预算充足时不要说"提高预算"；金额远低于阈值时不要无中生有地提醒
- 用第二人称"你"，语气像一个靠谱的财务助理
- 控制在 60 字以内，不要罗列规则原文"""


def generate_explanation_with_source(
    request: PurchaseRequest,
    constraint: Constraint | None,
    decision: str,
    reason: str | None,
    monthly_spent: float = 0.0,
) -> tuple[str, bool]:
    """生成决策解释，**并标明这句是不是 LLM 写的**（同 `generate_insight_with_source`）。"""
    budget = constraint.monthly_budget_usd if constraint else 0.0
    threshold = constraint.single_tx_threshold_usd if constraint else 0.0
    # 兜底：按 decision 给一个带建议的固定解释
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

    user = (
        f"引擎判定：{decision}\n"
        f"引擎原因：{reason or '（无）'}\n"
        f"本笔请求：{request.item}（{request.vendor}）${request.amount_usd:.2f}\n"
        f"当前约束：月度预算 ${budget:.0f}、单笔阈值 ${threshold:.0f}"
        + (f"、本月已花 ${monthly_spent:.2f}" if monthly_spent else "")
    )
    text = _llm_text(_EXPLAIN_SYSTEM, user)
    if text:
        return text, True
    return fb, False


def generate_explanation(
    request: PurchaseRequest,
    constraint: Constraint | None,
    decision: str,
    reason: str | None,
    monthly_spent: float = 0.0,
) -> str:
    """生成决策解释（尤其擅长解释"为什么被拒"+ 给建议）。失败回退到模板。"""
    return generate_explanation_with_source(
        request, constraint, decision, reason, monthly_spent
    )[0]