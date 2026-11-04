# W-08 · `/api/*` 鉴权实现说明

> 结论先行：采用 **cookie session**，前端**一行代码不用改**，演示不受影响。
> 目标不是"做账号体系"，是"挡住裸脚本批量拉数据"。

---

## 一、为什么不用 header 凭据

工单最初设想"所有 `/api/*` 加统一 API key header"。实测这会**直接砸掉演示**：

五个前端页面的 fetch **全部无凭据**（实测 11 处），而页面路由本身不提供数据：

| 页面 | 调用的 API |
|---|---|
| `index.html` | `POST /api/create-order`、`POST /api/capture-order/{orderID}` |
| `constraints.html` | `POST /api/agent/parse`（2 处） |
| `approvals.html` | `GET /api/approvals`、`POST /api/mandates/confirm`、`POST /api/approvals/{trace_id}/reject` |
| `audit.html` | `GET /api/constraints/current`、`GET /api/mandates`、`POST /api/mandates/confirm` |
| `budget.html` | `GET /api/budget/summary` |

header 方案一上 → 页面数据区全空白 + "我已付款·捕获"按钮 401。

**cookie session 方案**：页面路由访问时下发签名 cookie，页面内 fetch 同源自动带上，
**前端零改动**。

---

## 二、豁免清单（关键：不是"凡 /api/* 全拦"）

`app/main.py` 的 `EXEMPT_PATHS`：

| 路径 | 为什么豁免 |
|---|---|
| `/` `/constraints` `/budget` `/approvals` `/audit` | 页面路由 —— 不鉴权评委才打得开 |
| `/healthz` | 外部保温探针不能带凭据（见 `W07_KEEP_WARM.md`） |
| `/api/health` | 诊断端点，不返回交易数据 |
| **`/api/webhook`** | ⚠️ **PayPal 是服务器对服务器调用，不可能带浏览器 cookie** |
| `/favicon.ico` | — |

> **`/api/webhook` 是 W-08 最容易踩的坑**。
> 它自己有 PayPal 官方签名校验（失败返 401）—— 那才是这条路径正确的防护。
> 若误拦成 401，**PayPal 事件永远收不到，mandate 状态不会更新**，
> 演示时表现为"人付了款但状态不变"。
> 回归测试 `test_webhook_is_exempt_from_session_auth` 专门守这一条。

`/static/*` 在中间件里单独放行（页面要能加载 CSS/JS）。

---

## 三、cookie 的结构与防护

```
cookie 名 : mandatepay_session（https 下用 __Host-mandatepay_session）
cookie 值 : <issued_at>.<ua_digest>.<hmac_sha256签名>
```

| 防护 | 做法 | 防什么 |
|---|---|---|
| **防篡改** | HMAC-SHA256 签名（`hmac.compare_digest` 防时序攻击） | 改签发时间/改 UA/伪造 cookie |
| **防重放** | 8 小时有效期（`SESSION_TTL_SECONDS`） | 泄露的 cookie 不会长期可用 |
| **防跨客户端** | 签名覆盖 User-Agent 摘要 | 复制 cookie 到别的浏览器用不了（纵深防御） |
| **防 XSS 窃取** | `HttpOnly` | JS 读不到 cookie |
| **防 CSRF** | `SameSite=Lax` | 跨站请求不带 cookie |
| **防中间人** | https 下用 `__Host-` 前缀 + `Secure` | 浏览器强制 `Path=/` + 禁止 `Domain` |

### 签名密钥从哪来

**不新增必填环境变量**（工单要求部署尽量少动）：

1. 有 `SESSION_SECRET` → 直接用（推荐，Azure 上应显式配这个）
2. 否则有 `PAYPAL_CLIENT_SECRET` → `sha256("mandatepay/session-key/v1" || secret)` 派生
   （它已是部署必填项、稳定、只在服务端用；加了 info 标签做用途隔离）
3. 都没有 → 进程级随机密钥（**重启后旧 cookie 全失效**）

> ⚠️ **Azure 部署建议显式配 `SESSION_SECRET`**。
> 用 `PAYPAL_CLIENT_SECRET` 派生能工作，但语义上把两件事绑在一起了 ——
> 将来轮换 PayPal 凭据会连带让所有会话失效（评委正逛着就掉线）。
> 配一个独立的 `SESSION_SECRET` 就没有这个耦合。

---

## 四、DoD 四条验收（全部实测通过）

| # | 项 | 结果 |
|---|---|---|
| 1a | `curl -i /api/mandates` 无 cookie → **401**；`curl -i /healthz` → **200** | ✅ 实测 401 / 200 |
| 1b | 未登录浏览器打开五页，**数据区正常渲染**（不是空白） | ✅ Chrome headless 实测：`/audit` 渲染出 7 处三层 Mandate 卡片，内容 `Notion Pro × $10.00 (USD)` 完整 |
| 3 | `approvals.html` 的"我已付款·捕获"按钮点击成功（非 401） | ✅ 端点鉴权通过（沙箱未付款时返 422 是**预期的** `PAYER_ACTION_REQUIRED` 行为，不是鉴权问题） |
| 4 | `index.html` 创建订单 / 捕获订单走通一次沙箱闭环 | ✅ 创建订单成功（`95371415GF5274333`），捕获路径通（422 = 未付款，符合设计） |

**额外验证（镜头④相关）**：`audit.html` 拉的 `GET /api/mandates` 正是镜头④要的原始 JSON ——
cookie 下发后浏览器照常拿得到，**镜头④不受鉴权影响**（Chrome headless 实测确认）。

---

## 五、测试（新增 39 条，共 89 全通过）

`tests/test_session_auth.py` 关键几条：

- `test_api_requires_session` —— 6 个 API 端点无 cookie 全 401
- `test_webhook_is_exempt_from_session_auth` —— **守 webhook 豁免**
- `test_api_works_after_page_visit` —— DoD 第 2 条
- `test_tampered_cookie_is_rejected` / `test_tampered_payload_is_rejected` —— 防篡改
- `test_cookie_bound_to_user_agent` —— UA 绑定
- `test_expired_cookie_rejected` / `test_future_issued_at_rejected` —— 时效
- `test_agent_parse_rejected_without_session` —— **证明鉴权在 LLM 调用之前**
  （否则裸脚本能靠打 API 白耗 Z.ai 免费档额度）

### 顺带修掉一个测试基础设施问题

新增 `tests/conftest.py`：把 `DATABASE_URL` 指向**临时 SQLite**
（`sqlite:///<tmpdir>/test.db`）。

**为什么需要**：`/api/agent/parse` 会真跑 LangGraph 状态机，
而 `validate_node` / `approve_node` 会**写库**。
实测不加隔离时：本地开发库被塞进 48 条 mandate + 16 条 purchase_request。

顺带发现并解决的两个测试陷阱：

| 陷阱 | 现象 | 修法 |
|---|---|---|
| `TestClient` 会持久化 cookie | 以为中间件没生效（第二个请求带上了 cookie，返 200 而不是 401） | 每个"裸请求"用**新建**的 client |
| mock 目标搞错 | mock 了 `agent.intent.parse` 但请求仍走真 LLM（82 秒） | `graph.py` 用 `from .intent import parse`，要 mock **`agent.graph.parse`**；且 `insight_node`/`explain_node`/`pay_node` 各还要单独 mock |

**效果**：测试从 112 秒 → **2.4 秒**，且开发库零污染。

---

## 六、已知限制（如实记录）

1. **不做账号体系** —— 工单明确要求。任何人打开页面就拿到会话，
   所以它挡的是"随手写的脚本批量拉数据"，**挡不住**有心的定向爬取。
2. **不做速率限制** —— 拿到 cookie 后仍可高频请求。
3. **UA 绑定可伪造** —— 纵深防御的一层，不是主要防线。
4. **会话不区分用户** —— 所有人共享同一个"已登录"状态。
   本项目是 Demo 且数据是演示数据，可接受。
5. **8 小时硬过期** —— 超过需刷新页面（页面刷新会自动重新签发）。
6. **如果 `SESSION_SECRET` 与 `PAYPAL_CLIENT_SECRET` 都没配** → 进程级随机密钥，
   **重启后所有会话失效**（评委需要刷新页面）。Azure 上务必配前者。

---

## 七、部署时要做的事

Azure App Service → Configuration → Environment variables，加一个：

```
SESSION_SECRET=<随机长字符串>
```

生成方式（任意其一）：

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
# 或
openssl rand -base64 36
```

**不配也能跑**（会退化为从 `PAYPAL_CLIENT_SECRET` 派生），但配了更干净 ——
避免将来轮换 PayPal 凭据时把评委的会话一起搞掉。
