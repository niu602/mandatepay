"""存活探针与保温脚本的单元测试 —— W-07。

为什么要有这组测试：
`/healthz` 是**部署依赖**的端点（平台健康检查 + 外部保温探针都打它）。
如果它哪天因为一次重构而 500 或变慢，线上表现是"服务看起来挂了"，
但本地其他测试全绿 —— 这种故障最难查。

覆盖：
1. `/healthz` 返回 200，且**不查库、不调 PayPal**（这是它的核心契约）
2. `/healthz` 与 `/api/health` 行为分离（前者极轻、后者带诊断信息）
3. `scripts/keep_warm.py` 的 CPU 预算核算逻辑
4. `scripts/keep_warm.py` 的代理绕过（实测踩过的坑）

运行：python -m pytest tests/ -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)


# ──────────────────────────────────────────────────────
# 1. /healthz 的契约
# ──────────────────────────────────────────────────────

def test_healthz_returns_200():
    """存活探针必须 200 —— 否则平台会认为实例不健康。"""
    r = client.get("/healthz")
    assert r.status_code == 200


def test_healthz_body_is_minimal():
    """返回体刻意极小：pinger 每 30 分钟打一次，body 越小越好。

    只断言 status 键存在且为 ok —— 不锁死其他字段，
    避免以后加诊断信息时无谓地改测试。
    """
    r = client.get("/healthz")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    # 轻量的另一面：不该有一大串诊断字段
    assert len(data) <= 3, f"/healthz 返回体应保持极小，实际字段：{sorted(data)}"


def test_healthz_does_not_query_database(monkeypatch):
    """核心契约：**不查库**。

    做法：把 store 的连接入口换成会抛异常的桩 ——
    如果 /healthz 碰了数据库，这个测试就会失败。
    """
    from agent import store

    def _boom(*args, **kwargs):
        raise AssertionError("/healthz 不该碰数据库")

    monkeypatch.setattr(store, "_Conn", _boom)
    monkeypatch.setattr(store, "backend_name", _boom)

    r = client.get("/healthz")
    assert r.status_code == 200


def test_healthz_does_not_call_paypal(monkeypatch):
    """核心契约：**不调 PayPal**。

    把 paypal_client 的取 token 函数换成抛异常的桩。
    """
    from app import paypal_client

    def _boom(*args, **kwargs):
        raise AssertionError("/healthz 不该调 PayPal")

    # 模块内所有可能发起 HTTP 的入口都换成桩
    for name in ("_get_token", "_access_token", "get_access_token", "_token"):
        if hasattr(paypal_client, name):
            monkeypatch.setattr(paypal_client, name, _boom)


def test_healthz_is_fast():
    """存活探针必须快 —— 它会被平台高频调用。

    阈值 1秒在本机足够宽松（实测 36ms），但能挡住
    "不小心在探针里加了 DB 查询"这类回归。
    """
    import time

    t0 = time.perf_counter()
    r = client.get("/healthz")
    elapsed = time.perf_counter() - t0

    assert r.status_code == 200
    assert elapsed < 1.0, f"/healthz 耗时 {elapsed:.3f}s，超过 1 秒 —— 探针不该这么重"


# ──────────────────────────────────────────────────────
# 2. 与 /api/health 的分工
# ──────────────────────────────────────────────────────

def test_api_health_still_works_and_is_richer():
    """/api/health 保留诊断信息（db_backend / paypal_mode / 订阅事件）。

    刻意保留这个端点：它是人工排查配置时的入口。
    """
    r = client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    # 诊断字段：这些是 /healthz 刻意不带的
    assert "db_backend" in data
    assert "paypal_mode" in data
    assert "subscribed_events" in data


def test_healthz_and_api_health_are_separate_paths():
    """两个端点必须都在，且路径不冲突。"""
    assert client.get("/healthz").status_code == 200
    assert client.get("/api/health").status_code == 200
    # 页面路由不受影响
    for path in ("/", "/constraints", "/budget", "/approvals", "/audit"):
        assert client.get(path).status_code == 200, f"{path} 应正常"


# ──────────────────────────────────────────────────────
# 3. 保温脚本的 CPU 预算核算
# ──────────────────────────────────────────────────────

def _keep_warm():
    """导入 scripts/keep_warm.py（它在 scripts/ 下，不在包路径里）。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import keep_warm
    return keep_warm


def test_cpu_budget_30min_is_well_under_dod_threshold():
    """30 分钟间隔必须远低于 DoD 的「当天 < 10 CPU 分钟」。

    F1 硬上限是 60 CPU 分钟/天，留 6 倍余量才安全 ——
    因为实例卸载时机不可控，成本是线性放大的。
    """
    kw = _keep_warm()
    est = kw.estimate_cpu_minutes_per_day(30)
    assert est["under_dod_threshold"] is True
    assert est["cpu_minutes_per_day"] < 10.0
    # 占预算应低于 20%（留足余量）
    assert est["pct_of_budget"] < 20.0


def test_cpu_budget_scales_with_interval():
    """间隔越大越省 —— 单调性检查，防止公式写反。"""
    kw = _keep_warm()
    costs = [kw.estimate_cpu_minutes_per_day(iv)["cpu_minutes_per_day"]
             for iv in (10, 30, 60, 120)]
    assert costs == sorted(costs, reverse=True), f"应随间隔增大而下降：{costs}"


def test_cpu_budget_never_exceeds_budget():
    """任何合理间隔都不该超 60 分钟预算。"""
    kw = _keep_warm()
    for iv in (10, 30, 60, 120, 240):
        est = kw.estimate_cpu_minutes_per_day(iv)
        assert est["cpu_minutes_per_day"] < est["budget_minutes"], \
            f"{iv} 分钟间隔不该超预算"


def test_keep_warm_has_no_hardcoded_url():
    """脚本不硬编码任何**真实** URL —— 必须由参数传入。

    理由：URL 里可能带实例专属信息，且部署域名会变。

    断言口径：检查 docstring 里的**用法示例**是占位符（`xxx` / `<你的域名>`），
    而不是检查"完全没有 URL 字符串"—— 因为 `--url https://xxx.azurewebsites.net`
    这种示例是必要的用法说明。
    真正要防的是：某个**具体实例**的域名被写死。
    """
    src = (Path(__file__).resolve().parent.parent / "scripts" / "keep_warm.py").read_text(
        encoding="utf-8")
    import re
    urls = re.findall(r"https?://[A-Za-z0-9][A-Za-z0-9.\-<>\-]*", src)
    for u in urls:
        # 占位符形态：含 xxx / <...> / example 等 → 是用法示例，放行
        is_placeholder = (
            "xxx" in u
            or "<" in u
            or "example" in u
            or "your" in u.lower()
        )
        assert is_placeholder, f"脚本不应硬编码具体 URL：{u}"
    # 部署域名尤其不能出现（哪怕带占位符，主机名部分也不该写死）
    assert "mandatepay.azurewebsites.net" not in src, "不应硬编码部署域名"
    assert "onrender.com" not in src, "不应硬编码部署域名"
    assert "up.railway.app" not in src, "不应硬编码部署域名"


def test_keep_warm_has_no_hardcoded_secret():
    """脚本不硬编码任何密钥。"""
    src = (Path(__file__).resolve().parent.parent / "scripts" / "keep_warm.py").read_text(
        encoding="utf-8")
    for marker in ("sk-", "ghp_", "AKIA", "password", "SECRET"):
        assert marker not in src, f"脚本不应出现 {marker}"


# ──────────────────────────────────────────────────────
# 4. 代理绕过（实测踩过的坑）
# ──────────────────────────────────────────────────────

def _probe_with_fake_opener(kw, monkeypatch, use_proxy: bool):
    """跑一次 _get，捕获它传给 build_opener 的 handlers。

    返回 (handlers, 是否真的发起了请求)。
    _get 会把 open() 的异常转成返回值，所以这里用哨兵异常区分。
    """
    captured = {}

    class _Sentinel(Exception):
        pass

    class _FakeOpener:
        def open(self, req, timeout=None):
            raise _Sentinel()

    def _fake_build_opener(*handlers):
        captured["handlers"] = handlers
        return _FakeOpener()

    monkeypatch.setattr(kw.urllib.request, "build_opener", _fake_build_opener)

    code, elapsed, body = kw._get("http://example.invalid/healthz", timeout=1,
                                  use_proxy=use_proxy)
    return captured.get("handlers") or (), body


def _empty_proxy_handlers(handlers, kw):
    """挑出"禁用代理"的 ProxyHandler —— 即 proxies == {}。"""
    return [h for h in handlers
            if isinstance(h, kw.urllib.request.ProxyHandler) and h.proxies == {}]


def test_probe_bypasses_proxy_by_default(monkeypatch):
    """默认必须绕过环境变量里的代理。

    实测踩过：开发机设了 http_proxy，urllib 默认读它，
    本地探针被代理拦成 502 → 误判成"服务已死"。

    做法：断言 build_opener 被传入了 ProxyHandler({})（= 禁用代理）。
    """
    kw = _keep_warm()

    # 假装环境里有代理
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")

    handlers, body = _probe_with_fake_opener(kw, monkeypatch, use_proxy=False)

    empty_proxies = _empty_proxy_handlers(handlers, kw)
    assert empty_proxies, \
        f"use_proxy=False 时应传入 ProxyHandler({{}}) 禁用代理，实际 handlers={handlers}"
    # 确认它确实读到了环境里的代理（说明这个测试有意义）
    assert empty_proxies[0].proxies == {}


def test_probe_can_opt_in_to_proxy(monkeypatch):
    """显式 use_proxy=True 时应走环境代理（不传禁用代理的 ProxyHandler({})）。"""
    kw = _keep_warm()

    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")

    handlers, body = _probe_with_fake_opener(kw, monkeypatch, use_proxy=True)

    empty_proxies = _empty_proxy_handlers(handlers, kw)
    assert not empty_proxies, \
        "use_proxy=True 时不应禁用代理（不应传 ProxyHandler({})）"
