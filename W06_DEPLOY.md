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
| Render 免费 PostgreSQL | ❌ 不用 | **创建 30 天后删除** —— 11 月建库正好在评审期（12-01 ~ 12-15）内失效（R-09） |
| Supabase 免费层 | ❌ 不用 | **7 天无活动即暂停**，需手动恢复 |
| **Render 免费 Web Service** | ✅ **采用** | 2026 年**唯一还有真正免费通用套餐**的 PaaS（750 实例小时/月，512MB / 0.1 CPU / 100GB 带宽），**免费实例无需绑卡** |
| Zeabur 免费档 | ❌ 不用 | **2026-10-24 已宣布停止运营**：12-01 起共享集群暂停所有服务，12-31 全面停止 —— 正好撞上评审期 |
| Hugging Face Spaces | ❌ 不用 | 官方文档：Docker Space **需要付费计划**（免费只保留 Static 与 ZeroGPU Gradio） |
| Railway Free | ❌ 不用 | $5 试用是**一次性 30 天**、过期无循环额度（Hobby 每月 $5）；且 2026-10 安全事故后要求全体轮换密钥 |

**关键约束**：评审期是 12-01 ~ 12-15。Neon 只休眠不删库 → 安全；
Render 免费实例 15 分钟无访问即休眠（唤醒 30-60 秒），由评委访问触发，不影响数据。

### Neon 连接串（已验证可用）

```
postgresql://neondb_owner:***@ep-soft-sky-arct6yay-pooler.c-4.us-west-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require
```

实测：连接成功（PostgreSQL 18.6）、六张表已建、约束已写入、目录已同步、已清场。
注意连接串用的是 **pooler 端点**（`-pooler.`），Neon 的连接池对 serverless 部署更友好。

---

## 二、方案修订过程（两次走错的记录）

工单原本指定 Render。执行中发现两个"看起来过不去"的坎，逐一查证后结论如下。

### 2.1 坎一：Render 的 Stripe "Add Card" 弹窗

**现象**：点 Blueprint → 弹出「Add Card — Add credit card to verify your identity」，
Stripe 托管，`$1 USD 临时授权不扣费`，右上角只有 ×，**关闭后无法继续部署**。

**查证结论：这个弹窗是 Blueprint 路径特有的，绕过办法是改走「直接创建 Web Service」。**

Render 官方 FAQ 原话（2026-10 实测存档）：

> "注册页面只有邮箱加密码，或者用 GitHub、GitLab、Bitbucket、Google 账号登录，
> **没有填卡这一格**。加卡的时候会扣 1 美元做验证，交易完成后退回……
> **不加卡，免费实例照样能用。**"

多方 2026 年实测一致：注册 → Dashboard → **New + → Web Service** → 选仓库 →
Instance Type 选 **Free** → 全程**不要求绑卡**。绑卡只是升级付费档或加购资源时的前置。

> 也就是说，工单截图里那个弹窗并不是"Render 免费档必须绑卡"，
> 而是"Blueprint 这条路径会拉起支付校验"。**改走手动创建即可绕过。**

### 2.2 坎二：银联卡能不能绑？

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

> 但既然直接创建 Web Service 就不需要卡，**最省事的做法是根本不绑卡**。

### 2.3 中途排除的两个平台（避免后来者重走）

**Zeabur —— 已停运**（官方公告 zeabur.com/blogs/zeabur-is-shutting-down）：

> "12 月 1 日起，共享集群：**暂停所有正在运行的服务**。你仍然可以手动恢复服务运行，以便进行数据备份。
> 12 月 31 日：所有独立服务器到期关闭，所有共享集群停止服务。"

评审期是 12-01 ~ 12-15 —— **服务在评审第一天就被暂停**，比 Render 免费 PG 的 30 天删除更致命。
（背景：2026 年下半年两起安全事件后决定结束运营。）

**Hugging Face Spaces —— Docker Space 现已需要付费计划**（官方文档）：

> "Static Spaces are free for everyone.
> **Gradio and Docker Spaces run on compute and require a paid plan to create.**"

免费只保留 Static Space 与 ZeroGPU Gradio（个人号限 2 个）。Docker 跑任意 Web 后端已不免费。
（其余限制：仅 80/443/8080 端口、16GB RAM/2 vCPU、50GB 非持久磁盘。）

---

## 三、Render 部署步骤（走手动 Web Service，绕过 Blueprint）

### 步骤 1：注册（不绑卡）

1. 打开 https://render.com → **用 GitHub 账号登录**（同时授权仓库 `niu602/mandatepay`）
2. **不要**进入任何 Billing / 计划升级页面（那里才会要求绑卡）

### 步骤 2：创建 Web Service（关键：不要用 Blueprint）

Dashboard → **New + → Web Service** → 选仓库 `niu602/mandatepay` → Connect

#### 2.1 主表单（Build & Deploy 区域）—— 逐项照抄

| 表单项 | 填什么 | 说明 |
|---|---|---|
| **Name** | `mandatepay` | 决定域名 `mandatepay.onrender.com` |
| **Region** | **Oregon**（或 Singapore） | 与 Neon 同区时数据库延迟最低；Neon 建的是 `us-west-2` → **选 Oregon** |
| **Branch** | `master` | ⚠️ 本仓库主分支是 `master` 不是 `main` |
| **Language / Runtime** | **Python** | 会自动带出 Python 运行时 |
| **Root Directory** | 留空 | 仓库根目录即项目根 |
| **Build Command** | `pip install -r requirements.txt` | 也可以留空（Render 会自动检测 requirements.txt），但显式写更稳 |
| **Start Command** | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` | **必须绑定 `0.0.0.0`** 且用 `$PORT`（Render 默认注入 `PORT=10000`） |
| **Instance Type / Plan** | **Free** | 512MB / 0.1 CPU / 750 小时/月 |
| **Auto Deploy** | **Yes** | push 到 master 自动重新部署 |

#### 2.2 Advanced 区域（可折叠的那一栏）

| 选项 | 设置 | 为什么 |
|---|---|---|
| **Environment Variables** | 见 §2.3，填 9 个 | 必填 |
| **Health Check Path** | `/api/health` | 我们的健康检查端点已存在（`app/main.py`）。留空也能跑，但填了能拿到正确的健康状态 |
| **Pull Request Previews** | 不启用 | 单人项目，用不上 |
| **Private Service** | **不启用** | 启用后只有同 workspace 的付费服务能访问，评委就访问不到了 |
| **IP Allowlist** | 不启用 | 同上，别把自己关在门外 |
| **Persistent Disk** | **不添加** | Free 实例不支持持久盘；且我们的数据在 Neon，无需本地盘 |
| **Docker Command / Context / Dockerfile Path** | 留空 | 我们走 **Python 原生运行时**，不用 Docker。（若选 Docker 运行时才需填 `Dockerfile`） |
| **Dockerignore Path** | 留空 | 同上 |

#### 2.3 Environment Variables —— 9 个，**全部加进"Environment Variables"分组**

Render 会自动注入一批 `RENDER_*` 变量和 `PORT`，**不用自己填**。

| Key | Value | 是否敏感 |
|---|---|---|
| `DATABASE_URL` | 你的 Neon 连接串（整条，含 `?sslmode=require&channel_binding=require`） | 🔑 敏感 |
| `PAYPAL_CLIENT_ID` | PayPal 沙箱 Client ID | 🔑 敏感 |
| `PAYPAL_CLIENT_SECRET` | PayPal 沙箱 Secret | 🔑 敏感 |
| `PAYPAL_WEBHOOK_ID` | PayPal Webhook ID | 🔑 敏感 |
| `LLM_API_KEY` | Z.ai key | 🔑 敏感 |
| `PAYPAL_MODE` | `sandbox` | 普通 |
| `LLM_BASE_URL` | `https://api.z.ai/api/paas/v4` | 普通 |
| `LLM_MODEL` | `glm-4.7-flash` | 普通 |
| `LLM_THINKING` | `disabled` | 普通 |
| **`PYTHON_VERSION`** | **`3.11.9`** | ⚠️ **必加**，见下 |

> **⚠️ `PYTHON_VERSION` 一定要设成 `3.11.9`**
> Render 对 2026-10-12 之后新建的服务**默认用 Python 3.14.8**。
> 我们的依赖链（langgraph 1.2 / langchain 1.4 / pydantic 2.14）本地是在 **3.13.16** 上
> 完整验证的，3.14 是个很新的版本 —— pydantic-core 等含 C 扩展的包可能没有预编译 wheel
> 而触发源码编译，构建变慢甚至失败。
> 锁 3.11.9 是 Render 长期支持的稳定版本（最低支持 3.7.3，Debian 12 bookworm 基线），
> 与 3.13 的依赖兼容性差异极小。
> 也已在仓库根加了 `.python-version` 文件（内容 `3.11.9`）—— Render 官方支持这种写法，
> 两种方式等效，env var 优先。

#### 2.4 提交后

点 **Create Web Service** → 看实时构建日志 → 首次约 2-4 分钟（pip install langchain 链）→
状态变 **Live** → 拿到 `https://mandatepay.onrender.com`

### 步骤 3：部署后验证（无需在 Render 上执行命令）

> 💡 **注意**：Render **Free 实例没有 Shell/SSH**（官方明确列为不支持的功能），
> 所以不能像 Render 付费实例那样进 Shell 跑命令。
> **好消息是 Neon 侧的建表/种子/清场我已经在本地连库执行完了**，
> 部署完直接访问即可，不需要任何额外操作。

若日后换了新库，在**本地**跑（不需要 Render Shell）：

```bash
DATABASE_URL=<neon连接串> bash scripts/deploy_init.sh
```

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
| `render.yaml` | Render Blueprint（IaC 路径，本次不用 —— 会触发卡片弹窗） |
| `Dockerfile` | 容器镜像，`$PORT` 兼容各平台端口注入，`exec` 形式保证正确接收 SIGTERM |
| `.dockerignore` | 排除 `.env` / `node_modules` / 本地数据库 / `static/vendor`，防密钥进镜像 |
| `Procfile` | 启动命令（若平台走原生模式） |
| `scripts/deploy_init.sh` | 一键：建表 → 种子约束 → 同步目录 → 清场 |

### 4.3 本地验证结果

```
✅ 五个页面全部 200：/ /constraints /budget /approvals /audit
✅ /static/budget.html                200
✅ /static/vendor/ag-studio.mjs       200（本地 ESM 命中，不再走 CDN）
✅ 启动建表：postgresql schema 就绪
✅ 模拟 Render 启动命令（0.0.0.0 + $PORT + env）：六个端点全部 200
✅ deploy_init.sh 全流程跑通（建表→约束→目录→清场）
✅ 清场后应用正常：约束 $200 / 预算已花 $0
✅ Neon 实连：PG 18.6，六表就绪，约束已写，目录已同步，已清场
✅ Procfile 命令 + Neon 生产库 + 完整环境变量 → 六端点全 200
✅ 真实 LLM 链路：连 Neon + Z.ai 走 function calling，解析正确
✅ DSN 兼容：postgres:// 与 postgresql:// 都能用（store._effective_dsn 归一化）
✅ 应用常驻内存 60MB（Render 免费档 512MB 有充足余量）
```

### 4.4 冷启动预判（实测前）

| 项 | 实测值 | 判断 |
|---|---|---|
| 应用 import 耗时 | **0.68s** | 运行时不构成瓶颈 |
| 应用常驻内存 | **60MB** | Render 512MB 充足 |
| `paypal-agent-toolkit` 传递依赖 | **仅 httpx / pydantic / requests** | 很轻，工单担心的"依赖过重"**不成立** |
| `uvicorn[standard]` | 含 httptools / uvloop / watchfiles | 需编译或取预编译 wheel → **构建期**可能慢（不是运行期） |
| langchain 链 | langgraph + langchain + langchain-openai | 中等，pip 安装约 1-2 分钟 |

> 结论：冷启动风险主要在**构建阶段**（pip install），而非运行时。
> 工单给的精简顺序（① 裸 uvicorn ② 查 toolkit 依赖）**已预先验证第 ② 条不适用**。

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
| 首次访问（冷） | 待填 | Render 实例启动 + 应用初始化 |
| 休眠后唤醒 | 待填 | 15 分钟无访问即休眠，官方称唤醒约 1 分钟 |

**若冷启动 > 90 秒**，按工单顺序精简：
1. `uvicorn[standard]` → 裸 `uvicorn`（去掉 httptools/uvloop/watchfiles 编译依赖）
2. 检查 `paypal-agent-toolkit` 传递依赖 —— **已验证：仅 httpx/pydantic/requests，不重**

---

## 七、已知限制（如实记录）

1. **API 无鉴权**（技术债 #12）：公网部署后 `/api/*` 全部暴露。
   本项目是 Demo 性质，评审期可接受；但**不要放任何真实数据**。
2. **实例 15 分钟休眠**：评委访问会触发唤醒（30-60 秒）。
   建议**演示前自己访问一次预热**，或接受首次加载慢。
3. **`/budget` 的 AG Studio 走 CDN**（若未在构建期装 `static/vendor/`）：
   `static/vendor/` 已 gitignore（npm 商业包），Render 构建时不会自动装。
   页面会自动回退 jsdelivr CDN —— **功能正常**，但首屏依赖外网。
4. **PayPal Webhook 需要公网 URL**：部署后把 webhook 地址改成
   `https://<你的域名>/api/webhook`（比 ngrok 稳定，评审期一直有效）。
5. **Neon 连接串（数据库密码）曾在聊天中明文出现** —— 按用户要求不做处理，
   但记录在案：若后续要轮换，需同步更新 Render 的 `DATABASE_URL`。


