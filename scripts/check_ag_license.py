#!/usr/bin/env python
"""验证 AG Studio license 注入路径（DoD 验收）。

三条 DoD：
  1. 不设环境变量 → 行为与改动前**完全一致**（trial 模式、无报错、
     控制台只有警告横幅，AG Studio 正常渲染）
  2. 随便设一个值 → **不崩**（页面正常渲染，AG Studio 正常出图）
  3. scan_secrets 零命中（由提交前的扫描保证，这里只提醒）

怎么验的
--------
用 Chrome headless 加载 /budget，**捕获控制台输出**（--enable-logging），
再读 DOM 里状态条的文字：
  - 状态条含 "AG Studio rendered" → 渲染成功（没有被 license 逻辑打断）
  - 状态条含 "fell back to the classic view" → 渲染失败（license 逻辑破坏了流程）

控制台里出现 `[AG Studio] ...` 开头的 warn 是**预期**的（trial 模式提示）。

用法：
```bash
python scripts/check_ag_license.py                 # 只测当前环境
python scripts/check_ag_license.py --with-key      # 额外测「设了值」的分支
```
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

# AG Studio 的成功/失败标志（与 static/budget.html 里的文案一致）
OK_MARK = "AG Studio rendered the budget overview"
FALLBACK_MARK = "fell back to the classic view"


def find_chrome() -> str | None:
    for p in (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        shutil.which("google-chrome") or "",
        shutil.which("chromium") or "",
    ):
        if p and Path(p).exists():
            return p
    return None


def load_budget(url: str, timeout: int = 120) -> tuple[str, str]:
    """加载 /budget，返回 (DOM, 控制台输出)。"""
    chrome = find_chrome()
    if not chrome:
        return "", ""

    with tempfile.TemporaryDirectory() as tmp:
        profile = Path(tmp) / "prof"
        cmd = [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--hide-scrollbars",
            f"--user-data-dir={profile}",
            # ⚠️ 控制台捕获的关键是 `--enable-logging=stderr` + `--v=1`。
            #    用了 `--log-file=` 反而**抓不到**页面 console
            #    （实测：log-file 里没有 CONSOLE 行，stderr 里才有）。
            "--enable-logging=stderr",
            "--v=1",
            "--virtual-time-budget=25000",
            "--dump-dom",
            url,
        ]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout,
                encoding="utf-8", errors="replace",
            )
        except subprocess.TimeoutExpired:
            return "", ""

        dom = proc.stdout or ""
        console = proc.stderr or ""
        return dom, console


def check(dom: str, console: str, label: str) -> bool:
    """检查一次加载的结果。返回是否通过。"""
    print(f"\n=== {label} ===")

    ok = True

    # ① 渲染成功
    rendered = OK_MARK in dom
    fell_back = FALLBACK_MARK in dom
    if rendered:
        print(f"  ✅ AG Studio 渲染成功（状态条含 '{OK_MARK[:34]}…'）")
    else:
        ok = False
        if fell_back:
            print("  ❌ 掉回 classic 视图 —— license 逻辑破坏了渲染流程！")
        else:
            print("  ❌ 状态条里找不到渲染成功标志（可能没加载完）")

    # ② 控制台：不应有 error 级报错
    errors = [
        ln for ln in console.splitlines()
        if re.search(r"\[AG Studio\].*(error|failed|Error)", ln, re.I)
    ]
    if errors:
        ok = False
        print("  ❌ 控制台出现 AG Studio 相关的错误：")
        for e in errors[:3]:
            print(f"       {e.strip()[:120]}")
    else:
        print("  ✅ 控制台无 AG Studio 错误")

    # ③ 控制台输出
    # Chrome 把页面 console 输出成 `INFO:CONSOLE:<line>] "<text>", source: ...`
    con_lines = [ln for ln in console.splitlines() if "CONSOLE" in ln]
    mine = [ln for ln in con_lines if "[AG Studio]" in ln]
    ag_license_msgs = [
        ln for ln in con_lines
        if "License" in ln or "license" in ln
    ]

    if mine:
        print(f"  ℹ️  我们自己的 [AG Studio] 提示 {len(mine)} 条：")
        for w in mine[:3]:
            print(f"       {w.strip()[:110]}")
    else:
        print("  ℹ️  无 [AG Studio] 提示（key 为空时这是预期 —— 不打扰）")

    if ag_license_msgs:
        print(f"  ℹ️  AG Studio 自身的 license 横幅 {len(ag_license_msgs)} 行"
              f"（trial 模式本就有，不是本次改动引入的）：")
        for w in ag_license_msgs[:2]:
            print(f"       {w.strip()[:110]}")
    else:
        print("  ℹ️  无 AG Studio license 横幅")

    # ④ 断言：页面自己的 console 里不能有未捕获异常
    uncaught = [
        ln for ln in con_lines
        if "Uncaught" in ln or "Uncaught (in promise)" in ln
    ]
    if uncaught:
        ok = False
        print("  ❌ 有未捕获异常：")
        for u in uncaught[:3]:
            print(f"       {u.strip()[:120]}")
    else:
        print("  ✅ 无未捕获异常")

    # ④ 页面没崩：DOM 里有 studio 容器
    if 'id="studio"' in dom:
        print("  ✅ studio 容器存在（页面结构完整）")
    else:
        ok = False
        print("  ❌ 找不到 studio 容器 —— 页面可能崩了")

    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description="AG Studio license 注入路径验收")
    ap.add_argument("--url", default="http://127.0.0.1:8881", help="服务地址")
    ap.add_argument("--with-key", action="store_true", help="额外测「设了值」的分支")
    ap.add_argument("--key", default="TEST-FAKE-KEY-1234567890", help="测试用的假 key")
    args = ap.parse_args()

    budget = args.url.rstrip("/") + "/budget"
    print(f"目标: {budget}")
    print(f"AG_STUDIO_LICENSE 环境变量: {'（已设）' if __import__('os').environ.get('AG_STUDIO_LICENSE') else '（未设 → trial 模式）'}")

    ok = True

    # ── 分支 1：当前环境（不设或设了，取决于调用方）──
    dom, console = load_budget(budget)
    if not dom:
        print("❌ 页面加载失败 —— 先启动服务：")
        print("   python -m uvicorn app.main:app --host 127.0.0.1 --port 8881")
        return 1
    label = "已设 key" if __import__('os').environ.get('AG_STUDIO_LICENSE') else "未设 key（trial 模式）"
    ok &= check(dom, console, label)

    # ── 分支 2：另起一个带 key 的服务 ──
    if args.with_key:
        print("\n" + "─" * 62)
        print(f"另起一个设了 key 的实例（AG_STUDIO_LICENSE={args.key}）")
        print("─" * 62)
        env_proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app",
             "--host", "127.0.0.1", "--port", "8893"],
            cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env={**__import__("os").environ, "AG_STUDIO_LICENSE": args.key},
        )
        try:
            import time
            time.sleep(12)
            dom2, console2 = load_budget("http://127.0.0.1:8893/budget")
            if dom2:
                ok &= check(dom2, console2, "设了 key（不该崩）")
            else:
                ok = False
                print("  ❌ 带 key 的实例加载失败")
        finally:
            env_proc.terminate()
            try:
                env_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                env_proc.kill()

    print()
    print("=" * 62)
    print("✅ 全部通过" if ok else "❌ 有失败项")
    print("=" * 62)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
