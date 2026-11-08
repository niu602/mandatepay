#!/usr/bin/env python
"""生成部署环境变量清单 `deploy.env`（供平台控制台整段粘贴）。

为什么需要这个脚本
------------------
`deploy.env` 曾经是手工拼出来的，结果**过期了**：
里面留着 127.0.0.1 的本机 DATABASE_URL（旧 ngrok 的 webhook id 也是）。

手工维护的部署清单必然会漂移 —— 它不在 git 里（gitignore），
没人会记得去更新它，而下一个照着粘贴的人会把本机地址部署到公网上。

所以把它变成**可重生成的产物**：跑一次这个脚本，
值永远来自当前的 `.env`，不会过期。

用法
----
```bash
python scripts/gen_deploy_env.py --database-url <线上连接串>   # 生成 deploy.env
python scripts/gen_deploy_env.py --print                      # 只打印（打码）
python scripts/gen_deploy_env.py --check                      # 检查是否与 .env 一致
```

**为什么要显式传 `--database-url`**：
本地 `.env` 的 `DATABASE_URL` 指向 **127.0.0.1 的本机 PostgreSQL**（本地开发用），
而部署到公网必须连**云端数据库**（本项目用 Neon 免费 PostgreSQL）。

如果照搬 `.env`，部署出来的服务会连 `127.0.0.1` —— 在 Azure 上就是连自己，
必然连不上自己的库。这是**已经踩过的坑**：仓库里遗留的旧 `deploy.env`
就带着 127.0.0.1 的 DATABASE_URL。

所以这个脚本默认会**拒绝**生成本机地址，并要求显式传入线上连接串：

```bash
# 从 Neon 控制台拿连接串（Console → Connect → URI）
python scripts/gen_deploy_env.py --database-url "postgresql://user:pass@ep-xxx.neon.tech/db?sslmode=require"
```

`--check` 的退出码：0 = 一致；1 = 不一致或发现危险值（该重新生成了）。

安全
----
`deploy.env` 含明文密钥，已在 `.gitignore` 与 `.dockerignore` 里。
这个脚本**不会**打印密钥到 stdout（除非 `--print`，那时也会打码）。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
OUT_PATH = ROOT / "deploy.env"

# ── 部署需要的变量（顺序即粘贴顺序）────────────────────────
# 部署专用但本地 .env 没有的 → 直接写死
FIXED = {
    "PAYPAL_MODE": "sandbox",
    "LLM_BASE_URL": "https://api.z.ai/api/paas/v4",
    "LLM_MODEL": "glm-4.7-flash",
    "LLM_THINKING": "disabled",
    "PYTHON_VERSION": "3.11.9",
}

# 从 .env 取的（值敏感）
FROM_ENV = [
    "DATABASE_URL",
    "PAYPAL_CLIENT_ID",
    "PAYPAL_CLIENT_SECRET",
    "PAYPAL_WEBHOOK_ID",
    "LLM_API_KEY",
]

# 明确不部署的（本地专用）
EXCLUDE = {
    "NGROK_AUTHTOKEN",  # 本地隧道用
    "LLM_TIMEOUT",      # 有默认值 60
}


def read_env() -> dict[str, str]:
    """读 .env（不依赖 python-dotenv —— 注释与空行都要正确处理）。"""
    out: dict[str, str] = {}
    if not ENV_PATH.exists():
        return out
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        out[k] = v
    return out


def _is_local_db(url: str) -> bool:
    """判断 DATABASE_URL 是不是本机地址 —— 这是最危险的一种过期。"""
    return bool(re.search(r"@(127\.0\.0\.1|localhost|0\.0\.0\.0)[:/]", url))


def _is_ngrok(url: str) -> bool:
    return "ngrok" in url.lower()


def build(env: dict[str, str], database_url: str | None = None) -> tuple[list[str], list[str]]:
    """生成内容行 + 警告列表。

    `database_url` 显式传入线上连接串；不给就用 .env 的（本机地址 → 会警告）。
    """
    lines: list[str] = []
    warnings: list[str] = []

    for key in FROM_ENV:
        val = env.get(key, "")
        if key == "DATABASE_URL" and database_url:
            val = database_url
        if not val:
            warnings.append(f"{key} 在 .env 里为空 —— 留空会让部署后功能不完整")
            lines.append(f"{key}=")
            continue
        if key == "DATABASE_URL" and _is_local_db(val):
            warnings.append(
                f"DATABASE_URL 是**本机地址**（{val.split('@')[-1]}）—— "
                "部署到公网后连不上自己的库！必须换成本机之外的数据库连接串"
            )
        lines.append(f"{key}={val}")

    for key, val in FIXED.items():
        lines.append(f"{key}={val}")

    return lines, warnings


def main() -> int:
    ap = argparse.ArgumentParser(description="生成部署环境变量清单 deploy.env")
    ap.add_argument("--print", action="store_true", help="只打印，不写文件")
    ap.add_argument("--check", action="store_true", help="检查 deploy.env 是否与 .env 一致")
    ap.add_argument(
        "--database-url",
        metavar="URL",
        help=(
            "线上数据库连接串（必填，除非 .env 里已是非本机地址）。"
            "本地 .env 通常指向 127.0.0.1，部署必须换云端库。"
        ),
    )
    args = ap.parse_args()

    env = read_env()
    if not env:
        print(f"❌ 读不到 {ENV_PATH} —— 先配好 .env")
        return 1

    body, warnings = build(env, args.database_url)

    header = [
        "# ── MandatePay · 部署环境变量清单 ──────────────────────",
        "# ⚠️  本文件由 scripts/gen_deploy_env.py 生成 —— 不要手工编辑！",
        "#     手工维护会过期（本仓库就踩过：DATABASE_URL 留着 127.0.0.1 本机地址）。",
        "#     改了 .env 后跑 `python scripts/gen_deploy_env.py` 重新生成。",
        "#",
        "# 用法：平台控制台 → Environment Variables → Add from .env → 粘下面全部内容",
        "# （.env 格式：一行一个 KEY=VALUE，# 开头是注释）",
        "#",
        "# ⚠️  含明文密钥 —— 已 gitignore，绝不要提交或分享。",
    ]
    content = "\n".join(header + body) + "\n"

    if warnings:
        print("⚠️  生成时发现的问题：")
        for w in warnings:
            print(f"   · {w}")
        print()

    if args.check:
        if not OUT_PATH.exists():
            print(f"❌ {OUT_PATH.name} 不存在 —— 跑 `python scripts/gen_deploy_env.py` 生成")
            return 1
        current = OUT_PATH.read_text(encoding="utf-8")
        if current == content:
            print(f"✅ {OUT_PATH.name} 与 .env 一致")
            return 0
        print(f"❌ {OUT_PATH.name} 与 .env 不一致 —— 跑 `python scripts/gen_deploy_env.py` 重新生成")
        return 1

    # 有本机地址的警告 → **拒绝写文件**（--print / --check 只是查看，不拦）
    dangerous = [w for w in warnings if "本机地址" in w]
    if dangerous and not args.database_url and not args.check and not args.print:
        print("❌ 拒绝生成 deploy.env：")
        for w in dangerous:
            print(f"   · {w}")
        print()
        print("请显式传入线上数据库连接串：")
        print('   python scripts/gen_deploy_env.py --database-url "postgresql://user:pass@host/db?sslmode=require"')
        return 1

    if args.print:
        # --print 优先显示**已生成的 deploy.env**（若存在）——
        # 因为那是真正要粘贴的内容；.env 里的 DATABASE_URL 是本机地址，
        # 拿 .env 打印会误导（也过不了上面的拒绝逻辑）。
        target = OUT_PATH.read_text(encoding="utf-8") if OUT_PATH.exists() else content
        # 打码输出，避免密钥进终端历史/日志
        masked = []
        for ln in target.splitlines():
            if "=" in ln and not ln.startswith("#"):
                k, v = ln.split("=", 1)
                masked.append(f"{k}={v[:6]}***" if len(v) > 12 else ln)
            else:
                masked.append(ln)
        print("\n".join(masked))
        return 0

    OUT_PATH.write_text(content, encoding="utf-8")
    print(f"✅ 已生成 {OUT_PATH.name}（{len(body)} 个变量）")
    print("   用法：平台控制台 → Environment Variables → Add from .env → 粘全部内容")
    if warnings:
        print("\n⚠️  上面的问题请先解决再部署。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
