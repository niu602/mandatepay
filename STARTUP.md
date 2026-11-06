# 本机开发速查（Start Here）

> 面向**已经在本地 clone 过的开发者**。第一次上手（拿 PayPal 凭证、15 分钟跑通）看
> [README.md 的 "Run it in 15 minutes"](README.md#run-it-in-15-minutes)。
>
> 本文档只解决三件事：
> 1. **最快启动**（本机已配好环境时的三条命令）
> 2. **日常开发循环**（改代码 → 验证 → 提交）
> 3. **Windows / Linux 差异** 与 **端口占用排查**
>
> 最后更新：2026-11-06　·　对应提交：`1aed3d0`（W-10）

---

## 一、最快启动（本机环境已就绪时）

项目根目录：`D:\paypal-ai-hackathon`（Windows）/ 仓库根（Linux）

### 三条命令

```bash
cd D:/paypal-ai-hackathon

# ① 建表（幂等，可反复跑）
./.venv/Scripts/python.exe scripts/init_db.py

# ② 种子数据（幂等；库非空时自动跳过）
./.venv/Scripts/python.exe scripts/seed_catalog_local.py

# ③ 起服务
./.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8888
```

打开 **http://127.0.0.1:8888/**

### 不用 venv 的话（依赖已在系统 Python 里）

```bash
cd D:/paypal-ai-hackathon
python scripts/init_db.py
python scripts/seed_catalog_local.py
python -m uvicorn app.main:app --host 127.0.0.1 --port 8888
```

> ⚠️ 系统 Python 是 **3.13.14**，仓库 `.python-version` 锁的是 **3.11.9**
> （平台默认 3.14 太新，`pydantic-core` 可能没有预编译 wheel）。
> 本机开发用 3.13 没问题，**部署到平台时必须设 `PYTHON_VERSION=3.11.9`**。

### 停止服务

在起服务的那个终端按 `Ctrl+C`。

---

## 二、启动后自检（30 秒）

```bash
# ① 存活探针（最轻，不查库不调 PayPal）
curl http://127.0.0.1:8888/healthz
#   → {"status":"ok"}

# ② 五个页面
for p in / /constraints /budget /approvals /audit; do
  printf "%-14s %s\n" "$p" "$(curl -s -o /dev/null -w '%{http_code}' -m 10 http://127.0.0.1:8888$p)"
done
#   → 五个都应是 200

# ③ 带 session 的 API（浏览器能拿到、裸脚本拿不到）
curl -s -c /tmp/ck.txt -o /dev/null http://127.0.0.1:8888/     # 先访问页面拿 cookie
curl -s -b /tmp/ck.txt http://127.0.0.1:8888/api/catalog | head -c 200
```

**为什么第 ③ 步要先访问页面？** W-08 起 `/api/*` 走 cookie session 鉴权：
页面路由访问时下发签名 cookie，页面内 fetch 同源自动携带。
所以**裸 `curl /api/mandates` 返回 401 是正确行为**，不是 bug：

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8888/api/mandates
#   → 401   ← 预期
```

豁免清单：`/healthz`、`/api/health`、`/api/webhook`（PayPal 服务器回调，不带浏览器 cookie）。

---

## 三、日常开发循环

### 改完代码后

```bash
# ① 语法 + 单元测试（113 条，约 2.4 秒，全离线）
./.venv/Scripts/python.exe -m pytest tests/ -q

# ② 密钥扫描（提交前必跑）
./.venv/Scripts/python.exe scripts/scan_secrets.py
```

> 测试用**临时 SQLite**（`tests/conftest.py` 把 `DATABASE_URL` 指向临时目录），
> **不会污染你的开发库**。若发现开发库被写了数据，说明有测试漏了 mock。
> 快速自查：
>
> ```bash
> ./.venv/Scripts/python.exe -c "
> import sys; sys.path.insert(0,'.')
> from agent import store
> with store._Conn() as db:
>     for t in ('mandates','purchase_requests'):
>         print(t, db.cur().execute(f'SELECT COUNT(*) n FROM {t}').fetchone()['n'])
> "
> ```

### 改了前端之后（必做）

```bash
# 静态检查（秒级）
./.venv/Scripts/python.exe scripts/check_ui_english.py

# 真实浏览器渲染检查（Chrome headless，会自动起服务）
./.venv/Scripts/python.exe scripts/check_ui_rendered.py
```

**两个都要跑。** 静态检查读源码，渲染检查跑 Chrome 后看真实 DOM ——
后者能抓到静态检查漏掉的一类问题：**JS 运行后才出现的中文**
（例：`<h2>Budget Overview <span>预算总览</span>` 中文被 span 切成独立文本节点，
渲染后是一行连续双语，但 DOM 里中文独占节点）。

### 提交

```bash
git add -A
git -c user.name="Niu Zisen" -c user.email="niu602@outlook.com" commit -F /tmp/msg.txt
```

**push 在非交互 shell 里需要 askpass 包装**（GitHub 凭据管理器在非交互下不弹窗）：

```bash
cat > /tmp/git-askpass.sh << 'EOF'
#!/bin/sh
case "$1" in
  *Username*) printf 'niu602' ;;
  *) printf '%s\n' "$(printf 'protocol=https\nhost=github.com\n\n' | '/c/Program Files/Git/cmd/git.exe' credential fill 2>/dev/null | sed -n 's/^password=//p')" ;;
esac
EOF
chmod +x /tmp/git-askpass.sh

GIT_ASKPASS=/tmp/git-askpass.sh GIT_TERMINAL_PROMPT=0 \
  "/c/Program Files/Git/cmd/git.exe" -c http.proxy= -c https.proxy= push origin master
```

> remote URL 保持干净（`https://github.com/niu602/mandatepay.git`，无 token），
> 凭据由 Windows 凭据管理器托管。

---

## 四、端口占用排查

**症状：起服务报 `address already in use` / `[Errno 98]`。**

Windows 上查占用：

```bash
netstat -ano | grep LISTENING | grep ":8888"
# 最后一列是 PID
taskkill //PID <PID> //T //F
```

一次性清掉本项目遗留的所有测试端口（8881–8899）：

```bash
for p in $(netstat -ano | grep LISTENING | grep -E "127.0.0.1:(888[0-9]|889[0-9])" | awk '{print $5}'); do
  taskkill //PID $p //T //F
done
```

> 为什么会堆积：每次做浏览器验证都会起一个 uvicorn，
> 而**后台 shell 结束后进程不一定被回收**。本项目已积累过 8 个（8881–8890）。
> 收工前跑一次上面的清理命令即可。

---

## 五、Windows / Linux 差异

| 事项 | Windows | Linux / macOS |
|---|---|---|
| venv 解释器 | `./.venv/Scripts/python.exe` | `./.venv/bin/python` |
| 激活脚本 | `.venv\Scripts\activate` | `source .venv/bin/activate` |
| `startup.sh` | 不可直接跑（Azure App Service 用它） | `bash startup.sh` |
| SQLite 绝对路径 | `sqlite:///D:/path/x.db` | `sqlite:////abs/path/x.db`（四斜杠） |
| 后台进程清理 | `taskkill //PID x //T //F` | `kill <PID>` |

> SQLite 路径两种写法都支持（`agent/store.py` 的 `_sqlite_path()` 会归一化，
> 且会自动创建父目录）。Unix 绝对路径会映射到系统临时目录，
> **不会在仓库里造 `tmp/` 污染工作树**。

---

## 六、常用端口

| 端口 | 用途 |
|---|---|
| 8888 | 本机开发默认（README 约定） |
| 8881–8899 | 验证用的临时端口（浏览器检查、对比实验）—— 收工前清理 |
| 5432 | 本地 PostgreSQL（`.env` 的 `DATABASE_URL` 指向它） |

**当前本机数据库**：`postgresql://***@127.0.0.1:5432/mandatepay`（本地 PG，账号密码在 `.env`）
换成 Neon 的连接串即可切到线上库（`store` 自动识别 `postgresql://`）。

---

## 七、故障速查

| 现象 | 原因 | 处理 |
|---|---|---|
| 起服务报端口占用 | 上次验证的 uvicorn 没退出 | 见 §四 |
| `/api/*` 全 401 | 没先访问页面拿 cookie | 见 §二 第 ③ 步 |
| `unable to open database file` | SQLite 父目录不存在 / 路径跨平台写法 | 用 `sqlite:///` + 绝对路径；`_sqlite_path()` 已自动 mkdir |
| 首次请求超时 | 首个请求才建表（线上场景） | 启动钩子已处理；本地跑一次 `init_db.py` 即可 |
| LLM 报 429 / 1305 | Z.ai 免费档过载 | 正常现象，客户端有指数退避；详见 `W05_ZAI_MIGRATION.md` |
| `/budget` 显示「运行时：本地 vendor」 | 本地装了 `static/vendor/` | 正常；线上走 jsdelivr CDN（`static/vendor/` 已 gitignore） |
| 前端中文检查报错 | 检查器口径问题而非页面问题 | 静态 + 渲染两个检查器都要跑，对照 README 的 "UI language" |

---

## 相关文档

| 文档 | 内容 |
|---|---|
| [README.md](README.md) | 项目全貌 + 陌生人 15 分钟跑通 |
| [W05_ZAI_MIGRATION.md](W05_ZAI_MIGRATION.md) | LLM 端点迁移 + 429 应对 |
| [W06_DEPLOY.md](W06_DEPLOY.md) | 部署平台选型（含几次走弯路的记录） |
| [W07_KEEP_WARM.md](W07_KEEP_WARM.md) | F1 保温 / 冷启动测量 |
| [W08_API_AUTH.md](W08_API_AUTH.md) | 会话鉴权设计与豁免清单 |
| [LLM_INTENT_VERIFICATION.md](LLM_INTENT_VERIFICATION.md) | 20 句 × 3 次意图解析报告 |
| [STRESS_REPORT.md](STRESS_REPORT.md) | 650 次判定压测报告 |
