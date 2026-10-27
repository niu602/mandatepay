"""安装 AG Studio 前端运行时到 static/vendor/。

背景：项目是 FastAPI + 纯静态 HTML（**无打包器、无 node_modules 依赖**），
而 AG Studio 只发 npm 包（`ag-studio`），且产物是带**裸依赖**的 ESM
（`from "ag-stack"` 等），没有 UMD 浏览器直引版。

所以纯 HTML 里必须用 `<script type="importmap">` 逐级映射裸 specifier。
本脚本做这件事：
  1. 在临时目录 `npm install ag-studio`
  2. BFS 解析**传递性**裸 import，定位每个包的实际 ESM 入口
  3. 把每个包的 **min 版** ESM 拷到 `static/vendor/<pkg>.mjs`
  4. 写出 `static/vendor/importmap.json`（页面直接内联同一份内容）

用法：
    python scripts/setup_ag_studio.py            # 安装
    python scripts/setup_ag_studio.py --check    # 只校验 vendor 是否就绪

注意：AG Studio 是商业产品（npm license = Commercial），无 license key 时
控制台会打印 trial 警告但**功能全开**、UI 无水印。要去水印需申请 trial key。
"""
from __future__ import annotations

import argparse
import os
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
VENDOR_DIR = BASE_DIR / "static" / "vendor"
ENTRY = "ag-studio"

# from "ag-xxx" / import("ag-xxx")
# 只匹配「像包名」的 specifier —— 否则压缩产物里 from 作为变量名会大量误判
# （例如 `,to:a=>s(a,` 会被当成 from "…"）。
BARE = re.compile(
    r"""(?:^|[;{}\s])(?:from|import)\s*\(?\s*["']([A-Za-z@][A-Za-z0-9@/_.-]*)["']""",
    re.MULTILINE,
)
# 仅在 React 代码生成字符串里出现、vanilla 运行路径不会加载的包
IGNORE = {"ag-grid-react", "ag-charts-react"}


def _bare_specs(text: str) -> set[str]:
    """抽取文本里真实的裸 import specifier 集合。"""
    out = set()
    for m in BARE.finditer(text):
        spec = m.group(1)
        if spec in IGNORE:
            continue
        out.add(spec)
    return out


def _run(cmd: list[str], cwd: Path) -> None:
    """执行一条命令。

    Windows 上 npm 实为 npm.cmd，CreateProcess 不能直接跑 .cmd，
    必须经 cmd.exe（shell=True）或显式解析成完整路径。
    """
    exe = shutil.which(cmd[0])
    if exe is None:
        raise RuntimeError(f"找不到可执行文件: {cmd[0]}（请确认已安装 Node.js）")
    print("  $ " + " ".join(cmd))
    subprocess.run(cmd, cwd=cwd, check=True, shell=os.name == "nt",
                   stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)


def _entry_file(pkg_dir: Path) -> Path | None:
    """定位包的 ESM 入口（优先 module 字段，其次常见 dist 路径）。"""
    meta = json.loads((pkg_dir / "package.json").read_text(encoding="utf-8"))
    for cand in (meta.get("module"), meta.get("main")):
        if cand and (pkg_dir / cand).exists():
            return pkg_dir / cand
    for alt in ("dist/package/main.esm.min.mjs", "dist/package/main.esm.mjs",
                "dist/main.esm.min.mjs", "dist/main.esm.mjs"):
        if (pkg_dir / alt).exists():
            return pkg_dir / alt
    return None


def _min_variant(pkg_dir: Path, entry: Path) -> Path:
    """同一包内优先用 min 版（体积减半）。"""
    if entry.name == "main.esm.mjs":
        mini = entry.with_name("main.esm.min.mjs")
        if mini.exists():
            return mini
    return entry


def install() -> int:
    VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="agstudio-") as tmp:
        tmpdir = Path(tmp)
        print("① npm install ag-studio（临时目录）…")
        _run(["npm", "init", "-y"], tmpdir)
        _run(["npm", "install", "ag-studio", "--no-audit", "--no-fund"], tmpdir)

        node_modules = tmpdir / "node_modules"
        print("② 解析传递性裸依赖…")
        seen: set[str] = set()
        queue = [ENTRY]
        graph: dict[str, Path] = {}
        while queue:
            pkg = queue.pop(0)
            if pkg in seen:
                continue
            seen.add(pkg)
            pkg_dir = node_modules / pkg
            if not pkg_dir.exists():
                print(f"   ⚠️  依赖缺失，跳过: {pkg}")
                continue
            entry = _entry_file(pkg_dir)
            if entry is None:
                print(f"   ⚠️ 找不到 ESM 入口，跳过: {pkg}")
                continue
            graph[pkg] = _min_variant(pkg_dir, entry)
            text = graph[pkg].read_text(encoding="utf-8", errors="ignore")
            for spec in _bare_specs(text):
                if (node_modules / spec).exists() and spec not in seen:
                    queue.append(spec)

        print("③ 拷贝 ESM 到 static/vendor/ …")
        import_map: dict[str, str] = {}
        total = 0
        for pkg, path in graph.items():
            dest = VENDOR_DIR / f"{pkg}.mjs"
            shutil.copy2(path, dest)
            size = dest.stat().st_size
            total += size
            import_map[pkg] = f"/static/vendor/{pkg}.mjs"
            print(f"   ✅ {pkg:26s} {size/1024:>8.0f} KB  ({path.name})")

        (VENDOR_DIR / "importmap.json").write_text(
            json.dumps({"imports": import_map}, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"\n✅ 共 {len(graph)} 个包，{total/1024/1024:.1f} MB")
        print(f"   importmap 已写入 {VENDOR_DIR / 'importmap.json'}")
    return 0


def check() -> int:
    """校验 vendor 是否就绪且 import map 完整。"""
    imap_file = VENDOR_DIR / "importmap.json"
    if not imap_file.exists():
        print("❌ static/vendor/importmap.json 不存在 —— 请先跑 python scripts/setup_ag_studio.py")
        return 1
    imap = json.loads(imap_file.read_text(encoding="utf-8"))["imports"]
    missing = []
    for pkg, url in imap.items():
        f = BASE_DIR / url.lstrip("/")   # url 形如 /static/vendor/x.mjs
        if not f.exists():
            missing.append(f"{pkg} → {url}")
    for pkg, url in imap.items():
        text = (BASE_DIR / url.lstrip("/")).read_text(encoding="utf-8", errors="ignore")
        for spec in _bare_specs(text):
            if spec not in imap:
                missing.append(f"{pkg} 引用了未映射的 {spec}")
    if missing:
        print("❌ import map 不完整：")
        for x in sorted(set(missing)):
            print("   -", x)
        return 1
    print(f"✅ vendor 就绪：{len(imap)} 个包，import map 完整")
    for pkg, url in sorted(imap.items()):
        f = BASE_DIR / url.lstrip("/")
        print(f"   {pkg:26s} {f.stat().st_size/1024:>8.0f} KB")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="安装 AG Studio 前端运行时")
    ap.add_argument("--check", action="store_true", help="只校验，不安装")
    args = ap.parse_args()
    return check() if args.check else install()


if __name__ == "__main__":
    raise SystemExit(main())
