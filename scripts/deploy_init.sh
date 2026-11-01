#!/usr/bin/env bash
# Render 部署后的初始化脚本（一键建表 + 种子数据 + 清场）
#
# 用法（在 Render Shell 或本地连 Neon 时）：
#     bash scripts/deploy_init.sh
#
# 步骤：
#   1. 建表（幂等；应用启动时也会自动建，这里是显式版本 + 打印表清单）
#   2. 种子数据：设置演示约束（预算 $200 / 阈值 $50 / 白名单 Notion+Figma）
#   3. 种子数据：拉取供应商目录（Notion $10 / Figma $15）
#   4. 清场：清掉历史交易（预算按"已承诺"口径算，占满会让新订单直接 REJECT）
#
# 注意：第 3 步要调 PayPal API，沙箱凭证必须已在环境变量里配好。
set -euo pipefail

cd "$(dirname "$0")/.."
PY="${PYTHON:-python}"

echo "=== 1/4 建表 ==="
"$PY" scripts/init_db.py

echo
echo "=== 2/4 种子：演示约束 ==="
"$PY" - <<'PYEOF'
from agent import store
from agent.models import Constraint
c = Constraint(
    monthly_budget_usd=200.0,
    single_tx_threshold_usd=50.0,
    vendor_whitelist=["Notion", "Figma"],
    category_whitelist=[],
)
store.save_constraint(c)   # created_at 留空由 store 自动填
print("约束已写入:", c.model_dump())
PYEOF

echo
echo "=== 3/4 种子：供应商目录（需 PayPal 沙箱凭证）==="
"$PY" scripts/setup_catalog.py || echo "⚠️ 目录同步失败（不影响主流程，Agent 仍可解析明确指定供应商的请求）"

echo
echo "=== 4/4 清场（清掉历史交易，保留约束）==="
"$PY" scripts/reset_demo_data.py --confirm

echo
echo "✅ 部署初始化完成。"
