"""pytest 全局fixture —— 测试环境隔离。

为什么要隔离数据库
------------------
W-08 的鉴权测试会真实跑一遍 LangGraph 状态机（`/api/agent/parse`），
而状态机的 `validate_node` / `approve_node` 会**写库**
（`save_request` / `save_constraint`）。

实测后果（不加隔离时）：
  - 本地开发库被塞进 48 条 mandate + 16 条 purchase_request
  - 单个测试文件跑 112 秒（其中 LLM 与 PayPal 调用是主因）

所以这里把 `DATABASE_URL` 指向一个**临时 SQLite 文件**：
`store._dsn()` 留空即走 SQLite（见 `agent/store.py`），自带幂等建表。

注意：`.env` 会被 `app/config.py` 的 `load_dotenv()` 读进来，
所以必须在 `app` 被 import **之前**设好环境变量 —— 这就是本文件
放在 `tests/conftest.py`（而非在测试函数里 setenv）的原因：
conftest.py 在测试模块 import 之前执行。

副作用控制：临时文件在会话结束时删除。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

# ── 必须在 import app.* 之前设好 ─────────────────────────
# `agent/store.py` 的 `_dsn()` 读 DATABASE_URL：
#   - 空串 → 用默认的 mandatepay.db（**不行**，那是开发库）
#   - `sqlite:///<path>` → 用指定路径（见 store._Conn.__init__）
# 所以这里给一个临时 SQLite 文件，测试数据不碰开发库。
# 同时 app/config.py 的 load_dotenv() 会读 .env —— 必须在此之前设好，
# 这就是本文件放在 tests/conftest.py（而非测试函数里 setenv）的原因：
# conftest.py 在测试模块 import 之前执行。
_TMP_DIR = tempfile.mkdtemp(prefix="mandatepay_test_")
_TMP_DB = str(Path(_TMP_DIR) / "test.db").replace("\\", "/")

os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_DB}"
# 让 LLM 相关的测试即便漏了 mock 也不会真发请求（兜底，非主力）
os.environ.setdefault("LLM_API_KEY", "")
# 避免测试期间读取真实 .env 里的 PayPal 凭据
os.environ["PAYPAL_MODE"] = "sandbox"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(scope="session", autouse=True)
def _cleanup_test_db():
    """会话结束删掉临时 SQLite 文件。"""
    yield
    try:
        import shutil

        shutil.rmtree(_TMP_DIR, ignore_errors=True)
    except Exception:
        pass


@pytest.fixture
def fresh_client():
    """每次给一个**不带 cookie** 的 TestClient（模拟裸脚本 / curl）。

    注意：不要复用同一个 client —— httpx 会自动持久化 Set-Cookie，
    第二个请求就带上 cookie 了，鉴权测试会假通过。
    （这个坑踩过：以为中间件没生效，其实是 client 带了 cookie。）
    """
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


@pytest.fixture
def browser_client():
    """给一个**会持久化 cookie** 的 TestClient（模拟浏览器）。

    用法：先访问页面拿 cookie，之后的 API 请求自动带上 —— 模拟评委路径。
    """
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)
