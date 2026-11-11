"""真实浏览器渲染后的纯中文可见区块检查（W-10 DoD 的"逐页截图自查"自动化）。

为什么需要这个
--------------
`scripts/check_ui_english.py` 是**静态**检查（读 HTML 源码）。
但 DoD 要的是"五页逐页截图自查，可见区域无纯中文段落"——
静态检查漏得掉一种情况：**JS 在运行时插入的中文**。
（比如 budget.html 的 AG Studio KPI 标签是 createStudio/setState 里预定义的，
静态看是双语，但渲染后可能被截断成纯中文。）

所以这个脚本用 Chrome headless 真实渲染，剥掉 script/style，
再检查剩下的可见文本节点有没有"纯中文"。

用法：
    python scripts/check_ui_rendered.py                  # 起服务 → 检查 → 关服务
    python scripts/check_ui_rendered.py --url http://...  # 检查已跑起来的服务
    python scripts/check_ui_rendered.py --keep-alive      # 保持服务运行（便于人工看）
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PAGES = (
    ("/", "index"),
    ("/constraints", "constraints"),
    ("/approvals", "approvals"),
    ("/audit", "audit"),
    ("/budget", "budget"),
)

# 纯中文判定：中文占可见字符 > 60%，且长度 > 3（滤掉单个汉字的图标/符号）
_PURE_ZH = re.compile(r"[\u4e00-\u9fff]")
_LATIN = re.compile(r"[A-Za-z]")

# Windows 上的 Chrome / Edge 路径
BROWSER_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def find_browser() -> str | None:
    for p in BROWSER_CANDIDATES:
        if os.path.exists(p):
            return p
    return shutil.which("chrome") or shutil.which("google-chrome") or shutil.which("msedge")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for(url: str, timeout: float = 40.0) -> bool:
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.6)
    return False


def render(browser: str, url: str, budget_ms: int = 11000) -> str:
    """用 headless 浏览器真实渲染一页，返回 DOM。"""
    out = subprocess.run(
        [
            browser, "--headless=new", "--disable-gpu", "--no-sandbox",
            "--virtual-time-budget=%d" % budget_ms,
            "--dump-dom", url,
        ],
        capture_output=True, timeout=90,
    )
    return out.stdout.decode("utf-8", "replace")


def visible_text_nodes(dom: str) -> list[str]:
    """剥掉 script/style，取出所有可见文本节点。

    i18n 三态：带 data-en/data-zh 的元素，文本由 i18n.js 按模式重写
    （zh 模式必然是纯中文）。那是用户主动选的语言，不是"漏改"，
    所以这些元素的文本不进检查范围 —— 否则切到中文就永远报失败。
    """
    clean = re.sub(r"<script[^>]*>.*?</script>", " ", dom, flags=re.S | re.I)
    clean = re.sub(r"<style[^>]*>.*?</style>", " ", clean, flags=re.S | re.I)
    clean = re.sub(r"<!--.*?-->", " ", clean, flags=re.S)
    clean = re.sub(r"<!DOCTYPE[^>]*>", " ", clean, flags=re.I)

    tag = re.compile(r"<(/?)([a-zA-Z][\w-]*)([^>]*)>")
    void = {"br", "img", "input", "meta", "link", "hr", "source", "area",
            "base", "col", "embed", "param", "track", "wbr"}
    managed = re.compile(r"\bdata-(?:en|zh|ph-en|ph-zh)=")

    nodes: list[str] = []
    stack: list[bool] = []
    skip = 0
    pos = 0
    for m in tag.finditer(clean):
        chunk = clean[pos:m.start()].strip()
        if chunk and skip == 0:
            nodes.append(chunk)
        pos = m.end()
        closing, name, attrs = m.group(1), m.group(2).lower(), m.group(3)
        if name in void:
            continue
        if closing:
            if stack and stack.pop():
                skip -= 1
        else:
            hit = bool(managed.search(attrs))
            stack.append(hit)
            if hit:
                skip += 1
    tail = clean[pos:].strip()
    if tail and skip == 0:
        nodes.append(tail)
    return nodes


def is_pure_chinese(text: str) -> bool:
    """纯中文可见区块：有中文、几乎无英文、长度够大。"""
    if len(text) <= 3:
        return False
    if not _PURE_ZH.search(text):
        return False
    han = len(_PURE_ZH.findall(text))
    latin = len(_LATIN.findall(text))
    # 数字/标点不算英文，所以只看"中文多 + 英文几乎没有"
    return han / len(text) > 0.6 and latin <= 2


def check(base: str, browser: str) -> int:
    print("=" * 84)
    print("W-10 渲染后检查 —— 真实浏览器渲染，验证可见区域无纯中文段落")
    print("=" *84)
    print(f"浏览器: {browser}")
    print(f"目标  : {base}")
    print()

    failed = False
    for path, name in PAGES:
        url = base + path
        dom = render(browser, url)
        if not dom.strip():
            print(f"  ❌ {path:14s} 渲染失败（DOM 为空）")
            failed = True
            continue

        nodes = visible_text_nodes(dom)
        pure = [n for n in nodes if is_pure_chinese(n)]
        zh_nodes = [n for n in nodes if _PURE_ZH.search(n)]
        bilingual = len(zh_nodes) - len(pure)

        status = "✅" if not pure else "❌"
        if pure:
            failed = True
        print(f"  {status} {path:14s} 可见文本节点 {len(nodes):>4}　"
              f"含中文 {len(zh_nodes):>3}（双语 {bilingual}）　纯中文 {len(pure)}")
        for n in pure[:5]:
            print(f"       ⚠️ 纯中文: {n[:70]}")

    print("-" * 84)
    if failed:
        print("❌ 未通过 —— 存在纯中文可见区块")
        return 1
    print("✅ 通过 —— 五页渲染后可见区域均无纯中文段落（双语保留）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="W-10 渲染后纯中文检查")
    ap.add_argument("--url", default="", help="已跑起来的服务地址（留空则自动起一个）")
    ap.add_argument("--keep-alive", action="store_true", help="检查完不关服务")
    args = ap.parse_args()

    browser = find_browser()
    if not browser:
        print("❌ 找不到 Chrome / Edge —— 装一个 Chrome 或 Edge 后重试")
        print("   （这个检查需要真实浏览器渲染，静态检查请用 scripts/check_ui_english.py）")
        return 2

    proc = None
    base = args.url.rstrip("/")
    if not base:
        port = free_port()
        base = f"http://127.0.0.1:{port}"
        root = Path(__file__).resolve().parent.parent
        py = sys.executable
        print(f"启动服务: {base}")
        proc = subprocess.Popen(
            [py, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=str(root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if not wait_for(base + "/healthz"):
            print("❌ 服务启动超时")
            if proc:
                proc.terminate()
            return 2

    try:
        rc = check(base, browser)
    finally:
        if proc and not args.keep_alive:
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except Exception:
                proc.kill()

    return rc


if __name__ == "__main__":
    raise SystemExit(main())
