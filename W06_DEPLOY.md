# W-06 · Neon + Render 部署（全免费）

> 执行时间：2026-11-01　目标：把 MandatePay 部署成公开可访问的 URL，供海外评委直接体验。
>
> **状态更新（11-02）**：Neon 数据库 ✅ 已建好并初始化；**Render 被 Stripe 卡片验证拦住**
> （见 §二），已切换到免卡方案 **Zeabur**（见 §三）。

---

## 一、平台选型（为什么是 Neon + Render）

| 方案 | 结论 | 原因 |
|---|---|---|
| **Neon** 免费 PostgreSQL | ✅ **采用** | 免费项目**不会自动删除**（只休眠，唤醒秒级）；提供标准 `postgresql://` 连接串；与 Render 同区（Oregon）时延迟低 |
| Render 免费 PostgreSQL | ❌ 不用 | **创建 30 天后删除** —— 11 月建库正好在评审期（12-01 ~ 12-15）内失效（R-09） |
| Supabase 免费层 | ❌ 不用 | **7 天无活动即暂停**，需手动恢复。评审期间没人点就会挂 |
| Render 免费 Web Service | ⚠️ **需绑卡** | 2026 年实际体验：创建服务时强制 Stripe 身份验证（$1 临时授权），**无法跳过** → 与"全免费"目标冲突，改用 Zeabur |

**关键约束**：评审期是 12-01 ~ 12-15。Neon 只休眠不删库 → 安全；
Zeabur / Render 实例都会在无访问时休眠（唤醒 30-60 秒），这由评委访问触发，不影响数据。

### Neon 连接串（已验证可用）

```
postgresql://neondb_owner:***@ep-soft-sky-arct6yay-pooler.c-4.us-west-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require
```

实测：连接成功（PostgreSQL 18.6）、六张表已建、约束已写入、目录已同步、已清场。
注意连接串用的是 **pooler 端点**（`-pooler.`），Neon 的连接池对 serverless 部署更友好。

---

## 二、Render 的卡片验证（为什么换方案）

创建 Blueprint 时弹出 **"Add Card — Add credit card to verify your identity"**：
全名 / 国家或地区 / 邮编 / 卡号，Stripe 托管，`$1 USD 临时授权不扣费`。
右上角只有关闭按钮（×），**关闭后无法继续部署**。

### 结论

- 这是 **Render 账号级的身份验证**，不是免费档的"付费墙" —— 但对"全免费"目标是硬门槛。
- 多方资料一致：Render 免费档**理论**上无需卡（750 小时/月），但 2026 年实际对新账号
  普遍要求 Stripe 验证（地域/风控策略差异）。
- **绕过方式**：用任意真实信用卡走完 $1 验证，之后免费档可用；
  但这违背"全免费"前提，且国内信用卡的 Stripe 验证常被拒。

### 决策：改用 Zeabur（无需信用卡）

| 维度 | Render | **Zeabur** |
|---|---|---|
| 是否要卡 | ⚠️ **要**（Stripe $1 验证） | ✅ **不要** |
| 免费额度 | 750 小时/月 | $5/月等值额度 |
| 内存 | 512MB | 256MB（**实测应用只用 60MB，够用**） |
| 休眠 | 15 分钟 | 15 分钟 |
| 控制台 | 英文 | ✅ **原生中文**（华人团队，对国内用户友好） |
| 部署方式 | Blueprint（render.yaml） | GitHub 直连 / Dockerfile |
| 数据库 | 免费 PG 30 天删除 | 用外部 Neon（不删） |

---

## 三、Zeabur 部署步骤（免卡，中文界面）

### 步骤 1：注册

1. 打开 https://zeabur.com → **GitHub 登录**
2. 新建 Project（项目）

### 步骤 2：部署服务

1. Project → **Deploy New Service** → 选 GitHub 仓库 `niu602/mandatepay`
2. 平台会检测到 `Dockerfile` → 用容器模式
   （若想更快，可删掉 Dockerfile 用 Python 原生模式 + `requirements.txt`，平台自动识别）
3. **Variables** 里填环境变量：

| 变量 | 值 |
|---|---|
| `DATABASE_URL` | Neon 连接串（见 §一） |
| `PAYPAL_CLIENT_ID` | PayPal 沙箱 Client ID |
| `PAYPAL_CLIENT_SECRET` | PayPal 沙箱 Secret |
| `PAYPAL_WEBHOOK_ID` | Webhook ID |
| `LLM_API_KEY` | Z.ai key |

   非敏感项已写在 `zeabur.yaml` 里：`PAYPAL_MODE=sandbox` / `LLM_BASE_URL` / `LLM_MODEL=glm-4.7-flash` / `LLM_THINKING=disabled`

4. **Deploy** → 拿到 `https://mandatepay-xxx.zeabur.app`

### 步骤 3：部署后初始化

Neon 侧的建表/种子/清场**已完成**（本地连 Neon 执行的 `deploy_init.sh`）。
若换新库，重跑：

```bash
DATABASE_URL=<neon连接串> bash scripts/deploy_init.sh
```

---

## 四、备选：Hugging Face Spaces（若 Zeabur 也不合适）

免费 Docker Space，**无需卡**，2 vCPU / 16GB RAM（最宽裕），48 小时休眠（最宽容）。

1. https://huggingface.co/spaces/new → SDK 选 **Docker**
2. 仓库地址填 `https://huggingface.co/spaces/<你的用户名>/mandatepay`：
   ```
   git remote add hf https://huggingface.co/spaces/<用户名>/mandatepay
   git push hf main
   ```
3. Variables/Secrets 里填同样的 5 个环境变量
4. 访问 `https://<用户名>-mandatepay.hf.space`

⚠️ HF Spaces 要求 **端口 7860**（或 Dockerfile 里的 EXPOSE 与之匹配），
且根路径 `/` 必须返回响应（我们的 `/` 返回支付页，满足）。

---

## 五、本地已完成的准备工作

### 5.1 代码修复（部署前必须做，否则线上有问题）

| 修复 | 原因 | 文件 |
|---|---|---|
| **挂载 `/static`** | `budget.html` 的 AG Studio 运行时靠 `fetch('/static/vendor/ag-studio.mjs')` 探测本地 ESM。此前**从未挂载 `/static`**，探测永远 404 → 只能回退 jsdelivr CDN（外网依赖 + 首屏慢） | `app/main.py` |
| **启动时自动建表** | Neon 免费项目**只有空库没有表**，而建表逻辑在 `store._Conn.__enter__`（连接时执行）。若不显式触发，**第一个请求才建表** → 冷启动首请求超时 | `app/main.py`（lifespan） |
| 静态资源直出 | 部署后前端资源统一走 `/static` | `app/main.py` |

### 5.2 部署配置

| 文件 | 用途 |
|---|---|
| `render.yaml` | Render Blueprint（若日后绑卡可用，配置已就绪） |
| `zeabur.yaml` | Zeabur 服务定义（免卡方案，当前采用） |
| `Dockerfile` | 容器镜像，`$PORT` 兼容各平台端口注入，`exec` 形式保证正确接收 SIGTERM |
| `.dockerignore` | 排除 `.env` / `node_modules` / 本地数据库 / `static/vendor`，防密钥进镜像 |
| `scripts/deploy_init.sh` | 一键：建表 → 种子约束 → 同步目录 → 清场 |

### 5.3 本地验证结果

```
✅ 五个页面全部 200：/ /constraints /budget /approvals /audit
✅ /static/budget.html                200
✅ /static/vendor/ag-studio.mjs       200（本地 ESM 命中，不再走 CDN）
✅ 启动建表：postgresql schema 就绪
✅ 模拟 Render 启动命令（0.0.0.0 + $PORT + env）：六个端点全部 200
✅ deploy_init.sh 全流程跑通（建表→约束→目录→清场）
✅ 清场后应用正常：约束 $200 / 预算已花 $0
✅ Neon 实连：PG 18.6，六表就绪，约束已写，目录已同步，已清场
✅ DSN 兼容：postgres:// 与 postgresql:// 都能用（store._effective_dsn 归一化）
✅ 应用常驻内存 60MB（Zeabur 免费档 256MB 有余量）
```

### 5.4 冷启动预判（实测前）

| 项 | 实测值 | 判断 |
|---|---|---|
| 应用 import 耗时 | **0.68s** | 运行时不构成瓶颈 |
| 应用常驻内存 | **60MB** | Zeabur 256MB 够用 |
| `paypal-agent-toolkit` 传递依赖 | **仅 httpx / pydantic / requests** | 很轻，工单担心的"依赖过重"**不成立** |
| `uvicorn[standard]` | 含 httptools / uvloop / watchfiles | 需编译或取预编译 wheel → **构建期**可能慢（不是运行期） |
| langchain 链 | langgraph + langchain + langchain-openai | 中等，pip 安装约 1-2 分钟 |

> 结论：冷启动风险主要在**构建阶段**（pip install），而非运行时。
> 工单给的精简顺序（① 裸 uvicorn ② 查 toolkit 依赖）**已预先验证第 ② 条不适用**。

---

## 六、验收清单（DoD）

部署完成后逐项核对：

- [ ] 公开 URL 五个页面全部 200：/ /constraints /budget /approvals /audit
- [ ] `/budget` 双视图均正常（AG Studio 视图 + 原版视图，切换按钮可用）
- [ ] 冷启动耗时已记录（首访 + 唤醒后各测一次）
- [ ] 截图存档

---

## 七、冷启动耗时记录

| 场景 | 耗时 | 备注 |
|---|---|---|
| 首次构建 | 待填 | pip install 全量 |
| 首次访问（冷） | 待填 | 平台实例启动 + 应用初始化 |
| 休眠后唤醒 | 待填 | 15 分钟无访问即休眠 |

**若冷启动 > 90 秒**，按工单顺序精简：
1. `uvicorn[standard]` → 裸 `uvicorn`（去掉 httptools/uvloop/watchfiles 编译依赖）
2. 检查 `paypal-agent-toolkit` 传递依赖 —— **已验证：仅 httpx/pydantic/requests，不重**

---

## 八、已知限制（如实记录）

1. **API 无鉴权**（技术债 #12）：公网部署后 `/api/*` 全部暴露。
   本项目是 Demo 性质，评审期可接受；但**不要放任何真实数据**。
2. **实例 15 分钟休眠**：评委访问会触发唤醒（30-60 秒）。
   建议**演示前自己访问一次预热**，或接受首次加载慢。
3. **`/budget` 的 AG Studio 走 CDN**（若未在构建期装 `static/vendor/`）：
   `static/vendor/` 已 gitignore（npm 商业包），容器构建时不会自动装。
   页面会自动回退 jsdelivr CDN —— **功能正常**，但首屏依赖外网。
4. **PayPal Webhook 需要公网 URL**：部署后把 webhook 地址改成
   `https://<你的域名>/api/webhook`（比 ngrok 稳定，评审期一直有效）。
5. **密钥已在聊天中出现**：Neon 连接串（2026-11-02）包含数据库密码，
   建议在 Neon 控制台**轮换密码**（Rotation），并同步更新平台环境变量。

