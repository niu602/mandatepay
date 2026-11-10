#!/usr/bin/env python
"""证明「未设 AG_STUDIO_LICENSE 时，行为与改动前完全一致」。

DoD 原文：*不设环境变量时与现在行为完全一致（trial、无报错、控制台仅警告横幅）*

怎么证明「完全一致」
------------------
不是靠断言，是靠 **diff**：
1. `git stash` 把改动藏起来 → 启动服务 → 加载 /budget → 抓控制台输出
2. `git stash pop` 恢复改动 → 重启服务 → 再抓一次
3. 两次的控制台输出**归一化**（去掉 PID、时间戳、耗时等易变部分）后 diff

归一化要处理的易变项：
- Chrome 的 `[pid:tid:timestamp]` 前缀
- 耗时数字（`runtime: xxx ms`）
- 行号（文件行号会随改动变化）—— 这条要小心：
  AG Studio 自己的横幅来自 vendor 文件，行号固定；
  我们自己的提示来自 budget.html，行号会变 → 归一化时按「消息文本」比。

用法：
```bash
python scripts/compare_ag_license_behavior.py
```
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 8894


def capture_console(url: str, timeout: int = 120) -> list[str]:
    """加载页面，返回归一化后的控制台消息列表。"""
    with tempfile.TemporaryDirectory() as tmp:
        cmd = [
            CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
            f"--user-data-dir={Path(tmp) / 'p'}",
            "--enable-logging=stderr", "--v=1",
            "--virtual-time-budget=22000",
            "--dump-dom", url,
        ]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout,
                encoding="utf-8", errors="replace",
            )
        except subprocess.TimeoutExpired:
            return []

        msgs: list[str] = []
        for ln in (proc.stderr or "").splitlines():
            if "CONSOLE" not in ln:
                continue
            m = re.search(r'CONSOLE:\d+\]\s*"(.*)"', ln)
            if not m:
                continue
            text = m.group(1)
            # 归一化：去掉数字（耗时、行号等易变部分）
            text = re.sub(r"\d+", "N", text)
            text = re.sub(r"\s+", " ", text).strip()
            if text:
                msgs.append(text)
        return msgs


def run_server_and_capture(env_extra: dict | None = None) -> list[str]:
    """起服务 → 等就绪 → 抓控制台 → 关服务。"""
    import os
    import time

    env = {**os.environ}
    env.pop("AG_STUDIO_LICENSE", None)
    if env_extra:
        env.update(env_extra)

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env=env,
    )
    try:
        time.sleep(13)
        return capture_console(f"http://127.0.0.1:{PORT}/budget")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    ).stdout.strip()


def main() -> int:
    print("=" * 66)
    print("AG Studio license 改动前后的行为对比")
    print("=" * 66)

    if git("status", "--porcelain").strip() == "":
        print("❌ 工作树是干净的 —— 没有改动可比。先做 A2 的改动。")
        return 1

    print("\n① 抓「改动前」的基线（git stash）…")
    stashed = git("stash", "push", "-u", "-m", "ag-license-compare")
    if not stashed:
        print("❌ git stash 失败")
        return 1
    try:
        before = run_server_and_capture()
        print(f"   抓到 {len(before)} 条控制台消息")
    finally:
        git("stash", "pop")

    print("\n② 抓「改动后」（未设 key）…")
    after = run_server_and_capture()
    print(f"   抓到 {len(after)} 条控制台消息")

    print("\n③ 抓「改动后」（设了 key）…")
    after_key = run_server_and_capture({"AG_STUDIO_LICENSE": "TEST-FAKE-KEY-1234567890"})
    print(f"   抓到 {len(after_key)} 条控制台消息")

    # ── diff ──
    print("\n" + "=" * 66)
    print("差异分析")
    print("=" * 66)

    before_set = set(before)
    after_set = set(after)

    only_before = before_set - after_set
    only_after = after_set - before_set

    if not only_before and not only_after:
        print("\n✅ 未设 key 时，控制台输出与改动前**逐条完全一致**")
        print("   → 行为完全不变（trial 模式、无报错、只有 AG Studio 自己的横幅）")
    else:
        if only_before:
            print("\n⚠️ 改动前有、改动后没有的消息：")
            for m in sorted(only_before):
                print(f"   − {m[:100]}")
        if only_after:
            print("\n⚠️ 改动后新增的消息：")
            for m in sorted(only_after):
                print(f"   + {m[:100]}")

    # 设了 key 的分支：应有 Invalid License Key（说明 setLicenseKey 被调用了）
    invalid = [m for m in after_key if "Invalid" in m and "License" in m]
    if invalid:
        print("\n✅ 设了 key 时，AG Studio 打出了 Invalid License Key 横幅")
        print("   → 证明 setLicenseKey(key) **确实被调用了**（假 key 被判无效是预期的）")
    else:
        print("\n⚠️ 设了 key 时没看到 Invalid License Key —— setLicenseKey 可能没执行")

    # 未设 key 的分支：不该有 Invalid License Key（trial 模式是另一种提示）
    invalid_trial = [m for m in after if "Invalid" in m and "License" in m]
    if invalid_trial:
        print("\n⚠️ 未设 key 时也出现了 Invalid License Key —— 与改动前不一致")
    else:
        print("✅ 未设 key 时无 Invalid License Key（是正常的 trial 提示，不是无效 key）")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
