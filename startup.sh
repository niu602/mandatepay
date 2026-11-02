#!/bin/bash
# Azure App Service 启动脚本
#
# 用法：App Service → Settings → Configuration → General settings → Startup Command
#      填 `bash startup.sh`
#
# ⚠️ 为什么用 `app.main:app` 而不是 `--app-dir app`：
# 我们的 FastAPI 实例在 app/main.py，且该模块内部用了**相对导入**
# （`from . import config, paypal_client`）。
# 用 `--app-dir app` 会把 app/main.py 提升成顶层模块 `main`，
# 相对导入立刻失效（ImportError: attempted relative import with no known parent package）——
# 本地实测过，不是纸上推演。
# 所以从仓库根启动，用完整包路径 `app.main:app`，相对导入才成立。
#
# 关键点（Azure 官方要求）：
# 1. 必须绑 0.0.0.0，不能只绑 127.0.0.1，否则外部流量到不了应用
# 2. 端口用 8000 —— App Service 内置容器的约定端口
#    （自定义 Docker 容器则用 WEBSITES_PORT 或 EXPOSE 声明的端口）
# 3. 仓库根要能作为工作目录 —— Azure 部署源码时默认就是根目录

exec python -m uvicorn app.main:app \
    --host 0.0.0.0 \
    --port "${WEBSITES_PORT:-8000}" \
    --timeout-keep-alive 120
