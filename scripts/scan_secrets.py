# -*- coding: utf-8 -*-
"""W-04 宽口径密钥扫描：工作树 + 全历史 blob（含 base64 解码后二次扫描）。

比 W-03 的口径更宽，两类模式：

1. 厂商前缀（各云厂商/服务商的 key 形态）
   (sk-|ghp_|xoxb-|AKIA|AIza|eyJ)[A-Za-z0-9_\\-]{12,}

2. 通用赋值（任意变量名被赋了一个长随机串）
   (api[_-]?key|secret|token|password)['"]?\\s*[:=]\\s*['"][A-Za-z0-9_\\-.]{16,}

3. 数据库连接串里的密码（W-06 新增）
   (postgres|postgresql|mysql|mongodb(\\+srv)?|redis)://[^:\\s]+:([^@\\s/]{8,})@
   —— 这一条是实战补的：部署到 Neon 后连接串会出现在文档里，
   而前两条都扫不到它（它不是"变量=值"形态，也不是厂商 key 前缀）。
   曾有一次 `neondb_owner:***@...` 出现在文档中（用户名无害、密码已打码），
   说明这类形态确实容易在写文档时漏掉，必须单独设口径。

扫描范围：
  A. 工作树（未跟踪 + 已跟踪），排除 .git/.venv/node_modules 等噪音目录
  B. 全历史所有 blob（git rev-list --objects --all 逐个取出内容）
  C. 每个 blob 先按 base64 解码一次，解码结果再跑同样的两条正则
     （防止有人把 key base64 后塞进文件）

用法：
  python scripts/scan_secrets.py                 # 全量扫描
  python scripts/scan_secrets.py --quick         # 只扫工作树 + 历史（跳过 base64，慢）
  python scripts/scan_secrets.py --json out.json # 输出 JSON 报告

退出码：0 = 未发现；1 = 发现疑似泄漏
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE_DIR = Path(__file__).resolve().parents[1]

# ── 三条扫描口径 ────────────────────────────────────────
VENDOR_RE = re.compile(
    r"(sk-|ghp_|xoxb-|AKIA|AIza|eyJ)[A-Za-z0-9_\-]{12,}"
)
ASSIGN_RE = re.compile(
    r"(api[_-]?key|secret|token|password)['\"]?\s*[:=]\s*['\"][A-Za-z0-9_\-\.]{16,}",
    re.IGNORECASE,
)
# 数据库连接串中的密码（只取密码部分作为命中片段）
# 只扫**非本地**主机 —— `postgres:postgres@localhost` 是本地开发的默认口令，
# 写进文档/脚本无数千处，扫出来全是噪音；真正要防的是云托管数据库的密码。
DB_URL_RE = re.compile(
    r"(?:postgres|postgresql|mysql|mongodb(?:\+srv)?|redis|amqp)://"
    r"[^:\s/]+:([^@\s/]{8,})@(?!localhost|127\.0\.0\.1|0\.0\.0\.0)([^\s/'\"?]+)",
    re.IGNORECASE,
)

# ── 扫描时跳过的目录/文件 ───────────────────────────────
SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
    ".ruff_cache", ".workbuddy", ".idea", ".vscode", "static/vendor",
}
SKIP_FILE_SUFFIX = {".pyc", ".pyo", ".so", ".dll", ".exe", ".zip", ".mjs", ".map"}
# 明确豁免：这些文件按设计就包含"看起来像密钥"的占位或示例
ALLOW_FILES = {".env.example", ".env.sample", "LICENSE"}
# 扫描器自身：里面**故意**写了一批假密钥样本做自检（should_hit 列表），
# 它们不是真凭据。整文件豁免，避免自指误报。
SCANNER_SELF = "scan_secrets.py"
# 这些文件里的命中属于「文档/脚本里的示例文本」，人工确认过，不算泄漏
# ⚠️ 豁免必须**精确**（整段等于），不能用"包含"判断 ——
#    用包含判断时，"password" 会把 `password: "真实密码"` 这类真命中一起放过
#    （W-06 自检实测到的：加豁免后工作树里的真密码反而没被抓到）。
#    所以豁免项用精确匹配，且只列确定无疑的形态。
ALLOW_SUBSTR_EXACT = {
    "***REMOVED***", "your-api-key", "your_api_key", "YOUR_API_KEY",
    "sk-xxxx", "sk-xxxxx", "sk-...", "<your-key>", "sk-todo",
    "sk-0000000000000000000000000000000000000000000000000",
    # 打码后的密码段（文档里写连接串时习惯把密码替换成 ***）
    "***",
    # 文档里常见的占位符密码段（示例文本，非真实凭据）
    "password", "passwd", "PASSWORD", "your_password", "your-password",
}

# ── base64 候选：长度 >= 24 的 base64 串 ────────────────
B64_CANDIDATE_RE = re.compile(r"[A-Za-z0-9+/]{24,}={0,2}")
B64_MIN_LEN = 24


def _is_probably_base64(s: str) -> bool:
    """快速排除明显不是 base64 的串（长度 %4 合法 + 字符集）。"""
    if len(s) < B64_MIN_LEN or len(s) % 4 != 0:
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", s))


def _redact(text: str) -> str:
    """命中片段脱敏：保留前 6 位与后 4 位，中间打码。"""
    if len(text) <= 12:
        return text[:4] + "***"
    return f"{text[:6]}***{text[-4:]}"


def _is_allowed(raw: str) -> bool:
    """判断命中片段是否属于豁免。

    **精确匹配整段**，不做"包含"判断 ——
    包含判断会让 `"password": "真实密码"` 里的 password 把整段放行（真泄漏被放过）。
    对"变量=值"型命中（口径 1/2），额外检查**值部分**是否精确等于某个豁免项：
    如 `api_key = 'sk-xxxx'` → 整段不是豁免，但值 `sk-xxxx` 是 → 豁免。
    """
    if raw in ALLOW_SUBSTR_EXACT:
        return True
    # 口径 2：拆出引号里的值再判断
    m = re.search(r"['\"]([^'\"]{8,})['\"]", raw)
    if m and m.group(1) in ALLOW_SUBSTR_EXACT:
        return True
    return False


def _scan_text(text: str, source: str, layer: str) -> list[dict]:
    """在一段文本里跑三条正则，返回命中列表。"""
    hits: list[dict] = []
    for kind, rx in (("vendor_prefix", VENDOR_RE), ("generic_assign", ASSIGN_RE)):
        for m in rx.finditer(text):
            raw = m.group(0)
            if _is_allowed(raw):
                continue
            # 取命中片段前后的上下文，便于人工判断
            lo = max(0, m.start() - 40)
            hi = min(len(text), m.end() + 40)
            ctx = text[lo:hi].replace("\n", "\\n")
            hits.append({
                "layer": layer,
                "source": source,
                "kind": kind,
                "match_redacted": _redact(raw),
                "line": text.count("\n", 0, m.start()) + 1,
                "context": ctx,
                "length": len(raw),
            })
    # 第三条：数据库连接串里的密码（只取密码部分作为命中片段）
    for m in DB_URL_RE.finditer(text):
        pwd = m.group(1)
        if _is_allowed(pwd):
            continue
        lo = max(0, m.start() - 40)
        hi = min(len(text), m.end() + 40)
        ctx = text[lo:hi].replace("\n", "\\n")
        hits.append({
            "layer": layer,
            "source": source,
            "kind": "db_url_password",
            "match_redacted": _redact(pwd),
            "line": text.count("\n", 0, m.start()) + 1,
            "context": ctx,
            "length": len(pwd),
        })
    return hits


def _scan_bytes(data: bytes, source: str, layer: str) -> list[dict]:
    """扫一段原始内容（先按文本解，再做 base64 解码二次扫）。"""
    hits: list[dict] = []
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1", errors="ignore")
    hits.extend(_scan_text(text, source, layer))

    # ── base64 解码后二次扫描 ──
    for m in B64_CANDIDATE_RE.finditer(text):
        cand = m.group(0)
        if not _is_probably_base64(cand):
            continue
        try:
            decoded = base64.b64decode(cand, validate=True)
        except Exception:
            continue
        if len(decoded) < 12:
            continue
        try:
            dec_text = decoded.decode("utf-8")
        except UnicodeDecodeError:
            continue
        # 解码后若不含可打印字符太多，跳过
        printable = sum(1 for c in dec_text if 32 <= ord(c) < 127 or c in "\n\r\t")
        if printable / len(dec_text) < 0.85:
            continue
        for hit in _scan_text(dec_text, f"{source} (base64-decoded)", f"{layer}+base64"):
            hit["b64_source"] = _redact(cand)
            hits.append(hit)
    return hits


# ── A. 工作树扫描 ───────────────────────────────────────
def scan_worktree() -> list[dict]:
    hits: list[dict] = []
    for path in BASE_DIR.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(BASE_DIR)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if path.suffix.lower() in SKIP_FILE_SUFFIX:
            continue
        if path.name in ALLOW_FILES:
            continue
        # 扫描器自身（含故意的假密钥自检样本），避免自指误报
        if path.name == SCANNER_SELF:
            continue
        # 明确忽略 .env 本体（本来就 gitignore，从不入库）
        if path.name == ".env" or path.name.endswith(".bak_w02"):
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        hits.extend(_scan_bytes(data, str(rel).replace("\\", "/"), "worktree"))
    return hits


# ── B+C. 全历史 blob 扫描 ───────────────────────────────
def scan_history() -> list[dict]:
    hits: list[dict] = []

    # 列出所有历史对象（去重后的 blob）
    out = subprocess.run(
        ["git", "rev-list", "--objects", "--all"],
        cwd=BASE_DIR, capture_output=True, text=True, errors="ignore",
    ).stdout

    blob_ids: list[str] = []
    seen: set[str] = set()
    for line in out.splitlines():
        parts = line.split(" ", 1)
        if len(parts) != 2:
            continue
        sha, name = parts
        if sha in seen:
            continue
        seen.add(sha)
        blob_ids.append((sha, name))  # type: ignore[arg-type]

    # 用 cat-file --batch 批量取内容，比逐个 git show 快得多
    if not blob_ids:
        return hits

    proc = subprocess.Popen(
        ["git", "cat-file", "--batch"],
        cwd=BASE_DIR, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
    )
    assert proc.stdin and proc.stdout
    payload = "\n".join(sha for sha, _ in blob_ids) + "\n"
    stdout, _ = proc.communicate(payload.encode("ascii", "ignore"))

    # 解析 batch 输出：<sha> <type> <size>\n<content>\n
    idx = 0
    names = {sha: name for sha, name in blob_ids}
    while idx < len(stdout):
        nl = stdout.find(b"\n", idx)
        if nl == -1:
            break
        header = stdout[idx:nl].decode("ascii", "ignore").split()
        if len(header) < 3:
            idx = nl + 1
            continue
        sha, _typ, size_s = header[0], header[1], header[2]
        try:
            size = int(size_s)
        except ValueError:
            idx = nl + 1
            continue
        content = stdout[nl + 1: nl + 1 + size]
        idx = nl + 1 + size + 1
        name = names.get(sha, sha)
        if name.endswith((".pyc", ".so", ".dll", ".exe", ".zip")):
            continue
        # 扫描器自身的历史版本（含假密钥自检样本），避免自指误报
        if name.endswith(SCANNER_SELF):
            continue
        hits.extend(_scan_bytes(content, f"{sha[:8]}:{name}", "history"))

    return hits


# ── 分类与去噪 ──────────────────────────────────────────
def classify(hits: list[dict]) -> tuple[list[dict], list[dict]]:
    """把命中分成「真泄漏」与「疑似误报（需人工确认）」。"""
    real: list[dict] = []
    fuzzy: list[dict] = []
    for h in hits:
        ctx = h.get("context", "")
        # 明显是文档/示例/占位符语境的，归为误报候选
        if any(k in ctx for k in (
            "example", "示例", "your-", "YOUR_", "sk-xxx", "TODO", "占位",
            "process.env", "os.getenv", "getenv(", "环境变量", "配置",
        )):
            fuzzy.append(h)
        else:
            real.append(h)
    return real, fuzzy


def _self_check() -> bool:
    """扫描器自检：注入已知样本，确认每条口径都能检出。

    为什么需要：W-06 实战发现 —— 部署到 Neon 后连接串出现在文档里，
    而原有两条口径都扫不到它（既不是"变量=值"形态，也没有厂商 key 前缀）。
    补了口径 3，但如果不验证"它真能检出"，以后规则被改坏也不知道。

    返回 True 表示自检通过。
    """
    should_hit = [
        ("厂商前缀 sk-", "sk-abcdefghijklmnopqrstuvwxyz012345"),
        ("厂商前缀 ghp_", "ghp_abcdefghijklmnopqrstuvwxyz012345"),
        ("通用赋值 api_key", "api_key = 'abcdefghijklmnop123456'"),
        ("通用赋值 password", 'password: "Sup3rS3cretPwd12345"'),
        ("连接串密码(云库)", "postgresql://u:npg_OVy4bE0lsJPo@ep-x.neon.tech/db"),
        ("连接串密码(mysql)", "mysql://root:Sup3rS3cretPwd@db.example.com:3306/app"),
    ]
    should_miss = [
        ("打码连接串", "postgresql://u:***@ep-x.neon.tech/db"),
        ("本地默认口令", "postgresql://postgres:postgres@localhost:5432/db"),
        ("占位符密码", "postgresql://user:password@ep-x.neon.tech/db"),
        ("无密码连接串", "postgresql://neondb_owner@ep-x.neon.tech/db"),
    ]

    ok = True
    print("── 自检：注入样本验证各口径 ──")
    for label, sample in should_hit:
        hits = _scan_text(sample, "selfcheck", "selfcheck")
        good = bool(hits)
        ok = ok and good
        print(f"   {'✅' if good else '❌'} 应检出: {label}")
    for label, sample in should_miss:
        hits = _scan_text(sample, "selfcheck", "selfcheck")
        good = not hits
        ok = ok and good
        print(f"   {'✅' if good else '❌'} 应豁免: {label}")
    print(f"   自检结果: {'通过' if ok else '未通过 —— 扫描规则可能已失效'}")
    print()
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description="宽口径密钥扫描（W-04）")
    ap.add_argument("--quick", action="store_true", help="跳过 base64 解码二次扫描")
    ap.add_argument("--json", metavar="PATH", help="输出 JSON 报告")
    ap.add_argument("--no-selfcheck", action="store_true", help="跳过扫描器自检")
    args = ap.parse_args()

    print("=" * 74)
    print("W-04 宽口径密钥扫描")
    print("=" * 74)
    print("口径 1 厂商前缀 : (sk-|ghp_|xoxb-|AKIA|AIza|eyJ)[A-Za-z0-9_\\-]{12,}")
    print("口径 2 通用赋值 : (api[_-]?key|secret|token|password)['\"]?\\s*[:=]\\s*['\"][A-Za-z0-9_\\-.]{16,}")
    print("口径 3 连接串密码: (postgres|mysql|mongodb|redis...)://user:pass@<非本地主机>")
    print(f"base64 二次扫描 : {'关闭（--quick）' if args.quick else '开启'}")
    print()

    selfcheck_ok = True
    if not args.no_selfcheck:
        selfcheck_ok = _self_check()

    print("[1/2] 扫工作树 …")
    work_hits = scan_worktree()
    print(f"      → {len(work_hits)} 处命中")

    print("[2/2] 扫全历史 blob …")
    hist_hits = scan_history()
    print(f"      → {len(hist_hits)} 处命中")

    all_hits = work_hits + hist_hits
    real, fuzzy = classify(all_hits)

    print()
    print("=" * 74)
    print(f"合计命中 {len(all_hits)} 处 —— 真泄漏候选 {len(real)}，误报候选 {len(fuzzy)}")
    print("=" * 74)

    if real:
        print("\n⚠️ 真泄漏候选：")
        for h in real[:40]:
            print(f"  [{h['layer']}] {h['source']}:{h['line']}  ({h['kind']})")
            print(f"      {h['match_redacted']}")
            print(f"      ctx: {h['context'][:150]}")
        if len(real) > 40:
            print(f"  … 另有 {len(real)-40} 处")

    if fuzzy:
        print(f"\nℹ️ 误报候选（语境含示例/占位/getenv，共 {len(fuzzy)} 处）：")
        seen_src = {}
        for h in fuzzy:
            seen_src.setdefault(h["source"], 0)
            seen_src[h["source"]] += 1
        for src, n in sorted(seen_src.items(), key=lambda kv: -kv[1])[:20]:
            print(f"  {src}  ×{n}")

    if args.json:
        Path(args.json).write_text(
            json.dumps({"real": real, "fuzzy": fuzzy}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\nJSON 报告已写入 {args.json}")

    if real:
        print("\n❌ 发现疑似泄漏，请人工确认后再 push。")
        return 1
    if not selfcheck_ok:
        # 自检没过 = 扫描规则本身失效了，"0 命中"没有意义，必须让人知道
        print("\n❌ 扫描器自检未通过 —— 扫描规则可能已失效，本次「0 命中」不可信。")
        return 2
    print("\n✅ 未发现真泄漏候选。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
