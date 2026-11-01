# MandatePay — PayPal AI Hackathon 2026

> **让 AI 能安全地替人花钱。**
>
> 当前 AI 能写代码、写邮件、订会议，但没人敢给 AI 付款权限——因为钱出去了回不来。
> MandatePay 给 AI 装了一个"花钱的笼子"：用户设定约束（月度预算、供应商白名单、单笔阈值），Agent 在约束内自主执行采购，每笔交易生成可审计的三层 Mandate 记录（Intent / Cart / Payment）。

## 目标奖项

**Best Use of Agentic Commerce**（$5,000）

## 技术栈

| 层 | 选型 | 状态 |
|---|---|---|
| Agent | LangGraph（七节点状态机，含 2 个 AI 决策节点） | ✅ 已跑通 |
| 后端 | FastAPI + Python | ✅ 已跑通 |
| 支付 | PayPal Orders v2 REST（Web 流）+ Agent Toolkit（Agent 流） | ✅ **两条均已跑通** |
| 模型 | **任意 OpenAI 兼容端点**（此处用 Z.ai `glm-4.7-flash`，海外可注册） | ✅ **已实测**：function calling 20 句 × 3 次全对 + 决策解释/消费洞察实时产出 |
| 数据库 | **PostgreSQL 16**（`DATABASE_URL` 驱动，留空即回退 SQLite） | ✅ 已切换并回归 |
| 前端 | `static/` 原生 HTML（支付页 + 审计页） | ✅ 已可用 |
| MCP | @paypal/mcp | ⏳ P1 可选，冲奖用 |

> **数据库**：通过 `DATABASE_URL` 切换后端，业务代码零改动。
> 填 `postgresql://...` 走 PostgreSQL，留空走 SQLite。
> 建表 `python scripts/init_db.py`，迁移 `python scripts/migrate_sqlite_to_pg.py`。详见 [WEEK1_REVIEW.md](WEEK1_REVIEW.md) §四。

## 目录结构

```
paypal-ai-hackathon/
├── app/
│   ├── config.py          # 读取 .env（凭证、沙箱/生产切换）
│   ├── paypal_client.py   # PayPal REST API（token / 创建订单 / 捕获）— Web 流
│   └── main.py            # FastAPI：支付页 + 审计页 + 全部 API
├── agent/                 # ✅ LangGraph Agent 层（已建）
│   ├── models.py          # Constraint / PurchaseRequest / MandateRecord
│   ├── constraints.py     # 约束引擎（纯函数，可单测）
│   ├── intent.py          # 意图解析入口（LLM 优先，规则兜底）
│   ├── llm_intent.py      # LLM function calling 解析器（OpenAI 协议兼容，可换模型）
│   ├── paypal_tools.py    # PayPal Agent Toolkit 薄封装 — Agent 流
│   ├── graph.py           # 七节点状态机 parse→validate→insight→explain→approve→pay→verify
│   ├── store.py           # 持久化（双后端：PostgreSQL / SQLite）
│   ├── e2e_check.py       # 支付闭环校验脚本
│   └── demo.py            # 四场景端到端演示
├── scripts/
│   ├── init_db.py                # 幂等建表（按后端自动选 schema）
│   ├── migrate_sqlite_to_pg.py   # SQLite → PostgreSQL 数据迁移
│   ├── setup_webhook.py          # REST API 管理 Webhook 订阅
│   ├── verify_llm_zai.py         # 意图解析验证（20 句断言集 + 降级统计 + 断点续跑）
│   └── scan_secrets.py           # 宽口径密钥扫描（工作树 + 全历史）
├── static/
│   ├── index.html         # PayPal JS SDK 支付按钮页（Web 流）
│   └── audit.html         # 三层 Mandate 审计页 + 人工确认入口
├── mandatepay.db          # SQLite 备份（已 gitignore）
├── WEEK1_REVIEW.md        # 第 1 周复盘（D1-D7）
├── WEEK2_REVIEW.md        # 第 2 周复盘（D8-D14）
├── W05_ZAI_MIGRATION.md   # Z.ai 接入 + 意图解析重测（含 429 应对）
├── LICENSE                # MIT（Copyright 2026 Niu Zisen）
├── .env / .env.example
└── README.md
```

## 已验证状态

### ✅ Web 支付流（REST，完整闭环）
- 订单 `6S3278047H2709334` = COMPLETED
- 捕获 `58M77740LJ042791Y` = COMPLETED
- 金额 $10.00 USD

链路：前端点 PayPal 按钮 → `POST /api/create-order` → 沙箱弹窗买家登录付款 → `onApprove` → `POST /api/capture-order/{id}`

### ✅ Agent 支付流（Agent Toolkit，真实下单）
Agent 通过自然语言/结构化请求 → 约束校验 → 生成三层 Mandate → 调用官方 Toolkit 创建**真实沙箱订单**。

实测订单：`84L147677P5353009`、`0BX879395G816715L`、`8PR16919UM813823D` 等，均返回 `PAYER_ACTION_REQUIRED` + approval_url。

### ✅ 三场景端到端
| 场景 | 决策 | Mandate 状态 |
|---|---|---|
| Notion Pro $10（白名单内、低于阈值） | `AUTO_APPROVE` | APPROVED |
| Figma 企业版 $80（白名单内、超阈值） | `REQUIRE_HUMAN` | PENDING_HUMAN |
| Slack Pro $15（不在白名单） | `REJECT` | REJECTED |

## `PAYER_ACTION_REQUIRED` 不是 bug（产品决策）

Agent 创建订单后状态为 `PAYER_ACTION_REQUIRED`，必须人去 `approval_url` 登录付款后才能 capture。
**Agent 能自主下单，但最后一公里要人点。这是安全设计，不改，不做浏览器自动化。**

理由：
1. MandatePay 的命题就是"AI 不能偷偷花钱" —— 全自动扣款与核心叙事直接冲突；
2. PayPal 的 agentic commerce 范式本身就是"人预先授权范围 + Agent 在范围内行动"；
3. 评委看的是 agentic commerce 的**交互安全**，不是自动化程度。

正确做法：**Demo 走真实流程录屏，旁白强调"人确认"是安全设计 —— 这正是 MandatePay 的命题所在。**
前端提供"去确认付款"+"我已付款·捕获"按钮（**人授权 + 机器执行**），这才是 agentic commerce 的正确模式。

### 为什么不做浏览器自动化（三个不稳定因素，准确口径）

1. **死路归因（不是"卡路径"）**：是 **guest + 非测试邮箱**触发了"创建账户"流程。
   10-22 实测用 `niu602@outlook.com`（非测试邮箱）走 guest 付款，PayPal 把该邮箱当"新买家"，
   要求依次填 密码 → 出生日期 → 身份证件 → 邮编 → 地址验证 → 短信验证码；
   沙箱不真发短信、开发者后台收件箱也收不到，所以是死路。
   **正确做法：用测试买家账号 `sb-mtet153479750@personal.example.com` 直接余额付款，不会触发创建账户流程。**

2. **沙箱 genericError（"系统暂时无响应"）**：偶发、可重试；或先在浏览器单独登录沙箱买家账号
   （sandbox.paypal.com），再发起付款弹窗，即可直接进入付款确认页。**不是死路，不要写"必死"。**

3. **弹窗自动化（不是"抓不到"）**：CDP `Target.getTargets` **可以枚举到**弹窗的 page 目标
   （说"抓不到"不准确），但接管成本高；且弹窗**开合本身不稳定** —— 10-22 实测点击后未开独立窗口，
   回退为页内"没有找到安全的 PayPal 浏览器？帮助重启窗口完成付款"恢复弹层。
   **不要依赖自动化接管弹窗。**

> **关于"沙箱收件箱为空"的严谨性**：10-22 只查了沙箱账号"邮件通知"这一个 dashboard 入口，
> 没有穷举所有代码级取件渠道。这是**主动放弃**，不是**不可能证明**。
> 因此本项目不写"沙箱无法读取邮件"，而是选择测试买家账号余额付款路径，不依赖邮件回调。

## 混合闭环校验脚本

不要写成"全自动"或"纯人工"的二选一。正确写法是**混合闭环**：

> 自动化驱动可控部分（打开页面 / 点击按钮 / 登录测试买家）
> + 人点击最终"完成购买"
> + 后端调用 `capture-order`
> + 用 PayPal API 断言 `COMPLETED`

这条链路已沉淀为可复用的 API 测试脚本 `create → simulate approve → capture → assert COMPLETED`，
而不是靠浏览器自动化硬跑。

```bash
# 1. 创建订单，拿到 approval_url
python -m agent.e2e_check --create --amount 10 --item "Notion Pro"

# 2. 用测试买家账号 sb-mtet153479750@personal.example.com 在 approval_url 完成付款
#    （不要用 guest + 非测试邮箱，会触发创建账户 → 短信验证码死路）

# 3. 捕获并断言 COMPLETED
python -m agent.e2e_check --capture <ORDER_ID>
```

**另有 `--auto` 全自动模式（仅回归测试用，不是产品流程）**：

用沙箱测试卡在 **API 层**完成付款 —— PayPal 创建订单时即以测试卡扣款，返回 status 就是 `COMPLETED`，
不需要人去 approval_url，也不需要再 capture。这正是方法论里 "simulate approve" 的 API 化实现，
让 `create → simulate approve → capture → assert COMPLETED` 能一条命令跑完：

```bash
python -m agent.e2e_check --auto --amount 7.5
# ✅ 闭环成功 —— order=97108519MW170051J capture=2R045877YM6842912

# 实现要点：请求体带 payment_source.card（沙箱 VISA 测试卡 4333134069790191），
# 且必须带 PayPal-Request-Id 幂等头，否则返回 PAYPAL_REQUEST_ID_REQUIRED (400)。
```

> ⚠️ 区分清楚：`--auto` **只用于回归测试**，证明支付链路是通的；
> 产品主流程与 Demo 仍然走「Agent 下单 + 人确认」，不做浏览器自动化。

## 快速开始

```bash
cd D:\paypal-ai-hackathon
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 数据库（PostgreSQL；跳过则用 SQLite，无需额外配置）
createdb -U postgres -h 127.0.0.1 mandatepay
python scripts/init_db.py

# 起服务
python -m uvicorn app.main:app --port 8888
```

- `http://127.0.0.1:8888/` —— PayPal 支付测试页（Web 流）
- `http://127.0.0.1:8888/constraints` —— 约束设定页（自然语言设约束 + 结构化规则 + 消费洞察/决策解释演示）
- `http://127.0.0.1:8888/audit` —— 三层 Mandate 审计页 + 人工确认入口

### LLM 配置：任何 OpenAI 兼容端点可用

意图解析走标准 **OpenAI 协议**，因此**任何 OpenAI 兼容端点都能直接接入**，
只需改 `.env` 三个变量，业务代码零改动：

```bash
# .env
LLM_API_KEY=sk-xxxxxxxxxxxx          # 占位符：请填你自己的 key，勿提交 .env
LLM_BASE_URL=https://api.z.ai/api/paas/v4   # 示例：Z.ai（海外可注册，免费档可用）
LLM_MODEL=glm-4.7-flash              # 示例：Z.ai 的模型名
```

换成其他端点只改 `LLM_BASE_URL` / `LLM_MODEL` 即可，例如：

| 端点 | `LLM_BASE_URL` | `LLM_MODEL` |
|---|---|---|
| Z.ai（示例，海外可注册） | `https://api.z.ai/api/paas/v4` | `glm-4.7-flash` |
| 腾讯 MaaS | `https://tokenhub.tencentmaas.com/v1` | `glm-5.3-flash` |
| DeepSeek | `https://api.deepseek.com` | `deepseek-chat` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` |

可选配置：

- `LLM_THINKING=disabled` —— 关闭 GLM 系列的思维链（`reasoning_tokens` 归零）。
  通过 OpenAI SDK 的 `extra_body` 透传 `"thinking": {"type": "disabled"}`，
  **只有支持该字段的端点才设**，否则会报 400（默认不设，换任何端点都不炸）。
  实测：thinking 开关**不影响判定质量**（两组均 20/20 全对），
  但 function calling 场景下总 token 差别不大（详见 `W05_ZAI_MIGRATION.md` §5.2）。
- `LLM_TIMEOUT`（默认 60 秒）—— 单次请求超时。
- `LLM_RETRY_DELAY`（默认 8 秒）/ `LLM_RETRY_MAX`（默认 6 次）—— 过载退避重试。
- `LLM_MIN_INTERVAL`（默认 4 秒）—— 两次调用之间的最小间隔（客户端限速）。

> **不配也能跑**：LLM 调用失败会自动降级到规则解析，demo 不受影响。
> 但降级是**可观测的** —— 见下方「降级统计」，避免"AI 路径没生效却显示验证通过"。

### 免费档的 429 是常态（真实工程问题，已处理）

免费额度端点（如 Z.ai）会随机返回 `429`，错误码有两种：
`1305`（服务过载）与 `1302`（请求速率超限）。
实测过载高峰时成功率仅约 20%——**这不是代码 bug，是端点侧的容量问题**。

处理方式（三层）：

1. **客户端限速**（`LLM_MIN_INTERVAL`）—— 主动把请求摊开，比被动重试有效；
   密集重试会持续触发 `1302`，把退避变成"越退越堵"。
2. **指数退避 + 随机抖动** —— 第 n 次重试等 `8 × 2^(n-1)` 秒（上限 45 秒），
   叠加 ±30% 抖动，避免所有并发重试在同一秒打回去。
3. **降级可观测** —— `agent/llm_intent.py` 维护 `_STATS` 计数器
   （`calls` / `success` / `rate_limited` / `retries` / `degraded` / `no_tool_call`），
   验证脚本据此报告降级次数，**防止"AI 路径没生效但验证显示通过"的假绿**。

```python
from agent.llm_intent import llm_stats
print(llm_stats())   # {'calls': 60, 'success': 58, 'rate_limited': 9, 'retries': 9, ...}
```

### 意图解析验证（20 句断言集）

```bash
python scripts/verify_llm_zai.py --repeat 3 --thinking disabled --md LLM_INTENT_VERIFICATION.md
```

- 20 句覆盖 AUTO_APPROVE / REQUIRE_HUMAN / REJECT 三场景 + 阈值边界 + 推理（查目录比价 / 品类消歧）
- **期望结果人工枚举并固化在脚本的 `CASES` 表**（非模型自评、非运行后回填）
- 判定直接复用生产引擎 `agent/constraints.py` 的 `evaluate()`，避免"测试逻辑与生产逻辑不一致"
- 固定约束 + 固定花费快照（不读 DB）→ 结果可复现
- 逐句落盘 `.verify_state.json`，免费档过载时可断点续跑

最新结果：**60/60 正确、20/20 三次一致、降级 0 次** →
[LLM_INTENT_VERIFICATION.md](LLM_INTENT_VERIFICATION.md)

## 部署（Neon + Render，全免费）

```bash
# 1. Neon：console.neon.tech 建免费项目（Region 选 AWS US West (Oregon)），复制连接串
# 2. Render：render.com → New → Blueprint → 选本仓库 → 填环境变量 → Apply
# 3. 部署后初始化（建表 + 种子数据 + 清场）
bash scripts/deploy_init.sh
```

| 组件 | 选型 | 为什么 |
|---|---|---|
| 数据库 | **Neon** 免费 PostgreSQL | 只休眠不删库，评审期（12-01~12-15）安全 |
| 应用 | **Render** 免费 Web Service | 自动从 GitHub 构建，`render.yaml` 声明式配置 |

> 不用 Render 免费 PostgreSQL（**创建 30 天后删除**，正落在评审期内）；
> 不用 Supabase 免费层（**7 天无活动即暂停**，需手动恢复）。

必填环境变量（`render.yaml` 里其余已配好）：

```bash
DATABASE_URL=postgresql://user:password@ep-xxx.us-west-2.aws.neon.tech/mandatepay?sslmode=require
PAYPAL_CLIENT_ID=...        # PayPal 沙箱
PAYPAL_CLIENT_SECRET=...
PAYPAL_WEBHOOK_ID=...
LLM_API_KEY=...             # 任意 OpenAI 兼容端点
```

细节、验收清单、冷启动记录、已知限制见 **[W06_DEPLOY.md](W06_DEPLOY.md)**。

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 支付页（Web 流） |
| GET | `/constraints` | 约束设定页 |
| GET | `/budget` | 预算仪表盘（已承诺/剩余/月末预测/供应商分布） |
| GET | `/approvals` | 采购审批台（列表 + 审批弹窗） |
| GET | `/audit` | 审计页（三层 Mandate + 交易粒度分页） |
| POST | `/api/create-order` | 创建订单（Web 流） |
| POST | `/api/capture-order/{id}` | 捕获订单（Web 流） |
| POST | `/api/webhook` | PayPal 回调（**已做官方签名校验**） |
| POST | `/api/agent/constraints` | 设定约束 |
| POST | `/api/agent/parse` | 自然语言 → 七节点全流程（返回 `insight` 消费洞察 + `explanation` 决策解释） |
| POST | `/api/agent/requests` | 提交采购（校验→Mandate→真实下单） |
| POST | `/api/mandates/confirm` | 人付款后捕获并置 COMPLETED |
| GET | `/api/mandates` | 三层 Mandate 审计日志 |
| GET | `/api/constraints/current` | 当前生效约束 |
| GET | `/api/catalog` | 供应商目录 / SaaS 订阅计划 |
| GET | `/api/webhook/events` | Webhook 事件审计日志 |
| GET | `/api/health` | 健康检查（含 `db_backend`、webhook 配置状态） |

## AI 决策参与点（本周新增）

状态机从五节点扩到**七节点**，新增两个由 LLM 驱动的节点，让 AI 不只在入口"听一句话"，而是参与每一次决策：

| 节点 | 作用 | 产出示例 |
|---|---|---|
| `insight_node` | 每笔采购前结合本月已花费/预算，生成消费洞察 | 「本月已花 $170/$200，这笔 $10 会通过，但只剩 $20 余额」 |
| `explain_node` | 把约束判定翻译成可行动建议（擅长解释"为什么被拒"） | 「超出单笔阈值 $30，建议提高阈值或拆分订单」 |

两者均有**纯模板兜底**（无 Key / 调用失败也返回可读文案），保证 demo 永不断链。

## Webhook（D6 已完成）

### 订阅的 4 个事件

```
PAYMENT.CAPTURE.COMPLETED   ← 钱真正到账，MandatePay 的闭环终点
CHECKOUT.ORDER.APPROVED     ← 买家批准订单（尚未扣款）
PAYMENT.CAPTURE.DENIED      ← 扣款被拒
PAYMENT.CAPTURE.PENDING     ← 扣款挂起
```

### 签名校验（已实现）

`/api/webhook` 按官方方式校验 `PAYPAL-TRANSMISSION-ID` / `-SIG` / `-TIME` / `-CERT-URL` / `-AUTH-ALGO` 五个头
+ `PAYPAL_WEBHOOK_ID`，调 `POST /v1/notifications/verify-webhook-signature` 验签。**验签通过才处理事件**。

| 状态码 | 含义 |
|---|---|
| 200 | 已接收并处理（重复事件返回 `duplicate: true`） |
| 400 | 缺少 transmission-* 头 / body 非 JSON |
| 401 | 签名校验失败（疑似伪造请求） |
| 500 | 服务端未配置 `PAYPAL_WEBHOOK_ID` |
| 503 | 无法连通 PayPal 验签接口 |

事件按 `event_id` 幂等入库（`webhook_events` 表），`PAYMENT.CAPTURE.COMPLETED` 会把对应 mandate 置为 `COMPLETED`。

### 本地联调步骤

```bash
# 1) 起服务（显式绑 IPv4，端口 8888）
python -m uvicorn app.main:app --host 127.0.0.1 --port 8888

# 2) 起 ngrok 隧道 —— 两个坑，缺一不可：
#    坑A：开着 VPN 时 http_proxy/https_proxy 存在，ngrok 会走代理 → ERR_NGROK_9009（代理是付费功能）
#    坑B：只写 `ngrok http 8888` 会解析成 [::1]:8888（IPv6），而服务只听 IPv4 → ERR_NGROK_8012
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
    ngrok http 127.0.0.1:8888
# 隧道地址从 ngrok 控制台取：http://127.0.0.1:4040/api/tunnels
# 形如 https://dealer-rover-praying.ngrok-free.dev

# 3) 注册 Webhook 并订阅 4 个事件（无需手点 Dashboard）
python scripts/setup_webhook.py --create --url https://<你的隧道域名>/api/webhook --force
# 脚本会把返回的 webhook id 自动写进 .env 的 PAYPAL_WEBHOOK_ID

# 4) 重启服务后确认
curl http://127.0.0.1:8888/api/health      # webhook_id_configured 应为 true

# 5) 验证隧道真的转发到服务（必须返回我们的 JSON，而不是 ngrok 的 HTML 错误页）
curl -H "ngrok-skip-browser-warning: true" https://<你的隧道域名>/api/health
# 期望：{"status":"ok",...,"db_backend":"postgresql"}

# 6) 终极验证：让 PayPal 真实投递一个事件（带真签名，走完整链路）
python scripts/verify_webhook.py --info
python scripts/verify_webhook.py --simulate PAYMENT.CAPTURE.COMPLETED
# 期望：本地 webhook_events 表出现该 event_id，且 verified=1
# 若只想改 URL 而不重建（webhook id 保持不变）：
#   python scripts/verify_webhook.py --set-url https://<新域名>/api/webhook

# 7) 可选：本地跳过验签跑通事件处理逻辑（仅开发用，PAYPAL_MODE=live 时强制失效）
PAYPAL_WEBHOOK_SKIP_VERIFY=true python -m uvicorn app.main:app --host 127.0.0.1 --port 8888
```

> ⚠️ **隧道 URL 每次启动会变**（免费版随机子域名）。重启隧道后必须重注册：
> `python scripts/setup_webhook.py --create --url <新URL> --force`
>
> **本项目已用固定域名**：隧道以 `--domain=dealer-rover-praying.ngrok-free.dev` 启动，
> URL 不再变化，因此 **webhook 只需注册一次，重启隧道后无需重注册**。
> 换机器或换域名时才需要 `python scripts/verify_webhook.py --set-url <新URL>`（PATCH，保留 webhook id）。
>
> **首次配置 ngrok**：`ngrok config add-authtoken <token>`（token 放 `.env` 的 `NGROK_AUTHTOKEN`）。
> 注意 winget 装的 ngrok 是 3.3.1，太旧会被拒（`ERR_NGROK_121`，要求 ≥3.20.0），先跑一次 `ngrok update`。
>
> **simulate-event 的坑**：正确路径是 `POST /v1/notifications/simulate-event`，`webhook_id` 放在 body 里。
> 写成 `/v1/notifications/webhooks/{id}/simulate-event` 会返回 404（空 body）。

## 两条支付路径（重要，不要互相替代）

| 路径 | 场景 | 实现 |
|---|---|---|
| **REST 路径** | 用户点击 PayPal 按钮 → 弹窗付款 → 服务端捕获 | `app/paypal_client.py` + JS SDK。**已实测跑通完整交易** |
| **Agent Toolkit 路径** | LangGraph Agent 自主调用支付 | `agent/paypal_tools.py`（官方 Toolkit）。**已实测创建真实订单** |

REST 路径已验证，绝不替换；两条并存向评委展示"PayPal 集成覆盖两种范式"。

> ⚠️ Toolkit 的 `create_order` 参数格式与 REST **不同**：
> `{currency_code, items:[{name, item_cost, item_total, quantity}]}`，
> 不是 REST 的 `purchase_units[].amount.value`。按 REST 写法照抄会报错。

## 沙箱测试

- 买家（Personal）：developer.paypal.com → Testing Tools → Sandbox Accounts
  - 本项目使用的测试买家：**`sb-mtet153479750@personal.example.com`**
- 沙箱测试卡：VISA `4333134069790191`，有效期 `11/31`，CSC 任意 3 位
- ⚠️ **必须走测试买家账号登录、余额付款**，不要走 guest 付款。
  死路归因：**guest + 非测试邮箱**会被 PayPal 判定为"新买家"并进入创建账户流程
  （密码 → 出生日期 → 身份证件 → 邮编 → 地址验证 → 短信验证码），沙箱不真发短信
  且开发者后台收件箱收不到 → 死路。测试买家账号余额付款不触发该流程。
- ℹ️ 登录偶发"系统暂时无响应"（genericError）：重试即可；或先在浏览器单独登录沙箱买家账号
  再发起付款弹窗。**可解，不是死路。**

## 待办

- [x] 沙箱支付闭环跑通（REST）
- [x] LangGraph 七节点状态机（新增 insight / explain 两个 AI 决策节点）
- [x] **AI 推理链路**：`query_catalog` 多轮 tool loop（比较/消歧/查价，非分类器）
- [x] 约束引擎（滚动预算/双白名单/单笔+日累计双阈值）+ **单元测试 30 例**（`python -m pytest tests/`）
- [x] **约束压测**：50 约束 × 13 请求 = 650 次判定，三份独立参照实现交叉验证
      （`STRESS_REPORT.md`）；发现并修复 4 类误拒/漏放，修复后误拒 0 / 漏放 0
- [x] 前端四页：`/constraints` `/approvals` `/audit` `/budget`
- [x] AI 决策解释（explain_node）+ 消费洞察（insight_node），模板兜底保底
- [x] 三层 Mandate：一笔交易落 **3 条独立记录**（Intent/Cart/Payment，`trace_id` 串联）
- [x] Agent 流接真实 PayPal Agent Toolkit
- [x] LLM function calling 意图解析（**Z.ai `glm-4.7-flash` 实测 20 句 × 3 次 = 60/60 正确 + 20/20 稳定 + 降级 0**，见 [LLM_INTENT_VERIFICATION.md](LLM_INTENT_VERIFICATION.md)）
- [x] 供应商目录 / SaaS 订阅计划（Toolkit products / subscriptionPlans，Agent 侧可查）
- [x] 前端约束设定页 `/constraints` + 审计页 + 人工确认入口
- [x] SQLite → PostgreSQL 切换（含迁移脚本，D1-D5 回归通过）
- [x] Webhook 签名校验 + 公网隧道（4 事件订阅完成）
- [x] git init + .gitignore + MIT LICENSE（署名 2026 Niu Zisen）
- [ ] MCP Server 集成（P1 冲奖）
- [ ] Demo 视频 ≤3 分钟（**录屏剪辑，不要现场直播**）
- [ ] 11/11 提前提交 Devpost

## 规则

- 全程沙箱，不涉及真实资金
- `.env` 绝不进 git；Secret 不写进文档/聊天/前端
- 提交前确认 `git status` 干净

---

*PayPal AI Hackathon 2026 · 提交截止 2026-11-13 04:00 北京时间*
