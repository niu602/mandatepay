"""解析缓存的单元测试（W-14 · T6）。

被测行为：`agent.llm_intent.parse_with_llm` 的**进程内 LRU 缓存**。

验收点（工单 DoD）：**相同输入第二次零网络请求**。
做法不是"看结果对不对"，而是**数网络请求次数** ——
用一个假 client 替换真 client，它的 `invoke()` 每次调用都会计数；
同时保留真实的 `_invoke_with_retry`，让 `_STATS["calls"]` 也跟着走，
这样既能断言"没发请求"，也能断言"降级计数器没被动过"。

全程不联网、不消耗 LLM 额度。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import llm_intent  # noqa: E402
from agent.models import Constraint  # noqa: E402


class _FakeResponse:
    """模拟模型返回：带一个 create_purchase_request 的 tool_call。"""

    def __init__(self):
        self.content = "好的，帮你下单。"
        self.tool_calls = [{
            "name": "create_purchase_request",
            "args": {"vendor": "Notion", "item": "Notion Plus", "amount_usd": 10.0},
            "id": "call_1",
        }]


class _FakeClient:
    """假 client：每次 invoke 都记账。"""

    def __init__(self):
        self.invoke_count = 0

    def invoke(self, *a, **kw):
        self.invoke_count += 1
        return _FakeResponse()


@pytest.fixture
def fake_client(monkeypatch):
    """装好假 client、关掉节流、清空缓存与计数器。"""
    llm_intent.clear_parse_cache()
    llm_intent.reset_llm_stats()
    monkeypatch.setattr(llm_intent, "_MIN_INTERVAL", 0.0)   # 别为节流睡 4 秒
    client = _FakeClient()
    monkeypatch.setattr(llm_intent, "_client", lambda: client)
    yield client
    llm_intent.clear_parse_cache()


def test_same_text_second_time_makes_zero_network_calls(fake_client):
    """DoD：相同输入第二次零网络请求。"""
    text = "buy Notion for 10 dollars"

    r1 = llm_intent.parse_with_llm(text)
    assert r1 is not None and r1.request is not None
    assert fake_client.invoke_count == 1
    assert llm_intent.llm_stats()["calls"] == 1

    r2 = llm_intent.parse_with_llm(text)
    assert r2 is not None and r2.request is not None
    assert r2.request.vendor == "Notion"
    assert fake_client.invoke_count == 1, "第二次命中缓存，不应再发请求"
    assert llm_intent.llm_stats()["calls"] == 1, "缓存命中不该动降级计数器"


def test_different_text_still_calls_llm(fake_client):
    """不同输入 → 各打各的。"""
    llm_intent.parse_with_llm("buy Notion for 10 dollars")
    llm_intent.parse_with_llm("buy Figma for 15 dollars")
    assert fake_client.invoke_count == 2


def test_cache_result_is_a_copy(fake_client):
    """命中缓存返回的是副本 —— 调用方改了它不能污染缓存。"""
    text = "buy Notion for 10 dollars"
    first = llm_intent.parse_with_llm(text)
    first.request.vendor = "MUTATED"

    second = llm_intent.parse_with_llm(text)
    assert second.request.vendor == "Notion", "缓存被污染了"
    assert fake_client.invoke_count == 1


def test_constraint_change_invalidates_cache(fake_client):
    """约束变了 → 同一句话重新解析（system prompt 变了，结果可能不同）。"""
    text = "buy Notion for 10 dollars"
    llm_intent.parse_with_llm(text, current_constraint=None)
    assert fake_client.invoke_count == 1

    c = Constraint(
        monthly_budget_usd=200.0,
        single_tx_threshold_usd=50.0,
        vendor_whitelist=["Notion"],
    )
    llm_intent.parse_with_llm(text, current_constraint=c)
    assert fake_client.invoke_count == 2, "约束变了却命中缓存 = 拿到旧约束下的结果"


def test_failed_parse_is_not_cached(monkeypatch):
    """解析失败（返回 None）不缓存 —— 否则一次抖动会毒化整段时间。"""
    llm_intent.clear_parse_cache()
    llm_intent.reset_llm_stats()
    monkeypatch.setattr(llm_intent, "_MIN_INTERVAL", 0.0)

    class _NoToolClient:
        def __init__(self):
            self.invoke_count = 0

        def invoke(self, *a, **kw):
            self.invoke_count += 1
            resp = _FakeResponse()
            resp.tool_calls = []          # 模型没给 tool_call → 判定为失败
            return resp

    client = _NoToolClient()
    monkeypatch.setattr(llm_intent, "_client", lambda: client)

    text = "buy Notion for 10 dollars"
    assert llm_intent.parse_with_llm(text) is None
    assert llm_intent.parse_with_llm(text) is None
    assert client.invoke_count == 2, "失败结果不该被缓存（否则永远救不回来）"
    llm_intent.clear_parse_cache()


def test_cache_respects_maxsize(fake_client):
    """上限 128 条 —— 超了淘汰最老的。"""
    for i in range(llm_intent._PARSE_CACHE_MAXSIZE + 5):
        llm_intent.parse_with_llm(f"buy vendor {i} for 10 dollars")
    assert len(llm_intent._parse_cache) == llm_intent._PARSE_CACHE_MAXSIZE

    # 最老的那条已被淘汰 → 重新打一次 LLM
    before = fake_client.invoke_count
    llm_intent.parse_with_llm("buy vendor 0 for 10 dollars")
    assert fake_client.invoke_count == before + 1

    # 最近的一条还在缓存里 → 不再打
    before = fake_client.invoke_count
    llm_intent.parse_with_llm(f"buy vendor {llm_intent._PARSE_CACHE_MAXSIZE + 4} for 10 dollars")
    assert fake_client.invoke_count == before


def test_use_cache_false_bypasses_cache(fake_client):
    """验证脚本用 use_cache=False —— 每次都真打。"""
    text = "buy Notion for 10 dollars"
    llm_intent.parse_with_llm(text)
    llm_intent.parse_with_llm(text, use_cache=False)
    assert fake_client.invoke_count == 2
