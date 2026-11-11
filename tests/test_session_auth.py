"""会话鉴权的单元测试 —— W-08。

这组测试保护的是**演示不被砸掉**的那条底线：
页面能正常取到数据（200），而裸脚本打 API 被挡（401）。

关键测试点：
1. `/api/*` 无 cookie → 401（工单 DoD 第 1 条）
2. `/healthz` 无 cookie → 200（外部保温探针不能带凭据）
3. **页面路由下发 cookie，且页面内 fetch 能取到数据**（DoD 第 2、3 条）
4. **`/api/webhook` 豁免** —— PayPal 是服务器对服务器调用，带不了 cookie；
   误拦会导致 PayPal 事件永远收不到、mandate 状态不更新
5. 签名防篡改：改一个字节即失效
6. UA 不绑定：换 User-Agent 仍然有效（演示稳定性优先）
7. 过期即失效

运行：python -m pytest tests/ -q
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from app import session  # noqa: E402
from app.main import app  # noqa: E402

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) MandatePayTest/1.0"
OTHER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Different/2.0"


def _fresh_client() -> TestClient:
    """每次新建 client → 不携带任何 cookie（模拟裸脚本/curl）。"""
    return TestClient(app)


def _get_cookie_from(response) -> str:
    """从 Set-Cookie 里取出 `name=value` 部分。"""
    raw = response.headers.get("set-cookie", "")
    if not raw:
        return ""
    return raw.split(";", 1)[0]


# ──────────────────────────────────────────────────────
# 1. DoD 第 1 条：裸请求被挡
# ──────────────────────────────────────────────────────

@pytest.mark.parametrize("method,path", [
    ("GET", "/api/mandates"),
    ("GET", "/api/approvals"),
    ("GET", "/api/budget/summary"),
    ("GET", "/api/constraints/current"),
    ("GET", "/api/catalog"),
    ("GET", "/api/webhook/events"),
])
def test_api_requires_session(method, path):
    """无 cookie 的 API 请求 → 401。

    这正是 DoD 第 1 条要验的：公网暴露 + /api/mandates 返回全部交易数据
    不能被裸脚本批量拉走。
    """
    c = _fresh_client()
    r = c.request(method, path, headers={"user-agent": UA})
    assert r.status_code == 401, f"{method} {path} 应 401，实际 {r.status_code}"
    # 错误信息要能指引人怎么解决（中文"会话"或英文 session 任一即可）
    detail = r.json().get("detail", "")
    assert ("会话" in detail) or ("session" in detail.lower()), \
        f"401 响应应说明需要会话，实际：{detail!r}"


def test_healthz_is_exempt():
    """`/healthz` 不鉴权 —— 外部保温探针不能带凭据（W-07 的脚本要能工作）。"""
    c = _fresh_client()
    r = c.get("/healthz", headers={"user-agent": UA})
    assert r.status_code == 200


def test_api_health_is_exempt():
    """`/api/health` 也豁免 —— 诊断端点，不返回交易数据。"""
    c = _fresh_client()
    r = c.get("/api/health", headers={"user-agent": UA})
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_webhook_is_exempt_from_session_auth():
    """**`/api/webhook` 必须豁免** —— PayPal 是服务器对服务器调用。

    它不可能带浏览器 cookie。若被拦成 401，PayPal 的事件永远收不到，
    mandate 状态就不会更新 —— 演示时"付了款但状态不变"。

    期望：请求能进到业务逻辑（缺签名头 → 400，而不是被鉴权拦成 401）。
    """
    c = _fresh_client()
    r = c.post("/api/webhook", json={}, headers={"user-agent": UA})
    assert r.status_code != 401, "/api/webhook 被误拦了 —— PayPal 事件将永远收不到"
    assert r.status_code == 400, "应该进到业务逻辑（缺签名头 → 400）"


def test_static_files_not_authed():
    """静态资源不鉴权 —— 否则页面加载不了 CSS/JS。"""
    c = _fresh_client()
    r = c.get("/static/budget.html", headers={"user-agent": UA})
    assert r.status_code == 200


# ──────────────────────────────────────────────────────
# 2. DoD 第 2、3 条：页面能正常用
# ──────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "path",
    ["/", "/constraints", "/budget", "/approvals", "/audit", "/paypal/return"],
)
def test_pages_issue_session_cookie(path):
    """页面路由访问时下发会话 cookie。

    `/paypal/return` 在列是硬性要求：它是 PayPal 批准后的回跳页，
    页面内要 POST /api/mandates/confirm 做捕获 —— 买家若在无 cookie 的
    浏览器里走完支付，没有这一步签发就会 401，捕获链路断在这里。
    """
    c = _fresh_client()
    r = c.get(path, headers={"user-agent": UA})
    assert r.status_code == 200
    raw = r.headers.get("set-cookie", "")
    assert raw, f"{path} 应下发 Set-Cookie"
    assert "HttpOnly" in raw, "cookie 应是 HttpOnly（JS 读不到）"
    assert "samesite=lax" in raw.lower(), "cookie 应是 SameSite=Lax（防 CSRF）"
    assert "path=/" in raw.lower(), "cookie 的 Path 应为 /"


@pytest.mark.parametrize(
    "path",
    ["/", "/constraints", "/budget", "/approvals", "/audit", "/paypal/return", "/paypal/cancel"],
)
def test_pages_are_not_authed(path):
    """页面路由本身不鉴权 —— 否则评委打不开页面。"""
    c = _fresh_client()
    r = c.get(path, headers={"user-agent": UA})
    assert r.status_code == 200


def test_api_works_after_page_visit():
    """**DoD 第 2 条的核心**：先访问页面拿 cookie，之后 API 正常返回数据。

    这模拟评委的真实路径：打开页面 → 页面里的 fetch 自动带 cookie → 出数据。
    """
    c = TestClient(app)
    page = c.get("/audit", headers={"user-agent": UA})
    assert page.status_code == 200
    cookie = _get_cookie_from(page)
    assert cookie, "应拿到 cookie"

    # 页面里的 fetch 是同源请求，浏览器自动带 cookie
    r = c.get("/api/mandates", headers={"user-agent": UA, "cookie": cookie})
    assert r.status_code == 200, "带 cookie 后 /api/mandates 应 200"
    assert "items" in r.json()


def test_budget_data_available_after_page_visit():
    """DoD 第 2 条：/budget 的数据区要能正常拿到（不能空白）。"""
    c = TestClient(app)
    page = c.get("/budget", headers={"user-agent": UA})
    cookie = _get_cookie_from(page)
    r = c.get("/api/budget/summary", headers={"user-agent": UA, "cookie": cookie})
    assert r.status_code == 200
    body = r.json()
    # 预算接口应返回结构化数据（字段名以实现为准，检查关键存在性）
    assert isinstance(body, dict)
    assert body, "预算数据不应为空"


def test_constraints_page_then_parse_endpoint(monkeypatch):
    """constraints 页面的 POST /api/agent/parse 也要能用（演示主流程入口）。

    **mock 掉 LLM**：这个测试验的是"鉴权通过后端点能正常处理"，
    不该真去调 Z.ai（实测耗时 122 秒，还撞上过载重试 —— 测试必须离线）。

    ⚠️ mock 的目标是 **`agent.graph.parse`** 而不是 `agent.intent.parse` ——
    `graph.py` 用的是 `from .intent import Intent, parse`（函数直接绑定到
    `agent.graph` 命名空间），monkeypatch `agent.intent.parse` 改不到它。
    踩过这个坑：mock 了但请求仍走了真 LLM，测试跑了 82 秒。

    另外 `insight_node` / `explain_node` **各自还会调一次 LLM**
    （`generate_insight` / `generate_explanation`）—— 只 mock parse 不够，
    实测仍要 63 秒。三处都要 mock。

    还有第四处：`pay_node` 会**真的去 PayPal 建沙箱订单**
    （`from .paypal_tools import create_order as paypal_create_order`），
    实测让本地库多了 48 条 mandate —— 测试有副作用，必须一并 mock。
    """
    from agent import ai_insight
    from agent import graph as graph_mod
    from agent import intent as intent_mod
    from agent.models import PurchaseRequest

    def _fake_parse(text):
        return intent_mod.ParsedIntent(
            intent=intent_mod.Intent.PURCHASE_REQUEST,
            request=PurchaseRequest(
                vendor="Notion", item="Notion Pro", amount_usd=10.0, category="Productivity"
            ),
            note="mocked（离线测试）",
            reasoning="mocked reasoning",
            trace=["query_catalog", "create_purchase_request"],
        )

    def _fake_create_order(item, amount, quantity=1):
        return {
            "order_id": "MOCK-ORDER-0001",
            "status": "PAYER_ACTION_REQUIRED",
            "approval_url": "https://example.invalid/approve",
            "amount": amount,
        }

    monkeypatch.setattr(graph_mod, "parse", _fake_parse)
    # insight / explain 节点各自还会调 LLM —— 一并 mock 掉
    monkeypatch.setattr(ai_insight, "generate_insight", lambda *a, **k: "mocked insight")
    monkeypatch.setattr(ai_insight, "generate_explanation", lambda *a, **k: "mocked explanation")
    # pay 节点会真去 PayPal 建单 —— 必须 mock（否则测试污染数据库 + 慢）
    monkeypatch.setattr(graph_mod, "paypal_create_order", _fake_create_order)

    c = TestClient(app)
    page = c.get("/constraints", headers={"user-agent": UA})
    cookie = _get_cookie_from(page)
    r = c.post(
        "/api/agent/parse",
        json={"text": "买 Notion Pro，10 美元", "session_id": "default"},
        headers={"user-agent": UA, "cookie": cookie, "content-type": "application/json"},
    )
    assert r.status_code == 200, f"带 cookie 的 agent/parse 应 200，实际 {r.status_code}: {r.text[:300]}"


def test_agent_parse_rejected_without_session(monkeypatch):
    """反向验证：同样的请求，**无 cookie** → 401（LLM 根本不该被调用）。

    这条更值钱：它证明鉴权发生在 LLM 调用**之前** ——
    否则裸脚本能靠打 API 白耗我们的 LLM 额度（Z.ai 免费档本来就紧张）。

    mock 目标同样是 `agent.graph.parse`（理由见上一条测试的注释）。
    """
    from agent import graph as graph_mod

    def _boom(*args, **kwargs):
        raise AssertionError("鉴权失败时不应调用 LLM")

    monkeypatch.setattr(graph_mod, "parse", _boom)

    c = _fresh_client()
    r = c.post(
        "/api/agent/parse",
        json={"text": "买 Notion Pro，10 美元", "session_id": "default"},
        headers={"user-agent": UA, "content-type": "application/json"},
    )
    assert r.status_code == 401, "无 cookie 应 401"


# ──────────────────────────────────────────────────────
# 3. 签名防篡改
# ──────────────────────────────────────────────────────

def test_tampered_cookie_is_rejected():
    """改一个字节 → 签名不匹配 → 401。

    这是"cookie session"和"明文 base64 cookie"的本质区别。
    """
    c = TestClient(app)
    page = c.get("/", headers={"user-agent": UA})
    cookie = _get_cookie_from(page)
    name, value = cookie.split("=", 1)

    # 篡改签名末位
    tampered = value[:-1] + ("A" if value[-1] != "A" else "B")
    r = c.get("/api/mandates", headers={"user-agent": UA, "cookie": f"{name}={tampered}"})
    assert r.status_code == 401, "篡改签名的 cookie 必须被拒"


def test_tampered_payload_is_rejected():
    """篡改 payload（改签发时间）→ 签名不匹配 → 401。

    攻击者想把过期 cookie 改新 —— 必须无效。
    """
    c = TestClient(app)
    page = c.get("/", headers={"user-agent": UA})
    cookie = _get_cookie_from(page)
    name, value = cookie.split("=", 1)
    issued_at, sig = value.split(".")

    # 把签发时间改成未来（试图延长有效期）
    forged = f"{int(time.time()) + 99999}.{sig}"
    r = c.get("/api/mandates", headers={"user-agent": UA, "cookie": f"{name}={forged}"})
    assert r.status_code == 401, "改签发时间必须被拒（签名覆盖了 payload）"


def test_garbage_cookie_is_rejected():
    """乱七八糟的 cookie 值 → 401，不应崩溃。"""
    for bad in ("garbage", "a.b.c", "x" * 500, "....", "1.2.3.4.5"):
        c = _fresh_client()
        r = c.get("/api/mandates", headers={"user-agent": UA, "cookie": f"mandatepay_session={bad}"})
        assert r.status_code == 401, f"垃圾 cookie {bad!r} 应 401"


def test_empty_and_missing_cookie_rejected():
    """空 cookie 头 / 无 cookie 头 → 401。"""
    c = _fresh_client()
    assert c.get("/api/mandates", headers={"user-agent": UA}).status_code == 401
    assert c.get("/api/mandates",
                  headers={"user-agent": UA, "cookie": ""}).status_code == 401


# ──────────────────────────────────────────────────────
# 4. UA 不绑定与过期
# ──────────────────────────────────────────────────────

def test_cookie_survives_user_agent_change():
    """**换 User-Agent 后 cookie 仍然有效**。

    曾经把 UA 摘要写进 payload，UA 变了就 401。但 UA 可伪造，本来就不是
    有效防线；而且浏览器升级、代理改写 UA 会让正常用户突然掉登录。
    现在刻意不绑定 UA —— 演示现场"打开就能用"优先。
    """
    c = TestClient(app)
    page = c.get("/", headers={"user-agent": UA})
    cookie = _get_cookie_from(page)

    r = c.get("/api/mandates", headers={"user-agent": OTHER_UA, "cookie": cookie})
    assert r.status_code == 200, "换 User-Agent 后 cookie 应仍然有效"


def test_cookie_works_without_user_agent_header():
    """完全不带 User-Agent 头 → 依然有效（cookie 与 UA 彻底解耦）。"""
    c = TestClient(app)
    page = c.get("/", headers={"user-agent": UA})
    cookie = _get_cookie_from(page)

    r = c.get("/api/mandates", headers={"cookie": cookie})
    assert r.status_code == 200, "不带 UA 头也应通过"


def test_expired_cookie_rejected():
    """过期 cookie → 401。"""
    # 直接构造一个"很久以前签发"的 cookie：签名覆盖签发时间，
    # 所以必须用真实密钥重签 —— 用 session 模块的内部函数做。
    issued_at = int(time.time()) - (session.SESSION_TTL_SECONDS + 3600)
    payload = f"{issued_at}"
    forged = f"{payload}.{session._sign(payload)}"

    c = _fresh_client()
    r = c.get("/api/mandates",
              headers={"user-agent": UA, "cookie": f"mandatepay_session={forged}"})
    assert r.status_code == 401, "过期 cookie 应被拒"


def test_future_issued_at_rejected():
    """签发时间在未来（时钟偏移容忍 5 分钟，超出即拒）→ 401。"""
    issued_at = int(time.time()) + 3600
    payload = f"{issued_at}"
    forged = f"{payload}.{session._sign(payload)}"

    c = _fresh_client()
    r = c.get("/api/mandates",
              headers={"user-agent": UA, "cookie": f"mandatepay_session={forged}"})
    assert r.status_code == 401, "未来签发的 cookie 应被拒"


# ──────────────────────────────────────────────────────
# 5. session 模块自身的单元测试
# ──────────────────────────────────────────────────────

def test_verify_cookie_roundtrip():
    """签发 → 校验 → 通过。"""
    name, value = session.issue_cookie_parts()
    header = f"{name}={value}"
    assert session.verify_cookie(header) is True


def test_verify_cookie_rejects_none():
    """None / 空 → False。"""
    assert session.verify_cookie(None) is False
    assert session.verify_cookie("") is False


def test_issue_cookie_headers():
    """Set-Cookie 字符串含必要属性。"""
    raw = session.issue_cookie()
    assert "HttpOnly" in raw
    assert "SameSite=Lax" in raw
    assert "Path=/" in raw
    assert f"Max-Age={session.SESSION_TTL_SECONDS}" in raw
    assert "Secure" not in raw, "http 环境不该带 Secure"


def test_issue_cookie_secure_flag():
    """https 环境 → 带 Secure 且用 __Host- 前缀。"""
    raw = session.issue_cookie(secure=True)
    assert "Secure" in raw
    assert raw.startswith("__Host-mandatepay_session="), \
        "https 下应使用 __Host- 前缀（浏览器强制 Path=/ + 无 Domain）"


def test_secure_cookie_verifies():
    """__Host- 前缀的 cookie 也能正常校验。"""
    name, value = session.issue_cookie_parts(secure=True)
    assert session.verify_cookie(f"{name}={value}") is True


def test_secret_is_not_empty():
    """签名密钥必须非空。"""
    assert session._SECRET, "签名密钥不能为空"
    assert len(session._SECRET) >= 16, "签名密钥太短"


def test_session_module_has_no_hardcoded_secret():
    """session 模块不硬编码任何密钥。"""
    src = (Path(__file__).resolve().parent.parent / "app" / "session.py").read_text(
        encoding="utf-8")
    for marker in ("sk-", "ghp_", "AKIA"):
        assert marker not in src, f"session.py 不应出现 {marker}"


# ──────────────────────────────────────────────────────
# 6. 豁免清单本身的完整性
# ──────────────────────────────────────────────────────

def test_exempt_list_contains_critical_paths():
    """豁免清单必须包含这些路径 —— 少一个就会砸掉演示。"""
    from app.main import EXEMPT_PATHS

    # 页面路由（评委要能打开）
    for p in ("/", "/constraints", "/budget", "/approvals", "/audit"):
        assert p in EXEMPT_PATHS, f"{p} 必须豁免（否则评委打不开）"
    # 探针（保温脚本不能带凭据）
    assert "/healthz" in EXEMPT_PATHS, "/healthz 必须豁免（W-07 探针要能用）"
    # PayPal webhook（服务器对服务器）
    assert "/api/webhook" in EXEMPT_PATHS, "/api/webhook 必须豁免（PayPal 事件会丢）"
