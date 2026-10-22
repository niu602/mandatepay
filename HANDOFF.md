# MandatePay · PayPal AI Hackathon 2026 整合交接指示

> 版本：v3（最终整合版）
> 生成时间：2026-10-22 11:42 北京时间
> 距截止：2026-11-13 04:00 北京时间（22 天）
> 项目根目录：`D:\paypal-ai-hackathon\`
> 接手对象：后续 Agent / 协作者 / 用户本人

---

## 0. 最重要的事实（先读这条）

**PayPal 沙箱闭环已经跑通了。** 不是"配好了环境"，是真金白银地跑过一笔：

- 订单 `6S3278047H2709334` = **COMPLETED**
- 捕获 `58M77740LJ042791Y` = **COMPLETED**
- 金额 **$10.00 USD**

链路：前端点 PayPal 按钮 → `POST /api/create-order` → 沙箱弹窗买家登录付款 → `onApprove` → `POST /api/capture-order/{id}` → 页面显示 COMPLETED

**这意味着：赛事"PayPal 集成"这条硬要求你已经满足了。** 你现在的起点不是"从零开始"，而是"在已跑通的支付骨架上叠加 AI 和约束引擎"。这比原计划快了整整 3 天。

---

## 1. 赛事与硬约束（不可违背）

| 项 | 值 |
|---|---|
| 赛事 | PayPal AI Hackathon 2026 |
| 提交平台 | https://paypalaihackathon.devpost.com/ |
| 时间线 | 10/18 – 11/12 开发，**截止 11/13 01:30 GMT+5:30 = 北京时间 11/13 04:00** |
| 奖金 | $67,500+，冠军 $12,000；专项奖含 **Best Use of Agentic Commerce** |
| 提交硬要求 | PayPal Sandbox 集成 + AI 双核心 + 可运行原型；交付物 = **演示视频 ≤3 分钟** + GitHub 公开仓库 + 项目描述 |
| 资金安全 | 测试与参赛全程**不涉及真实资金**；唯一涉钱环节 = 获奖后收款（用 paypal.cn 账号，已实名审核通过） |

### 淘汰红线（踩一条直接出局）

- ❌ 静态 mockup / 不可运行原型
- ❌ 仓库没有 license 文件（高频淘汰点）
- ❌ Demo 视频超过 3 分钟
- ❌ `.env` 或密钥进了 git
- ❌ 截止前 1 小时提交（Devpost 拥堵是历史常态，**11/11 提前交**）

---

## 2. 项目定位

### 命题

> AI 能写代码、写邮件、订会议，但没人敢给 AI 付款权限——因为钱出去了回不来。
> MandatePay 给 AI 装一个"花钱的笼子"：用户用自然语言设定约束（月度预算、白名单、单笔阈值），Agent 在约束内自主执行采购，每笔交易生成可审计的三层 Mandate 记录。

### 三层 Mandate（杀手锏，灵感来自 PayPal AP2 协议）

| 层 | 作用 | 存储内容 |
|---|---|---|
| **Intent Mandate** | 用户授权 Agent "可以买什么" | 品类白名单、供应商白名单、月度预算上限 |
| **Cart Mandate** | 用户确认"这次具体买什么" | 商品明细、锁定价格、有效期 |
| **Payment Mandate** | 用户授权"钱怎么扣" | 付款方式、单次限额、是否需二次确认 |

### 选奖策略

| 奖项 | 奖金 | 竞争 | 你的胜率 |
|---|---|---|---|
| **🏆 Best Use of Agentic Commerce** | $5,000 | 低 | ⭐⭐⭐⭐⭐ |
| Best Demo Delivery | $5,000 | 中 | ⭐⭐⭐⭐ |
| Most Creative | $5,000 | 中 | ⭐⭐⭐ |

**主冲 Agentic Commerce**：知道的人少、技术面窄（Agent + 支付交互协议），你的 LangGraph 经验正好命中。

---

## 3. 当前进度（已验证基线）

### ✅ 已完成（这是你真正的起点）

| # | 项 | 状态 | 证据 |
|---|---|---|---|
| 1 | paypal.cn 账号实名审核通过（跨境收付款） | ✅ | 收款时用，开发不用 |
| 2 | developer.paypal.com 沙箱已开通 | ✅ | `PAYPAL_MODE=sandbox` |
| 3 | Client ID / Secret 已写入 `D:\paypal-ai-hackathon\.env` | ✅ | 禁止出现在文档/聊天/前端 |
| 4 | FastAPI 骨架 + PayPal Orders v2 API + JS SDK 支付页 | ✅ | `app/main.py`, `app/paypal_client.py`, `static/index.html` |
| 5 | **沙箱闭环测试已跑通** | ✅ | 订单 6S3278047H2709334 COMPLETED；捕获 58M77740LJ042791Y COMPLETED；$10.00 USD |
| 6 | 赛事规则与奖项调研 | ✅ | 见 §1 |
| 7 | 方案设计（三层 Mandate） | ✅ | 见 §2 |
| 8 | PayPal 官方 Agent Toolkit + MCP Server 调研 | ✅ | developer.paypal.com/ai-tools |
| 9 | LangGraph 状态机骨架 | ✅ | `C:/Users/lenovo/Desktop/规划/mandatepay_graph_skeleton.py`，语法已验证 |

### ⏳ 待做（按优先级，P0 不做就完不了赛）

| 优先级 | 任务 | 说明 |
|---|---|---|
| **P0** | **选题定案** | AI + PayPal + 约束。参考：AI 采购助手（自动比价 + 约束执行）、AI 订阅管家。优先选能体现 MCP 动态调用的方向 |
| **P0** | **LangGraph Agent 层** | 在现有 FastAPI 骨架上加 Agent：意图解析 → 约束校验 → 审批 → 支付执行 |
| **P0** | **约束引擎** | CRUD 约束 + 校验逻辑（预算/白名单/阈值） |
| **P0** | **三层 Mandate 审计日志** | 每次交易写入 Intent/Cart/Payment 记录，这是评委最爱看的页面 |
| **P0** | **前端约束 UI** | 设定约束的页面 + 采购请求列表 + 审批弹窗 + 审计日志页 |
| **P0** | **Demo 视频 ≤3 分钟** | **录屏剪辑，不要现场直播** |
| **P0** | **GitHub 公开仓库 + README + license** | `.env`/`.venv` 必须排除 |
| **P0** | **11/11 提前提交 Devpost** | |
| **P1** | MCP Server 集成（冲奖） | 让 Agent 动态发现 PayPal 工具 |
| **P1** | 多 Agent 协作（比价 Agent + 执行 Agent） | 呼应你 ARC 编译器的多 Agent 经验 |
| **P1** | Render 部署 | 赞助商，有印象分 |
| **P2** | Webhook 签名校验 | 骨架的 `/api/webhook` 已留路由，暂只打印事件 |

---

## 4. 关键架构决策：REST 与 Agent Toolkit 并存（不要二选一）

这是本交接文档最重要的结论：

> **已有的 REST 实现不要扔，Agent Toolkit 也不要不加。两者服务于不同路径。**

| 路径 | 用途 | 用什么 | 理由 |
|---|---|---|---|
| **Web 支付流** | 用户点击 PayPal 按钮 → 弹窗付款 → 捕获 | **现有 REST 实现**（`paypal_client.py`） | 已验证跑通，JS SDK 天然走 REST，换 Toolkit 反而增加复杂度 |
| **Agent 支付流** | LangGraph Agent 自主调用支付能力 | **官方 Agent Toolkit**（`paypal-agent-toolkit`） | 返回 LangChain Tool，可直接接 `ToolNode`，是"Agent 调用支付"的正解 |

### 为什么不是"用 Toolkit 替换 REST"

1. 现有 REST 实现**已实测跑通一笔完整交易**——在黑客松里，"已验证"比"更优雅"值钱 10 倍
2. Toolkit 的 LangChain Tool 接口是给 Agent 用的，不是给 HTTP 前端用的
3. 两个路径都保留，反而向评委展示了"PayPal 集成覆盖了两种范式"——这是加分项

### Agent Toolkit 接入方式

```bash
pip install paypal-agent-toolkit
```

```python
from paypal_agent_toolkit.langchain.toolkit import PayPalToolkit
from paypal_agent_toolkit.shared.configuration import Configuration, Context

toolkit = PayPalToolkit(
    client_id=os.environ["PAYPAL_CLIENT_ID"],
    secret=os.environ["PAYPAL_CLIENT_SECRET"],
    configuration=Configuration(
        actions={
            "orders": {"create": True, "get": True, "capture": True},
            "products": {"create": True, "list": True},
            "subscriptionPlans": {"create": True, "list": True, "show": True},
        },
        context=Context(sandbox=True),
    ),
)
tools = toolkit.get_tools()  # LangChain Tool 列表
```

LangGraph 侧：
```python
from langgraph.prebuilt import ToolNode
paypal_tool_node = ToolNode(tools)
```

### MCP Server（冲奖核弹）

- 本地：`npx -y @paypal/mcp --tools=all`
- 远程沙箱：`https://mcp.sandbox.paypal.com/sse` 或 `/http`
- **价值**：让 Agent 在运行时**动态发现** PayPal 工具，而非硬编码调用——评委视为"下一代 Agent 架构"，正好打 Agentic Commerce 专项奖
- **注意**：这是 P1 可选项。如果国内网络连不上就放弃，不影响主赛道

---

## 5. 项目结构与当前状态

```
D:\paypal-ai-hackathon\
├── app/
│   ├── __init__.py
│   ├── config.py          # 读取 .env（凭证、沙箱/生产切换）✅ 已完成
│   ├── paypal_client.py   # PayPal REST API（token / 创建订单 / 捕获）✅ 已跑通
│   └── main.py            # FastAPI 入口 ✅ 已跑通
│                          #   / 、/api/create-order、/api/capture-order/{id}、/api/webhook
├── static/
│   └── index.html         # PayPal JS SDK 支付按钮页 ✅ 已跑通
├── .env                   # 凭证（已 gitignore，禁止进 git / 前端 / 聊天）✅
├── .env.example           # 示例（无真实值）✅
├── .gitignore             # ✅
├── requirements.txt       # fastapi / uvicorn / requests / python-dotenv ✅
├── README.md              # ✅ 待补充 AI 部分说明
├── .venv/                 # 虚拟环境 ✅
│
├── [待建] agent/          # LangGraph 状态机 + 约束引擎 + MCP 集成
├── [待建] frontend/       # Vue3 约束 UI（或复用 static/ 扩写）
└── [待建] tests/          # 评测脚本
```

### 启动命令（端口固定 8888）

```bash
cd D:\paypal-ai-hackathon; .\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8888
```

服务日志输出到 **F 盘**（用户要求大文件/下载统一放 F 盘）。

---

## 6. 沙箱凭证与敏感信息规则

### 凭证位置

- 全部在 `D:\paypal-ai-hackathon\.env`：`PAYPAL_CLIENT_ID`、`PAYPAL_CLIENT_SECRET`、`PAYPAL_MODE=sandbox`
- API base：`https://api-m.sandbox.paypal.com`
- Webhook ID（可选）：`PAYPAL_WEBHOOK_ID`，当前为空

### 认证（Toolkit 内部处理，但调试时需要懂）

```bash
curl -X POST "https://api-m.sandbox.paypal.com/v1/oauth2/token" \
  -u "CLIENT_ID:CLIENT_SECRET" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=client_credentials"
```

access_token 有效期约 9 小时。所有后续请求 Header：`Authorization: Bearer <token>`

### 买家/卖家测试账号

> ⚠️ **以下账号密码属于敏感凭据。本交接文档只记录"存在且已验证"，完整凭据只在 `.env` 和沙箱管理后台中查看。**

- 买家（Personal）：已在 Sandbox Accounts 创建，余额 $5,000，已验证可登录付款
- 卖家（Business）：已在 Sandbox Accounts 创建
- 沙箱测试卡：VISA `4333134069790191`，有效期 `11/31`，CSC 任意 3 位
- 管理入口：developer.paypal.com → Testing Tools → Sandbox Accounts（会话可能过期需重新登录）

### 敏感信息铁律（必须遵守）

1. **Secret 不得**明文写入任何文档 / 聊天 / 回复 / 前端代码，一律指向 `.env`
2. `.env` 绝不可进 git（`.gitignore` 已配置，提交前检查 `git status`）
3. 给其他 Agent 交接时，只说"凭证在 `.env`"，不贴值
4. 提交 GitHub 前用 `.env.example` 替代

---

## 7. 支付闭环踩坑记录（实测经验，务必遵循）

| # | 坑 | 正确做法 |
|---|---|---|
| 1 | **死路归因（修正——不是"卡路径"）**：guest 路径 + **非测试邮箱** → 触发"创建账户"流程。10-22 实测用 `niu602@outlook.com`（非测试邮箱）走 guest 付款，PayPal 把该邮箱当"新买家"，要求依次填密码 → 出生日期 → 身份证件 → 邮编 → 地址验证 → 短信验证码；沙箱不真发短信，开发者后台收件箱也查不到，所以是死路 | **必须用测试买家账号登录付款**（`sb-mtet153479750@personal.example.com`）。用测试买家账号直接余额付款，不会触发创建账户流程 |
| 2 | 沙箱登录偶发"系统暂时无响应"（genericError） | 先重试；或先单独在浏览器登录沙箱买家账号（sandbox.paypal.com），再发起付款弹窗即直接进入付款确认页。**可解，不是死路** |
| 3 | checkout 弹窗自动化 | CDP `Target.getTargets` **可枚举到**弹窗 page 目标（说"抓不到"不准确），但接管成本高；且弹窗**开不开本身不稳定**——10-22 实测点击后未开独立窗口，回退为页内"没有找到安全的 paypal 浏览器？帮助重启窗口完成付款"恢复弹层。**不要依赖自动化接管弹窗** |
| 4 | 后端服务可能掉线 | 掉线后重跑启动命令即可；日志输出到 F 盘 |
| 5 | Webhook 未配公网地址 | **可选，暂跳过**。若以后做，需公网 URL（ngrok）+ Webhook ID + 签名校验。`app/main.py` 的 webhook 路由已留注释 |
| 6 | JS SDK 的 client-id 注入 | 已固定：`static/index.html` 用 `{{CLIENT_ID}}` 占位，后端渲染时替换 |
| 7 | **`PAYER_ACTION_REQUIRED` 不是 bug，是设计** | Agent 创建订单后状态为 `PAYER_ACTION_REQUIRED`，必须买家通过 `approval_url` 登录付款后才能 capture。这是 PayPal 的标准安全流程，**不要试图用浏览器自动化绕过它**——三个不稳定因素见上（guest+非测试邮箱触发创建账户流程、genericError 可重试、弹窗开合不稳定）。正确做法：把"人确认"作为产品特性展示——AI 可以自主下单，但不能自主扣款，这正是 MandatePay 的存在理由。Demo 走真实流程录屏即可 |

> **闭环方法论（统一口径，不要写成"全自动 vs 纯人工"的二选一）**：混合闭环——自动化驱动可控部分（打开页面 / 点击按钮 / 登录测试买家）+ 人点击最终"完成购买" + 后端调用 `capture-order` + 用 PayPal API 断言 `COMPLETED`。这条链路应当沉淀成一条可复用的 API 测试脚本：`create → simulate approve → capture → assert COMPLETED`，而不是靠浏览器自动化硬跑。
>
> **"收件箱为空"的严谨性（修正）**：10-22 只查了沙箱账号"邮件通知"这一个 dashboard 入口，没有穷举所有代码级取件渠道。这是**主动放弃**，不是**不可能证明**。文档里不要写"沙箱无法读取邮件"，要写"未穷举取件渠道，故选择测试买家账号余额付款路径，不依赖邮件回调"。

### 关于"最后一公里要不要自动化"的决策记录（2026-10-22）

> **决策：不改，保持人工确认。**
>
> 有人（包括后续的 Agent）会本能地觉得"Agent 支付应该全自动，要人去点说明没做完"。这个直觉在本项目里是错的。
>
> - MandatePay 的命题就是"AI 不能偷偷花钱"——全自动扣款与产品核心叙事冲突
> - PayPal 的 agentic commerce 范式本身就是"人预先授权范围 + Agent 在范围内行动"，不是"Agent 静默扣款"
> - 浏览器自动化在沙箱上是 3 天起步的无底洞，且 Demo 录制时一旦翻车无法重录
> - 正确做法：Demo 真实走一遍流程，旁白强调"人确认"是安全设计；前端可加"一键确认"按钮（人授权 + 机器执行），这才是 agentic commerce 的正确模式
>
> **唯一的例外**：如果用户明确要求演示"免密自动扣款"模式，再考虑 PayPal Vault（预保存付款方式 token）。但这需要用户先完成 Vault 设置，Sandbox 配置复杂，且与产品叙事冲突，**不作为主流程**。

---

### 必须订阅的 Webhook 事件

```json
["PAYMENT.CAPTURE.COMPLETED", "PAYMENT.CAPTURE.DENIED", "CHECKOUT.ORDER.APPROVED", "CHECKOUT.ORDER.COMPLETED"]
```

本地调试需要公网 URL → 用 ngrok：`ngrok http 8000`

---

## 8. LangGraph 状态机骨架说明

文件：`C:/Users/lenovo/Desktop/规划/mandatepay_graph_skeleton.py`（语法已验证通过）

```
领域模型: Constraint / PurchaseRequest / MandateRecord
Graph State: AgentState (constraint, request, messages, decision, mandate, payment_result, error)
工具层:    build_paypal_tools() → ToolNode
           set_constraint / evaluate_purchase (自定义工具)
节点:      parse → validate → approve → pay → verify → END
路由:      router() 根据 decision 决定走向
演示:      __main__ 里有三个场景（设定约束 / 小额自动 / 大额审批）
```

> ⚠️ **骨架里的 `pay_node` 目前是 mock 实现**（返回模拟 order_id），真实实现需要替换为 Toolkit 的 `orders.create` / `orders.capture` 调用。**这是接手后第一个要填的坑。**

### 填坑顺序建议

1. 先把骨架跑通，确认五节点状态机能流转一轮
2. 把 `pay_node` 的 mock 换成 Toolkit 真实调用
3. 让三个场景端到端跑通（设定约束 → 小额自动通过 → 大额需审批）
4. 再迁移到 `D:\paypal-ai-hackathon\agent\` 目录，与 FastAPI 骨架合并

---

## 9. 用户偏好（必须遵守）

- 全程用**中文**交流
- 项目目录固定在 **D 盘**（`D:\paypal-ai-hackathon`）；大文件/下载统一放 **F 盘**
- 端口固定 **8888**
- 测试与参赛全程不涉及真实金额
- 未经用户明确要求，不向额外账号/仓库/云盘写入内容
- 遵守"完全访问"执行模式：直接执行用户点名要做的操作，不做用户未要求的扩展动作
- 学习方式：**实践型，偏好边做边学**。给他可跑的骨架比给文档有效
- 偏好直接、不绕弯子的反馈；能接受尖锐批评
- 有正规 VPN，Devpost / GitHub / PayPal 均可访问

---

## 10. 给接手 Agent 的第一句话

> 先读本文件 + `D:\paypal-ai-hackathon\README.md` + `app/main.py` + `app/paypal_client.py`，确认服务能启动、闭环能跑通（用买家账号登录付款），再和用户确认选题，然后开始开发。
>
> **有一条与"通用建议"冲突的事实要特别注意**：通用建议会说"用官方 Agent Toolkit 替换手写 REST"。**在本项目中不要这么做**——手写 REST 那条路已经实测跑通了一笔完整交易，它是 Web 支付流的正确选择；Agent Toolkit 是给 LangGraph Agent 用的，两条路径并存。有任何与上述"已验证事实"冲突的结论，以实测为准并向用户说明。

---

## 11. 关键链接速查

| 资源 | 链接 |
|---|---|
| PayPal AI Hackathon Devpost | https://paypalaihackathon.devpost.com/ |
| PayPal Developer Dashboard | developer.paypal.com |
| **Agent Toolkit** | developer.paypal.com/ai-tools/toolkit/ |
| **MCP Server** | developer.paypal.com/ai-tools/mcp-server/ |
| **Agent Tools 参考** | developer.paypal.com/ai-tools/agent-tools/ |
| REST API 文档 | developer.paypal.com/api/rest/ |
| REST API 错误参考 | developer.paypal.com/api/errors/overview |
| Agentic Commerce | developer.paypal.com/agentic-commerce-services/about |
| PayPal GitHub 示例 | github.com/paypal/agent-toolkit |
| ngrok | ngrok.com |
| Render | render.com |
| LangGraph | langchain-ai.github.io/langgraph |

---

## 附录 A：与 v1 交接指示的差异说明

本整合版（v3）取代了 `C:/Users/lenovo/Desktop/规划/MandatePay_任务交接指示.md` 的 v1 版本。主要差异：

| 项 | v1（旧） | v3（整合版） |
|---|---|---|
| 项目位置 | 假设从零在 C 盘建项目 | **实际项目已在 `D:\paypal-ai-hackathon`** |
| PayPal 状态 | 假设 D1-D3 跑通 | **沙箱闭环已实测跑通**（订单+捕获均 COMPLETED） |
| REST vs Toolkit | 建议"用 Toolkit 替换手写 REST" | **两者并存**：REST 用于 Web 流（已验证），Toolkit 用于 Agent 流 |
| 前端 | 假设 Vue3 新写 | 已有 `static/index.html` + JS SDK，可扩展 |
| 敏感信息 | 未涉及 | 新增 §6 敏感信息铁律 + §7 踩坑记录 + 认证 curl 示例 |
| 骨架细节 | 未展开 | 新增 §8 骨架结构与填坑顺序 |
| 用户偏好 | 通用描述 | 新增 D盘/F盘/端口8888/完全访问模式等实测约束 |
| 每日 Standup | 有 | 见附录 B |

---

## 附录 B：每日 Standup 模板

```
日期：10/___
今天完成了：
卡住的：
明天的 1 件最重要的事：
距离截止还有 ___ 天
```

> 你之前比赛最大的问题是"做到哪算哪"。这个模板强迫你每天知道自己在哪。**坚持填，比任何技巧都管用。**

---

*本交接指示基于 2026-10-22 实测状态生成。项目实际推进后请持续更新本文件，保持"已验证事实"始终准确。*
*完整对话回溯：F:/2026-10-16-16-14-50/.workbuddy/projects/*/3e7fc865-fd69-4ddb-9d81-d406cf54642b.jsonl*
