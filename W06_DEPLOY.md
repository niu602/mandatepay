# W-06 · Neon + Render 部署（全免费）

> 执行时间：2026-11-01　目标：把 MandatePay 部署成公开可访问的 URL，供海外评委直接体验。

---

## 一、平台选型（为什么是 Neon + Render）

| 方案 | 结论 | 原因 |
|---|---|---|
| **Neon** 免费 PostgreSQL | ✅ **采用** | 免费项目**不会自动删除**（只休眠，唤醒秒级）；提供标准 `postgresql://` 连接串；与 Render 同区（Oregon）时延迟低 |
| Render 免费 PostgreSQL | ❌ 不用 | **创建 30 天后删除** —— 11 月建库正好在评审期（12-01 ~ 12-15）内失效（R-09） |
| Supabase 免费层 | ❌ 不用 | **7 天无活动即暂停**，需手动恢复。评审期间没人点就会挂 |
| Render 免费 Web Service | ✅ **采用** | 750 小时/月免费额度足够；自动从 GitHub 仓库构建；`render.yaml` 可声明式配置 |

**关键约束**：评审期是 12-01 ~ 12-15。Neon 只休眠不删库 → 安全；
Render 实例 15 分钟无访问会休眠（唤醒约 30-60 秒），这由评委访问触发，不影响数据。

---

## 二、本地已完成的准备工作

### 2.1 代码修复（部署前必须做，否则线上有问题）

| 修复 | 原因 | 文件 |
|---|---|---|
| **挂载 `/static`** | `budget.html` 的 AG Studio 运行时靠 `fetch('/static/vendor/ag-studio.mjs')` 探测本地 ESM。此前**从未挂载 `/static`**，探测永远 404 → 只能回退 jsdelivr CDN（外网依赖 + 首屏慢） | `app/main.py` |
| **启动时自动建表** | Neon 免费项目**只有空库没有表**，而建表逻辑在 `store._Conn.__enter__`（连接时执行）。若不显式触发，**第一个请求才建表** → 冷启动首请求超时 | `app/main.py`（lifespan） |
| 静态资源直出 | 部署后前端资源统一走 `/static` | `app/main.py` |

### 2.2 部署配置

- **`render.yaml`** —— 声明式 Blueprint 配置（无密钥，全部 `sync: false` 手动填）
- **`scripts/deploy_init.sh`** —— 部署后一键初始化：建表 → 种子约束 → 同步目录 → 清场
- **`.env.example`** —— 补充 Neon 连接串格式说明

### 2.3 本地验证结果（部署前的模拟）

```
✅ 五个页面全部 200：/ /constraints /budget /approvals /audit
✅ /static/budget.html                200
✅ /static/vendor/ag-studio.mjs       200（本地 ESM 命中，不再走 CDN）
✅ 启动建表：postgresql schema 就绪
✅ deploy_init.sh 全流程跑通（建表→约束→目录→清场）
✅ 清场后应用正常：约束 $200 / 预算已花 $0
✅ DSN 兼容：Neon 的 postgresql:// 直连（store._effective_dsn 已归一化 postgres:// 前缀）
```

### 2.4 冷启动预判（实测前）

| 项 | 实测值 | 判断 |
|---|---|---|
| 应用 import 耗时 | **0.68s** | 运行时不构成瓶颈 |
| `paypal-agent-toolkit` 传递依赖 | **仅 httpx / pydantic / requests** | 很轻，工单担心的"依赖过重"**不成立** |
| `uvicorn[standard]` | 含 httptools / uvloop / watchfiles | 需编译或取预编译 wheel → **构建期**可能慢（不是运行期） |
| langchain 链 | langgraph + langchain + langchain-openai | 中等，pip 安装约 1-2 分钟 |

> 结论：冷启动风险主要在**构建阶段**（pip install），而非运行时。
> 工单给的精简顺序（① 裸 uvicorn ② 查 toolkit 依赖）**已预先验证第 ② 条不适用**。

---

## 三、需要你执行的两个账号动作

> 这两步涉及你的账号授权，我无法代替。全部配置已就绪，你只需复制粘贴。

### 步骤 1：创建 Neon 免费项目（拿连接串）

1. 打开 https://console.neon.tech/app → 注册（GitHub 登录最快）
2. **Create a project**：
   - Project name: `mandatepay`
   - Postgres version: 任意（默认即可）
   - Region: **AWS US West (Oregon)** ← 与 Render 同区，延迟最低
   - Plan: **Free**
3. Console 会显示连接串，形如：
   ```
   postgresql://user:password@ep-xxx.us-west-2.aws.neon.tech/mandatepay?sslmode=require
   ```
   点 **"Connect" → "URI"** 复制（**不要用 SQLAlchemy 那条**）

### 步骤 2：创建 Render 服务

1. 打开 https://render.com → 用 **GitHub 账号**登录并授权仓库 `niu602/mandatepay`
2. **New → Blueprint** → 选 `niu602/mandatepay` → 找到 `render.yaml` → **Apply**
3. Render 会提示填写 `sync: false` 的环境变量：

| 变量 | 填什么 |
|---|---|
| `DATABASE_URL` | 步骤 1 复制的 Neon 连接串 |
| `PAYPAL_CLIENT_ID` | 你的 PayPal 沙箱 Client ID（本地 `.env` 里有） |
| `PAYPAL_CLIENT_SECRET` | 你的 PayPal 沙箱 Secret |
| `PAYPAL_WEBHOOK_ID` | 你的 Webhook ID（本地 `.env` 里有） |
| `LLM_API_KEY` | 你的 Z.ai key |

   其余（`PAYPAL_MODE=sandbox` / `LLM_BASE_URL` / `LLM_MODEL=glm-4.7-flash` / `LLM_THINKING=disabled`）
   已写在 `render.yaml` 里，不用填。

4. **Apply** 后等构建完成（首次约 2-4 分钟），拿到 `https://mandatepay.onrender.com`

### 步骤 3：部署后初始化（Render Shell 里执行，或本地连 Neon 执行）

```bash
bash scripts/deploy_init.sh
```

做四件事：建表 → 写演示约束（预算 $200 / 阈值 $50 / 白名单 Notion+Figma）→
同步供应商目录（Notion $10 / Figma $15）→ 清场（清掉历史交易，保留约束）。

> ⚠️ **为什么必须清场**：预算按"已承诺支出"口径计算（下单即计入），
> 历史测试订单会把预算占满，导致评审新建订单直接 REJECT —— 看起来像功能坏了。

---

## 四、验收清单（DoD）

部署完成后逐项核对：

- [ ] `https://mandatepay.onrender.com/` → 200
- [ ] `/constraints` → 200（自然语言设约束）
- [ ] `/budget` → 200，**AG Studio 视图与原版视图都能正常渲染**（切换按钮可用）
- [ ] `/approvals` → 200
- [ ] `/audit` → 200（三层 Mandate 可见）
- [ ] 冷启动耗时已记录（首访 + 唤醒后各测一次）
- [ ] 截图存档

---

## 五、冷启动耗时记录

| 场景 | 耗时 | 备注 |
|---|---|---|
| 首次构建 | 待填 | pip install 全量 |
| 首次访问（冷） | 待填 | Render 实例启动 + 应用初始化 |
| 休眠后唤醒 | 待填 | 免费实例 15 分钟无访问即休眠 |

**若冷启动 > 90 秒**，按工单顺序精简：
1. `uvicorn[standard]` → 裸 `uvicorn`（去掉 httptools/uvloop/watchfiles 编译依赖）
2. 检查 `paypal-agent-toolkit` 传递依赖 —— **已验证：仅 httpx/pydantic/requests，不重**

---

## 六、已知限制（如实记录）

1. **API 无鉴权**（技术债 #12）：公网部署后 `/api/*` 全部暴露。
   本项目是 Demo 性质，评审期可接受；但**不要放任何真实数据**。
2. **Render 免费实例 15 分钟休眠**：评委访问会触发唤醒（30-60 秒）。
   建议**演示前自己访问一次预热**，或接受首次加载慢。
3. **`/budget` 的 AG Studio 走 CDN**（若未在构建期装 `static/vendor/`）：
   `static/vendor/` 已 gitignore（npm 商业包），Render 构建时不会自动装。
   页面会自动回退 jsdelivr CDN —— **功能正常**，但首屏依赖外网。
   如需本地 ESM，在 `render.yaml` 的 `buildCommand` 里加一步
   `pip install -r requirements.txt && python scripts/setup_ag_studio.py`。
4. **PayPal Webhook 需要公网 URL**：Render 的 URL 是 `https://mandatepay.onrender.com`，
   可以直接作为 webhook 地址（比 ngrok 稳定，且评审期一直有效）。
   需要把 `PAYPAL_WEBHOOK_ID` 与新 URL 重新配置到 PayPal。
