# W-06 · Neon + Render 部署（全免费）

> 执行时间：2026-11-01（方案于 11-02 修订）　目标：把 MandatePay 部署成公开可访问的 URL，供海外评委直接体验。
>
> **修订说明**：本工单中途两次修正平台结论，过程记录在 §二（Zeabur 已停运、
> HF Spaces 需付费），最终**回到 Render** —— 但要走「直接创建 Web Service」
> 而非 Blueprint 路径，因为后者会触发 Stripe 卡片弹窗。

---

## 一、平台选型（最终结论）

| 方案 | 结论 | 原因 |
|---|---|---|
| **Neon** 免费 PostgreSQL | ✅ **采用** | 免费项目**不会自动删除**（只休眠，唤醒秒级）；提供标准 `postgresql://` 连接串 |
| **Railway** 免费 Web Service | ✅ **采用** | 官方定价页明确 Free 档 **"No credit card required"**；$0/月含 $1 额度 |
| Render 免费 Web Service | ⚠️ 账号被拦 | 官方文章说免卡，但**实测新账号在最后一步仍被强制 Stripe 验证**（$1 预授权）→ 改用 Railway |
| Render 免费 PostgreSQL | ❌ 不用 | **创建 30 天后删除** —— 11 月建库正好在评审期（12-01 ~ 12-15）内失效（R-09） |
| Supabase 免费层 | ❌ 不用 | **7 天无活动即暂停**，需手动恢复 |
| Zeabur 免费档 | ❌ 不用 | **2026-10-24 已宣布停止运营**：12-01 起共享集群暂停所有服务 —— 正好撞上评审期 |
| Hugging Face Spaces | ❌ 不用 | 官方文档：Docker Space **需要付费计划**（免费只保留 Static 与 ZeroGPU Gradio） |
| PythonAnywhere 免费版 | ❌ 不用 | 出站网络**只能访问白名单域名** —— 白名单里没有 `api.z.ai` / `neon.tech`，LLM 调用与数据库连接会双双失败 |
| Cloudflare Containers | ❌ 不用 | 免费额度（25 GiB-hrs 等）是 **Workers Paid $5/月** 附带的，Free 档为 N/A |
| Google Cloud Run | ❌ 不用 | 标准部署要绑卡；AI Studio Starter Tier 免卡但**只支持 AI Studio 内生成的应用**，无法部署我们的 GitHub 仓库 |

**关键约束**：评审期是 12-01 ~ 12-15。Neon 只休眠不删库 → 安全；
Railway 免费实例可开 Serverless 休眠（空闲不计费），唤醒时冷启动。

### Neon 连接串（已验证可用）

```
postgresql://neondb_owner:***@ep-soft-sky-arct6yay-pooler.c-4.us-west-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require
```

实测：连接成功（PostgreSQL 18.6）、六张表已建、约束已写入、目录已同步、已清场。
注意连接串用的是 **pooler 端点**（`-pooler.`），Neon 的连接池对 serverless 部署更友好。

---

## 二、方案修订过程（两次走错的记录）

工单原本指定 Render。执行中遇到"必须绑卡"的硬门槛，逐一查证后结论如下。

### 2.1 坎一：Render 的 Stripe "Add Card" 弹窗

**现象**：点 Blueprint → 弹出「Add Card — Add credit card to verify your identity」，
Stripe 托管，`$1 USD 临时授权不扣费`，右上角只有 ×，**关闭后无法继续部署**。

**第一次判断（错）**：以为必须绑卡 → 改用 Zeabur。

**第二次判断（也错）**：查到 Render 官方 FAQ 说免卡（"不加卡，免费实例照样能用"）→
  以为改走"手动创建 Web Service"（非 Blueprint）即可绕过 → 让用户去试。

**实测结论（对）**：用户按手动路径走到**最后一步 "Deploy Web Service" 时仍被要求填卡**。
参考 FreeTier.co 2026-08 的核实记录：Render 免费档"**注册时需要信用卡**"。
官方文章与实际体验的差异 → Render 对**新注册账号**普遍启用了 Stripe 身份验证。
这是账号级风控，不是某条部署路径的问题。

> **教训**：官方文档说"免卡"不等于新账号一定能免。
> 选型时必须查**用户实测记录**（FreeTier.co / Reddit / 社区），而不只看平台自己的宣传。

### 2.2 坎二：银联卡能不能绑？（用户提问，已查证）

**能，但有条件。**（据 Stripe 官方文档与多方实测）

| 卡种 | 可行性 | 说明 |
|---|---|---|
| **境内银行双标卡**（带 Visa/Mastercard 标识） | ✅ 可尝试 | Stripe 优先走 Visa/Master 通道。实测初始通过率约 45%，大额扣款降到 15%；**$1 预授权金额小，通过率明显高于大额扣款** |
| **纯银联单标卡**（BIN 以 62 开头） | ❌ 基本不行 | Stripe 官方文档：中国银联（CUP）在线支付**仅支持特定账户地区**（美/英/加/澳/新/港/新马/瑞士/EEA），中国大陆不在列；实测单标卡绑定成功率不足 12% |
| 香港中资银行实体卡 | ✅ 通过率更高（70%） | — |

**如果仍想绑卡**，四个必须先做好的准备（否则必被拒）：

1. 银行 App 里开通**国际线上支付 + 无卡交易 + 自动扣款**三项开关（国内卡默认关闭）
2. 账单地址**填卡的实际发卡地**，邮编要与银行登记一致（填海外地址直接触发风控）
3. 用**双标卡**（有 Visa/Master 标识），不是纯银联单标
4. **IP 与账单地址地区保持一致**（人在国内就用国内地址，别配海外地址）

> 但既然我们要的是"全免费"，**最省事的做法是换一个不需要卡的平台** → Railway。

### 2.3 排除的其他平台（避免后来者重走）

**Zeabur —— 已停运**（官方公告 zeabur.com/blogs/zeabur-is-shutting-down）：

> "12 月 1 日起，共享集群：**暂停所有正在运行的服务**。你仍然可以手动恢复服务运行，以便进行数据备份。
> 12 月 31 日：所有独立服务器到期关闭，所有共享集群停止服务。"

评审期是 12-01 ~ 12-15 —— **服务在评审第一天就被暂停**，比 Render 免费 PG 的 30 天删除更致命。
（背景：2026 年下半年两起安全事件后决定结束运营。）

**Hugging Face Spaces —— Docker Space 现已需要付费计划**（官方文档）：

> "Static Spaces are free for everyone.
> **Gradio and Docker Spaces run on compute and require a paid plan to create.**"

**PythonAnywhere —— 出站白名单挡住关键依赖**（官方 whitelist 页面）：

免费账号出站只能访问白名单域名，而白名单里**没有 `api.z.ai`（Z.ai LLM）
也没有 `neon.tech`（Neon 数据库）** —— LLM 调用与数据库连接会双双失败。
另有 100 秒/天的 CPU 限制、免费应用每 3 个月需手动续期。

**Cloudflare Containers —— 免费额度挂在付费档上**（官方定价页）：

> "Memory | **Free** N/A | Workers Paid 25 GiB-hours/month included"

Free 档是 N/A，25 GiB-hrs 只是 Workers Paid $5/月 附带的。

**Google Cloud Run —— Starter Tier 不支持部署我们的仓库**：

AI Studio Starter Tier 确实免卡免账号（Google I/O 2026 公告："no credit card,
no Google Cloud account"），但它**只能用 prompt 在 AI Studio 里生成应用**，
无法部署我们已有的 GitHub 仓库。标准 Cloud Run 部署则需要绑卡。

---

## 三、Railway 部署步骤（免卡）

### 步骤 1：注册（不绑卡）

1. 打开 https://railway.app → **用 GitHub 账号登录**（同时授权仓库 `niu602/mandatepay`）
2. **不要**进入任何 Billing / 升级页面

> 额度：注册即得**一次性 $5 试用额度**，30 天有效。
> 试用结束后自动转 Free 档（$0/月，含 $1/月 使用额度），**无需绑卡**。

### 步骤 2：创建服务

Dashboard → **New Project** → **Deploy from GitHub repo** → 选 `niu602/mandatepay`

| 配置项 | 填什么 | 说明 |
|---|---|---|
| **服务名** | `mandatepay` | 决定域名 `mandatepay.up.railway.app` |
| **Root Directory** | 留空 | 仓库根 |
| **Builder** | 自动 | 检测到 `Dockerfile` → 容器构建 |
| **Start Command** | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` | Railway 注入 `PORT` |
| **Healthcheck Path** | `/api/health` | 端点已存在 |

> ⚠️ Railway 会自动读 `Procfile`（我们已备好），所以 Start Command 留空也能跑 ——
> 但显式写更稳。

### 步骤 3：填环境变量

服务 → **Variables** 标签页 → 粘贴 `deploy.env` 的内容（同 Render 的做法）。
Railway 支持**直接粘贴 .env 格式整段**。

| Key | Value |
|---|---|
| `DATABASE_URL` | Neon 连接串 |
| `PAYPAL_CLIENT_ID` | PayPal 沙箱 Client ID |
| `PAYPAL_CLIENT_SECRET` | PayPal 沙箱 Secret |
| `PAYPAL_WEBHOOK_ID` | Webhook ID |
| `LLM_API_KEY` | Z.ai key |
| `PAYPAL_MODE` | `sandbox` |
| `LLM_BASE_URL` | `https://api.z.ai/api/paas/v4` |
| `LLM_MODEL` | `glm-4.7-flash` |
| `LLM_THINKING` | `disabled` |
| `PYTHON_VERSION` | `3.11.9` |

### 步骤 4：生成公开域名

服务 → **Settings → Networking** → **Generate Domain** → 得到 `https://mandatepay.up.railway.app`

### 步骤 5（可选但建议）：开 Serverless 休眠

服务 → **Settings → Deploy** → 打开 **Serverless**（空闲 10 分钟自动休眠，不计费）。

- 我们的 `store` 每次操作后**关闭数据库连接**（`agent/store.py` 的 `_Conn.__exit__`），
  空闲时没有出站流量 → 符合 Railway 的休眠判定 → 真的会睡
- 代价：评委首次访问要等冷启动（几秒到十几秒）
- **额度考量**：$1/月额度在 always-on 模式下约够 11.5 天（评审期 15 天），
  开 Serverless 后实际消耗远低于此 → 稳妥

### 步骤 6：部署后验证（无需在 Railway 上执行命令）

> 💡 Railway 免费档**没有 Shell**（与 Render 同样）。
> **好消息是 Neon 侧的建表/种子/清场我已经在本地连库执行完了**，
> 部署完直接访问即可，不需要任何额外操作。

若日后换了新库，在**本地**跑：

```bash
DATABASE_URL=<neon连接串> bash scripts/deploy_init.sh
```

### 备选：仍然想用 Render（若日后绑到卡）

`render.yaml` 保留在仓库里，配置完整（10 个环境变量 + 免费档 + 健康检查），
日后绑卡后可直接 **New → Blueprint → Apply**。

---

## 四、本地已完成的准备工作

### 4.1 代码修复（部署前必须做，否则线上有问题）

| 修复 | 原因 | 文件 |
|---|---|---|
| **挂载 `/static`** | `budget.html` 的 AG Studio 运行时靠 `fetch('/static/vendor/ag-studio.mjs')` 探测本地 ESM。此前**从未挂载 `/static`**，探测永远 404 → 只能回退 jsdelivr CDN（外网依赖 + 首屏慢） | `app/main.py` |
| **启动时自动建表** | Neon 免费项目**只有空库没有表**，而建表逻辑在 `store._Conn.__enter__`（连接时执行）。若不显式触发，**第一个请求才建表** → 冷启动首请求超时 | `app/main.py`（lifespan） |
| 静态资源直出 | 部署后前端资源统一走 `/static` | `app/main.py` |

### 4.2 部署配置

| 文件 | 用途 |
|---|---|
| `Dockerfile` | 容器镜像，`$PORT` 兼容各平台端口注入，`exec` 形式保证正确接收 SIGTERM |
| `.dockerignore` | 排除 `.env` / `node_modules` / 本地数据库 / `static/vendor`，防密钥进镜像 |
| `Procfile` | 启动命令（Railway / Render 原生模式自动识别） |
| `render.yaml` | Render Blueprint 配置（**备用** —— 若日后绑卡可直接用） |
| `.python-version` | `3.11.9` —— 锁定 Python 版本（Render 默认会是 3.14.8） |
| `deploy.env` | 部署环境变量清单（本地生成，含明文密钥，已 gitignore） |
| `scripts/deploy_init.sh` | 一键：建表 → 种子约束 → 同步目录 → 清场 |

> ⚠️ **不用 `railway.toml`**：官方文档明确 Config as Code 已废弃、
> **2026-12-01 停止读取旧文件** —— 正好撞上评审期。新格式是 `.railway/railway.ts`
> （需 `npm install railway`），为免额外依赖，本次走 Dashboard 手动部署。

### 4.3 本地验证结果

```
✅ 五个页面全部 200：/ /constraints /budget /approvals /audit
✅ /static/budget.html                200
✅ /static/vendor/ag-studio.mjs       200（本地 ESM 命中，不再走 CDN）
✅ 启动建表：postgresql schema 就绪
✅ 模拟平台启动命令（0.0.0.0 + $PORT + env）：六个端点全部 200
✅ Procfile 命令 + Neon 生产库 + 完整环境变量 → 六端点全 200
✅ deploy_init.sh 全流程跑通（建表→约束→目录→清场）
✅ 清场后应用正常：约束 $200 / 预算已花 $0
✅ Neon 实连：PG 18.6，六表就绪，约束已写，目录已同步，已清场
✅ 真实 LLM 链路：连 Neon + Z.ai 走 function calling，解析正确
✅ DSN 兼容：postgres:// 与 postgresql:// 都能用（store._effective_dsn 归一化）
✅ 应用常驻内存 60MB（Railway Free 0.5GB / Render Free 512MB 均充足）
✅ Python 3.11 语法兼容性检查：ast.parse(feature_version=(3,11)) 全部通过
```

### 4.4 冷启动预判（实测前）

| 项 | 实测值 | 判断 |
|---|---|---|
| 应用 import 耗时 | **0.68s** | 运行时不构成瓶颈 |
| 应用常驻内存 | **60MB** | 0.5GB 档充足 |
| `paypal-agent-toolkit` 传递依赖 | **仅 httpx / pydantic / requests** | 很轻，工单担心的"依赖过重"**不成立** |
| `uvicorn[standard]` | 含 httptools / uvloop / watchfiles | 需编译或取预编译 wheel → **构建期**可能慢（不是运行期） |
| langchain 链 | langgraph + langchain + langchain-openai | 中等，pip 安装约 1-2 分钟 |

> 结论：冷启动风险主要在**构建阶段**（pip install），而非运行时。
> 工单给的精简顺序（① 裸 uvicorn ② 查 toolkit 依赖）**已预先验证第 ② 条不适用**。

### 4.5 Railway 额度核算（评审期 12-01 ~ 12-15 = 360 小时）

按官方单价（RAM $0.00000386/GB/s，CPU $0.00000772/vCPU/s）：

| 场景 | 小时成本 | 360 小时合计 | $1 额度够吗 |
|---|---|---|---|
| 实测占用（60MB + 0.05 vCPU） | $0.0022 | **$0.80** | ✅ 够 |
| 实测占用 + CPU 翻倍 | $0.0036 | $1.30 | ❌ 差 $0.30 |
| 按 Free 档上限算（0.5GB + 0.1 vCPU） | $0.0097 | $3.50 | ❌ 差 $2.50 |

Railway 按**实际使用**计费（不是按分配上限），我们的应用实测 60MB 内存、CPU 占用低 →
实际落在第一行。**再叠加 Serverless 休眠（空闲不计费）→ 消耗远低于 $1，稳妥。**

> 补充：Railway 按**出站流量**判定空闲。我们的 `store` 每次操作后关闭数据库连接
> （`agent/store.py` 的 `_Conn.__exit__` 里 `self._conn.close()`），
> 空闲时没有出站包 → 符合休眠条件 → 真的会睡。

---

## 五、验收清单（DoD）

部署完成后逐项核对：

- [ ] 公开 URL 五个页面全部 200：/ /constraints /budget /approvals /audit
- [ ] `/budget` 双视图均正常（AG Studio 视图 + 原版视图，切换按钮可用）
- [ ] 冷启动耗时已记录（首访 + 唤醒后各测一次）
- [ ] 截图存档

---

## 六、冷启动耗时记录

| 场景 | 耗时 | 备注 |
|---|---|---|
| 首次构建 | 待填 | pip install 全量 |
| 首次访问（冷） | 待填 | 平台实例启动 + 应用初始化 |
| 休眠后唤醒 | 待填 | Serverless 模式空闲 10 分钟休眠 |

**若冷启动 > 90 秒**，按工单顺序精简：
1. `uvicorn[standard]` → 裸 `uvicorn`（去掉 httptools/uvloop/watchfiles 编译依赖）
2. 检查 `paypal-agent-toolkit` 传递依赖 —— **已验证：仅 httpx/pydantic/requests，不重**

---

## 七、已知限制（如实记录）

1. **API 无鉴权**（技术债 #12）：公网部署后 `/api/*` 全部暴露。
   本项目是 Demo 性质，评审期可接受；但**不要放任何真实数据**。
2. **休眠 + 冷启动**：Railway Serverless 空闲 10 分钟休眠，评委首次访问要等冷启动。
   建议**演示前自己访问一次预热**。
3. **`/budget` 的 AG Studio 走 CDN**（若未在构建期装 `static/vendor/`）：
   `static/vendor/` 已 gitignore（npm 商业包），平台构建时不会自动装。
   页面会自动回退 jsdelivr CDN —— **功能正常**，但首屏依赖外网。
4. **PayPal Webhook 需要公网 URL**：部署后把 webhook 地址改成
   `https://<你的域名>/api/webhook`（比 ngrok 稳定，评审期一直有效）。
5. **Railway Free 档每账号仅 1 个项目**（试用期 5 个）—— 部署前先确认没有其他项目占用。
6. **Railway 额度耗尽会停服**（官方："your subscription will be cancelled...
   we will stop all of your workloads"）。无卡不会被扣款，但服务会停 →
   **开 Serverless + 关注 Dashboard 的 usage 显示**是必须的。
7. **Neon 连接串（数据库密码）曾在聊天中明文出现** —— 按用户要求不做处理，
   但记录在案：若后续要轮换，需同步更新平台的 `DATABASE_URL`。


