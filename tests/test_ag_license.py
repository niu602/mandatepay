"""AG Studio license 注入路径的契约测试。

要保护的三条：
1. `/api/ag-license` **受 cookie 会话鉴权保护**（不在 EXEMPT_PATHS 里）
2. key 为空时 → 前端**跳过** setLicenseKey，且不产生任何噪音
3. setLicenseKey 抛异常 → **绝不影响**后续渲染与回退

外加：端点**不泄露**除 key 之外的任何东西（它会返回真实 key，
所以必须靠鉴权挡住裸请求）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import main as app_main  # noqa: E402


@pytest.fixture
def client():
    return TestClient(app_main.app)


# ──────────────────────────────────────────────────────
# 1. 鉴权
# ──────────────────────────────────────────────────────

def test_ag_license_requires_session(client):
    """裸请求（无 cookie）必须 401 —— 端点会返回真实 key，不能裸奔。"""
    r = client.get("/api/ag-license")
    assert r.status_code == 401, f"期望 401，实际 {r.status_code}"


def test_ag_license_not_in_exempt_paths():
    """端点绝不能被加进豁免清单。"""
    assert "/api/ag-license" not in app_main.EXEMPT_PATHS, (
        "/api/ag-license 必须受会话鉴权保护 —— 它返回的是真实 license key"
    )


def test_ag_license_returns_key_with_session(client):
    """带 cookie → 200，且响应体只有 key 字段。"""
    client.get("/")            # 先访问页面拿 cookie
    r = client.get("/api/ag-license")
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {"key"}, f"响应体只应有 key，实际 {body.keys()}"
    assert isinstance(body["key"], str)


# ──────────────────────────────────────────────────────
# 2. key 的来源
# ──────────────────────────────────────────────────────

def test_ag_license_reads_env_var(monkeypatch, client):
    """端点读 AG_STUDIO_LICENSE 环境变量。"""
    monkeypatch.setenv("AG_STUDIO_LICENSE", "FAKE-KEY-FOR-TEST")
    client.get("/")
    r = client.get("/api/ag-license")
    assert r.json()["key"] == "FAKE-KEY-FOR-TEST"


def test_ag_license_empty_when_unset(monkeypatch, client):
    """没设环境变量 → 空字符串（前端据此跳过，行为与改动前一致）。"""
    monkeypatch.delenv("AG_STUDIO_LICENSE", raising=False)
    client.get("/")
    r = client.get("/api/ag-license")
    assert r.json()["key"] == ""


# ──────────────────────────────────────────────────────
# 3. 前端契约（静态检查 budget.html）
# ──────────────────────────────────────────────────────

BUDGET = ROOT / "static" / "budget.html"


def _budget_src() -> str:
    return BUDGET.read_text(encoding="utf-8")


def test_license_fetch_precedes_create_studio():
    """setLicenseKey 必须在 createStudio **之前** —— AG Studio 初始化时才检查授权。

    顺序反了的话，key 会在授权检查之后才设，等于没设。
    """
    src = _budget_src()
    i_fetch = src.find("fetch('/api/ag-license'")
    i_create = src.find("createStudio(document.getElementById('studio')")
    assert i_fetch != -1, "budget.html 里找不到 fetch('/api/ag-license')"
    assert i_create != -1, "budget.html 里找不到 createStudio 调用"
    assert i_fetch < i_create, (
        f"license fetch (位置 {i_fetch}) 必须在 createStudio (位置 {i_create}) 之前"
    )


def test_license_block_is_wrapped_in_try_catch():
    """整段必须包在 try/catch 里 —— 失败不能影响渲染。

    注意结构上有一层嵌套：
        try {                          ← 外层：整个 AG Studio 启动
          const ctrl = new AbortController(); ...
          try {                        ← 内层：只包 fetch（配 finally 清 timer）
            licRes = await fetch('/api/ag-license', ...);
          } finally { clearTimeout(timer); }
          ...
        } catch (e) { ... }            ← 外层 catch
    所以这里匹配的是「包含 fetch 的那个 try ... catch」结构。
    """
    src = _budget_src()
    assert "fetch('/api/ag-license'" in src, "找不到 license fetch"
    m = re.search(
        r"try\s*\{(.*?fetch\('/api/ag-license'.*?)\}\s*catch\s*\(e\)\s*\{",
        src,
        re.S,
    )
    assert m, "包含 fetch 的 try 块必须有对应的 catch"
    body = m.group(1)
    assert "fetch('/api/ag-license'" in body
    assert "console.warn" in body, "catch 里应有 console.warn（只警告，不中断）"


def test_license_failure_does_not_throw():
    """catch 里不能有 throw / 未捕获的 Promise。"""
    src = _budget_src()
    m = re.search(
        r"fetch\('/api/ag-license'.*?catch\s*\(e\)\s*\{(.*?)\n\s{6}\}", src, re.S
    )
    assert m, "找不到 license 段的 catch 块"
    body = m.group(1)
    assert "throw" not in body, "catch 块里不能 throw —— 会破坏渲染"
    assert "console.warn" in body, "catch 块应只 console.warn"


def test_license_fetch_has_timeout():
    """fetch 必须有超时 —— 它是 await 的，挂住会拖住整个渲染。"""
    src = _budget_src()
    i = src.find("fetch('/api/ag-license'")
    assert i != -1
    window = src[max(0, i - 700):i + 300]
    assert "AbortController" in window or "signal" in window, (
        "license fetch 应带超时（AbortController），否则请求挂住会拖住渲染"
    )


def test_set_license_key_guarded():
    """调 setLicenseKey 前要确认它存在（不同版本导出可能不同）。"""
    src = _budget_src()
    assert "AgStudioLicenseManager" in src, "应通过 AgStudioLicenseManager 设置 key"
    assert "setLicenseKey" in src, "应调用 setLicenseKey"
    # 必须有存在性检查（可选链或 typeof 判断）
    assert (
        "typeof" in src and "setLicenseKey" in src
    ), "调用 setLicenseKey 前应做存在性检查（typeof === 'function'）"


def test_key_never_hardcoded_in_frontend():
    """前端绝不能硬编码 key —— 那等于发给每个访客 + 进 git 历史。"""
    src = _budget_src()
    # 只允许出现环境变量名与端点路径，不允许出现长串 key 形态
    assert "AG_STUDIO_LICENSE=" not in src, "前端不应出现 AG_STUDIO_LICENSE= 赋值"
    assert not re.search(r"['\"][A-Za-z0-9_\-]{24,}['\"]", src), (
        "前端疑似硬编码了一串长凭据 —— key 只能走后端端点下发"
    )


def test_env_example_has_placeholder():
    """`.env.example` 必须有空占位（这样要填的人知道有这回事）。"""
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "AG_STUDIO_LICENSE" in text, ".env.example 应包含 AG_STUDIO_LICENSE"
    # 必须是空值占位，不能带任何真实 key
    for line in text.splitlines():
        if line.startswith("AG_STUDIO_LICENSE"):
            assert line.strip() == "AG_STUDIO_LICENSE=", (
                f"占位符必须是空值，实际：{line!r}"
            )


def test_deploy_env_generator_has_placeholder():
    """部署清单生成器也要有空占位 —— 免得评委/用户不知道有这个变量。"""
    text = (ROOT / "scripts" / "gen_deploy_env.py").read_text(encoding="utf-8")
    assert '"AG_STUDIO_LICENSE": ""' in text, (
        "gen_deploy_env.py 的 FIXED 里应有空的 AG_STUDIO_LICENSE 占位"
    )
