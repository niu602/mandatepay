#!/usr/bin/env python
"""重拍 README 用的五张页面截图（线上真实实例），并裁掉底部空白。

为什么要重拍
------------
已有的截图是 2026-11-08 拍的，之后前端改过两处：
  1. 五页导航统一成「英文在前」并加了 GitHub Source 入口
  2. /budget 加了 AG Studio license 注入逻辑
所以旧截图与现在的页面**对不上** —— README 引用旧图会误导读者。

顺带解决另一个问题：之前用固定 `--window-size=1440,2600` 截图，
内容不足时底部会留一大片空白，在 README 里很丑。这里自动裁掉。

Pillow 只用于本地裁图，**不进 requirements.txt**（会影响部署构建）。

用法：
```bash
python scripts/reshoot_readme_screenshots.py
```
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "screenshots"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
URL = "https://mandatepay-zisen-dph6eucmf4gjckct.eastasia-01.azurewebsites.net"

# (文件名, 路径, 说明, 需要等更久吗)
SHOTS = [
    ("01-pay.png", "/", "PayPal checkout", False),
    ("02-constraints.png", "/constraints", "Constraint setup", True),
    ("03-approvals.png", "/approvals", "Approval desk", True),
    ("04-audit.png", "/audit", "Three-layer Mandate audit", True),
    ("05-budget-agstudio.png", "/budget", "AG Studio budget dashboard", True),
]

WIDTH = 1440
TALL = 2600          # 先按高的截，最后裁
CROP_MIN_H = 260     # 低于这个高度说明页面异常，不裁


def shoot(path: str, out_file: Path, wait_ms: int) -> bool:
    """截一张图（未裁剪）。"""
    with tempfile.TemporaryDirectory() as tmp:
        cmd = [
            CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
            "--hide-scrollbars",
            f"--user-data-dir={Path(tmp) / 'p'}",
            f"--window-size={WIDTH},{TALL}",
            f"--virtual-time-budget={wait_ms}",
            f"--screenshot={out_file}",
            f"{URL}{path}",
        ]
        try:
            subprocess.run(cmd, capture_output=True, timeout=180)
        except subprocess.TimeoutExpired:
            return False
    return out_file.exists() and out_file.stat().st_size > 5000


def trim_bottom(path: Path) -> tuple[int, int]:
    """裁掉底部的页面背景色空白。返回 (原高, 裁后高)。

    ⚠️ 页面背景**不是纯白** —— 实测是 (247, 249, 252) 的浅灰蓝。
    所以不能按「纯白」判断（第一版用 >248 的阈值，底部全被漏掉，
    裁剪静默失效）。正确做法：取页面左上角的背景色作为参照，
    找最后一行「与背景色一致」的行。
    """
    try:
        from PIL import Image
    except ImportError:
        print("   ⚠️  没装 Pillow —— 跳过裁剪（底部会有空白，不影响可用）")
        print("      pip install Pillow   # 只用于本地裁图，不要加进 requirements.txt")
        with Image.open(path) as im:
            return im.size[1], im.size[1]

    with Image.open(path) as im:
        rgb = im.convert("RGB")
        w, h = rgb.size
        px = rgb.load()

        # 背景色参照：取左侧边缘中段的颜色（那里一定是背景）
        bg = px[5, min(200, h - 1)]

        def is_bg(c) -> bool:
            return all(abs(c[i] - bg[i]) <= 3 for i in range(3))

        # 从底部往上找第一行「不是背景色」的行
        last = 0
        for y in range(h - 1, -1, -1):
            row_has_content = False
            for x in range(0, w, 3):          # 抽样，避免逐像素太慢
                if not is_bg(px[x, y]):
                    row_has_content = True
                    break
            if row_has_content:
                last = y
                break

        if last == 0:
            return h, h                          # 整张都是背景，别裁
        new_h = min(h, last + 40)               # 留 40px 余量
        if new_h < CROP_MIN_H:
            return h, h                          # 太矮，判定异常
        cropped = rgb.crop((0, 0, w, new_h))
        cropped.save(path, "PNG", optimize=True)
        return h, new_h


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    ok = True
    for name, path, desc, needs_wait in SHOTS:
        out_file = OUT / name
        wait = 30000 if needs_wait else 15000
        print(f"→ {name}  ({desc})")
        if not shoot(path, out_file, wait):
            print(f"   ❌ 截图失败")
            ok = False
            continue
        before, after = trim_bottom(out_file)
        size_kb = out_file.stat().st_size / 1024
        print(f"   ✅ {size_kb:.0f} KB　高度 {before} → {after}px"
              f"{'（已裁底部空白）' if after < before else ''}")

    print()
    print("✅ 全部完成" if ok else "❌ 有失败项")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
