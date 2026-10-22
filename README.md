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
| Agent | LangGraph（五节点状态机） | ✅ 已跑通 |
| 后端 | FastAPI + Python | ✅ 已跑通 |
| 支付 | PayPal Orders v2 REST（Web 流）+ Agent Toolkit（Agent 流） | ✅ **两条均已跑通** |
| 模型 | DeepSeek-V3（意图解析，function calling） | ✅ 已接入（无 Key 时自动降级规则解析） |
| 数据库 | **SQLite**（开发期）→ PostgreSQL（Render 部署时） | ⚠️ 见下方说明 |
| 前端 | `static/` 原生 HTML（支付页 + 审计页） | ✅ 已可用 |
| MCP | @paypal/mcp | ⏳ P1 可选，冲奖用 |

> **数据库偏差说明（需确认）**：交接指示 §二 把数据库锁定为 PostgreSQL（Render 免费层）。
> 当前实现用的是 **SQLite** —— 开发期零配置、单文件、Demo 现场不易翻车。
> 持久化接口集中在 `agent/store.py`（save/load/list/update），换 PostgreSQL 只需改这一个文件，
> 不扩散到业务逻辑。若在 Render 部署前需要切 PG，改这一个文件 + 装 `psycopg2` 即可。

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
│   ├── llm_intent.py      # DeepSeek function calling 解析器
│   ├── paypal_tools.py    # PayPal Agent Toolkit 薄封装 — Agent 流
│   ├── graph.py           # 五节点状态机 parse→validate→approve→pay→verify
│   ├── store.py           # SQLite 持久化（约束 / 请求 / 审计日志）
│   ├── e2e_check.py       # 支付闭环校验脚本
│   └── demo.py            # 四场景端到端演示
├── static/
│   ├── index.html         # PayPal JS SDK 支付按钮页（Web 流）
│   └── audit.html         # 三层 Mandate 审计页 + 人工确认入口
├── mandatepay.db          # SQLite（已 gitignore）
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
3. 沙箱弹窗开合不稳定，浏览器自动化是无底洞，且让 Demo 录制随时翻车；
4. 评委看的是 agentic commerce 的**交互安全**，不是自动化程度。

正确做法：Demo 真实走一遍，旁白强调"人确认"是安全设计；前端提供"去确认付款"+"我已付款·捕获"按钮
（**人授权 + 机器执行**），这才是 agentic commerce 的正确模式。

## 混合闭环校验脚本

不要追求"全自动"或"纯人工"二选一 —— 机器驱动可控部分，人只点最后一步。

```bash
# 1. 创建订单，拿到 approval_url
python -m agent.e2e_check --create --amount 10 --item "Notion Pro"

# 2. 人在浏览器打开 approval_url，用沙箱测试买家账号完成付款

# 3. 捕获并断言 COMPLETED
python -m agent.e2e_check --capture <ORDER_ID>
```

## 快速开始

```bash
cd D:\paypal-ai-hackathon
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m uvicorn app.main:app --port 8888
```

- `http://127.0.0.1:8888/` —— PayPal 支付测试页（Web 流）
- `http://127.0.0.1:8888/audit` —— 三层 Mandate 审计页 + 人工确认入口

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 支付页（Web 流） |
| GET | `/audit` | 审计页 |
| POST | `/api/create-order` | 创建订单（Web 流） |
| POST | `/api/capture-order/{id}` | 捕获订单（Web 流） |
| POST | `/api/webhook` | Webhook 回调（骨架，待补签名校验） |
| POST | `/api/agent/constraints` | 设定约束 |
| POST | `/api/agent/requests` | 提交采购（校验→Mandate→真实下单） |
| POST | `/api/mandates/confirm` | 人付款后捕获并置 COMPLETED |
| GET | `/api/mandates` | 三层 Mandate 审计日志 |
| GET | `/api/constraints/current` | 当前生效约束 |

## 需订阅的 Webhook 事件

```json
["PAYMENT.CAPTURE.COMPLETED", "PAYMENT.CAPTURE.DENIED", "CHECKOUT.ORDER.APPROVED", "CHECKOUT.ORDER.COMPLETED"]
```

本地调试需公网 URL：`ngrok http 8888`。

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
- 沙箱测试卡：VISA `4333134069790191`，有效期 `11/31`，CSC 任意 3 位
- ⚠️ **必须走测试买家账号登录付款**。用非测试邮箱走 guest 付款会被 PayPal 当"新买家"要求创建账户
  （密码/出生日期/身份证件/邮编）→ 地址验证 → 短信验证码，沙箱不真发短信，是死路。
  用测试买家账号直接余额付款，不触发创建账户流程。

## 待办

- [x] 沙箱支付闭环跑通（REST）
- [x] LangGraph 五节点状态机
- [x] 约束引擎（预算/白名单/阈值）
- [x] 三层 Mandate 审计日志 + 持久化
- [x] Agent 流接真实 PayPal Agent Toolkit
- [x] DeepSeek function calling（含无 Key 降级）
- [x] 前端审计页 + 人工确认入口
- [ ] （确认）SQLite → PostgreSQL 切换
- [ ] Webhook 签名校验 + 公网隧道
- [ ] MCP Server 集成（P1 冲奖）
- [ ] Demo 视频 ≤3 分钟（**录屏剪辑，不要现场直播**）
- [ ] GitHub 公开仓库 + MIT license
- [ ] 11/11 提前提交 Devpost

## 规则

- 全程沙箱，不涉及真实资金
- `.env` 绝不进 git；Secret 不写进文档/聊天/前端
- 提交前确认 `git status` 干净

---

*PayPal AI Hackathon 2026 · 提交截止 2026-11-13 04:00 北京时间*
