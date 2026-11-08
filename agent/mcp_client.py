"""PayPal MCP Server 的最小 Python 客户端（stdio + JSON-RPC 2.0）。

为什么需要自己写
----------------
PayPal 官方 MCP Server 有两种接法：

| 方式 | 端点 | 认证 | 本项目可用性 |
|---|---|---|---|
| 本地 stdio | `npx -y @paypal/mcp` | `PAYPAL_ACCESS_TOKEN`（client_credentials 换来的） | ✅ 可用 |
| 远程 SSE / HTTP | `https://mcp.sandbox.paypal.com/sse` `/http` | OAuth authorization_code + PKCE（浏览器登录） | ❌ 需人工浏览器授权 |

实测远程端点对 client_credentials 的 bearer 直接返 401/404（第三方实测记录：
"Remote MCP ... No token: 401 ... The server only accepts tokens from its own
OAuth authorization_code + PKCE flow, which needs a browser login"）。
所以走**本地 stdio**。

为什么不装官方 `mcp` Python SDK
-----------------------------
那个包会给部署引入额外依赖（影响构建时间与冷启动）。
本项目只需要一个**只读工具**，用标准库实现 stdio JSON-RPC 足够：
MCP 的 stdio 传输就是「每行一个 JSON-RPC 消息」的 newline-delimited JSON。

最小协议流程
------------
1. → ``initialize``            握手，协商协议版本
2. → ``notifications/initialized``  通知服务端已就绪
3. → ``tools/list``             列工具
4. → ``tools/call``             调用工具

注意：stdio 是**双向长连接** —— 一次性把所有请求喂进去再关 stdin 是无效的
（客户端会等不到回复）。必须保持进程存活、逐条读响应。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

sys.stdout.reconfigure(encoding="utf-8")

PROTOCOL_VERSION = "2024-11-05"
CLIENT_INFO = {"name": "mandatepay-mcp", "version": "0.1"}

# PayPal MCP Server 的工具名是**点号分隔**的（`transactions.list`），
# 不是文档里常见的下划线写法 —— 传错名字 server 会直接抛 "Invalid tool"。
#
# 实测 @paypal/mcp@1.8.1 的 ACCEPTED_TOOLS 共 28 个：
#   invoices.create/list/get/send/sendReminder/cancel/generateQRC
#   orders.create/get/capture
#   disputes.list/get/create
#   shipment.create/get
#   products.create/list/update/show
#   subscriptionPlans.create/list/show
#   subscriptions.create/show/cancel
#   transactions.list
#   payments.createRefund/getRefunds
#
# 注意：**没有 webhooks 相关工具** —— webhook 配置只能走 Dashboard 或 REST。
# （PayPal token 的 scope 里有 applications/webhooks，但 MCP 目录未暴露对应工具。）
#
# 我们**只开只读的那一个** —— 理由见 README「MCP 不能替代 REST / Agent Toolkit」。
#
# ⚠️ **两套名字，别混**（实测踩过）：
#   - CLI 的 `--tools=` 参数值用**点号短名**：`transactions.list`
#     （server 的 parseArgs 会拿它去比对 ACCEPTED_TOOLS 枚举）
#   - MCP 协议层的 `tools/call` 用**工具函数名**：`list_transactions`
#     （tools/list 返回的 name 字段就是它）
#   传错会得到 `MCP error -32602: Tool transactions.list not found`。
READ_ONLY_TOOLS = ["invoices.create", "invoices.list"]
# MCP 协议层对应的工具函数名（tools/list 里返回的 name）
TOOL_NAME_BY_SHORT = {
    "invoices.list": "list_invoices",
    "invoices.create": "create_invoice",
    "transactions.list": "list_transactions",
}


def resolve_tool_name(short_or_full: str) -> str:
    """把 CLI 短名解析成 MCP 协议层的工具函数名。

    已经是完整函数名时原样返回。
    """
    return TOOL_NAME_BY_SHORT.get(short_or_full, short_or_full)


class McpError(RuntimeError):
    """MCP 调用失败。"""


class PayPalMcpClient:
    """PayPal MCP Server 的 stdio 客户端。

    用法::

        with PayPalMcpClient(tools=["list_transaction"]) as mcp:
            print(mcp.list_tools())
            result = mcp.call_tool("transactions.list", {"start_date": "2026-11-01"})
    """

    def __init__(
        self,
        tools: list[str] | None = None,
        access_token: str | None = None,
        environment: str = "SANDBOX",
        node_cmd: str | None = None,
    ) -> None:
        self.tools = tools if tools is not None else list(READ_ONLY_TOOLS)
        self.access_token = access_token or os.environ.get("PAYPAL_ACCESS_TOKEN", "")
        self.environment = environment
        self.node_cmd = node_cmd or self._find_node()
        self._proc: subprocess.Popen | None = None
        self._next_id = 0
        self._stderr_lines: list[str] = []

    # ── 环境准备 ──────────────────────────────────────────────

    @staticmethod
    def _find_node() -> str:
        """找 node/npx。Windows 下 npm 全局脚本常带 .cmd 后缀。"""
        for name in ("npx", "npx.cmd", "npx.exe"):
            path = shutil.which(name)
            if path:
                return path
        raise McpError(
            "找不到 npx —— 本地 MCP server 需要 Node.js 18+。"
            "装 Node：https://nodejs.org/en/download"
        )

    def _cmd(self) -> list[str]:
        """构造命令行。

        ⚠️ **必须用 `--key=value` 形式，不能用 `--key value`**。
        实测 @paypal/mcp@1.8.1 的 parseArgs 是：
            const [key, value] = arg.slice(2).split('=');
        分离写法下 value 是 undefined → `value.toLowerCase()` 直接抛
        "Cannot read properties of undefined (reading 'toLowerCase')"。
        这个报错信息完全看不出是参数格式问题，很容易卡住。
        """
        cmd = [self.node_cmd, "-y", "@paypal/mcp"]
        if self.tools:
            cmd.append(f"--tools={','.join(self.tools)}")
        cmd.append(f"--paypal-environment={self.environment}")
        return cmd

    # ── 生命周期 ──────────────────────────────────────────────

    def __enter__(self) -> "PayPalMcpClient":
        if not self.access_token:
            raise McpError(
                "缺少 PAYPAL_ACCESS_TOKEN —— "
                "用 client_credentials 换："
                "POST https://api-m.sandbox.paypal.com/v1/oauth2/token "
                "(grant_type=client_credentials)"
            )
        env = {**os.environ, "PAYPAL_ACCESS_TOKEN": self.access_token}

        self._proc = subprocess.Popen(
            self._cmd(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )

        # stderr 必须持续消费，否则管道写满会把 server 卡死
        t = threading.Thread(target=self._drain_stderr, daemon=True)
        t.start()

        self.initialize()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        if self._proc is None:
            return
        try:
            if self._proc.stdin:
                self._proc.stdin.close()
        except Exception:
            pass
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()
        self._proc = None

    def _drain_stderr(self) -> None:
        """把 server 的 stderr 收进列表（不丢弃 —— 出错时要能查）。"""
        if not self._proc or not self._proc.stderr:
            return
        for line in self._proc.stderr:
            self._stderr_lines.append(line.rstrip())
            if len(self._stderr_lines) > 200:
                del self._stderr_lines[:100]

    # ── JSON-RPC ──────────────────────────────────────────────

    def _send(self, method: str, params: dict | None = None,
              notify: bool = False) -> int | None:
        if not self._proc or not self._proc.stdin:
            raise McpError("MCP 进程未启动")
        if notify:
            msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
            if params:
                msg["params"] = params
        else:
            self._next_id += 1
            msg = {
                "jsonrpc": "2.0",
                "id": self._next_id,
                "method": method,
                "params": params or {},
            }
        self._proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self._proc.stdin.flush()
        return None if notify else self._next_id

    def _recv(self, want_id: int, timeout: float = 90.0) -> dict:
        """读一条响应（跳过 server 发来的 notification / 其他 id）。"""
        import time

        if not self._proc or not self._proc.stdout:
            raise McpError("MCP 进程未启动")
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = self._proc.stdout.readline()
            if not line:
                raise McpError(
                    "MCP server 提前退出。stderr 尾部：\n"
                    + "\n".join(self._stderr_lines[-8:])
                )
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                # server 可能打非 JSON 的日志到 stdout
                continue
            if msg.get("id") == want_id:
                if "error" in msg:
                    err = msg["error"]
                    raise McpError(
                        f"MCP {err.get('code')}: {err.get('message')} "
                        f"(data={str(err.get('data'))[:200]})"
                    )
                return msg.get("result", {})
            # 其它 id 或 notification → 忽略继续读
        raise McpError(f"等待 MCP 响应超时（{timeout}s）")

    # ── MCP 方法 ──────────────────────────────────────────────

    def initialize(self) -> dict:
        rid = self._send(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": CLIENT_INFO,
            },
        )
        result = self._recv(rid or 0)
        self._send("notifications/initialized", notify=True)
        return result

    def list_tools(self) -> list[dict]:
        rid = self._send("tools/list")
        result = self._recv(rid or 0)
        return result.get("tools", [])

    def call_tool(self, name: str, args: dict | None = None,
                  timeout: float = 90.0) -> dict:
        """调用工具。`name` 可以是短名（`transactions.list`）或完整函数名。"""
        rid = self._send(
            "tools/call",
            {"name": resolve_tool_name(name), "arguments": args or {}},
        )
        return self._recv(rid or 0, timeout=timeout)


# ── 命令行入口：python agent/mcp_client.py --list / --call ------------

def _print_tools(client: PayPalMcpClient) -> None:
    tools = client.list_tools()
    print(f"MCP server 暴露 {len(tools)} 个工具：")
    for t in tools:
        desc = (t.get("description") or "").strip().replace("\n", " ")
        if len(desc) > 110:
            desc = desc[:110] + "…"
        print(f"  · {t.get('name'):28s} {desc}")


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="PayPal MCP Server 客户端（stdio）")
    ap.add_argument("--list", action="store_true", help="列出可用工具")
    ap.add_argument("--call", metavar="TOOL", help="调用工具")
    ap.add_argument("--args", metavar="JSON", default="{}", help="工具参数（JSON）")
    ap.add_argument("--tools", default=",".join(READ_ONLY_TOOLS),
                    help=f"要启用的工具（逗号分隔），默认 {','.join(READ_ONLY_TOOLS)}")
    args = ap.parse_args()

    # token 没给就用 .env 的凭证现换一个
    token = os.environ.get("PAYPAL_ACCESS_TOKEN", "")
    if not token:
        from pathlib import Path

        from dotenv import load_dotenv

        load_dotenv(Path(__file__).resolve().parent.parent / ".env")
        import requests

        cid = os.environ.get("PAYPAL_CLIENT_ID", "")
        secret = os.environ.get("PAYPAL_CLIENT_SECRET", "")
        if not cid or not secret:
            print("❌ 没有 PAYPAL_ACCESS_TOKEN，也没有 .env 里的 PayPal 凭证")
            return 1
        r = requests.post(
            "https://api-m.sandbox.paypal.com/v1/oauth2/token",
            auth=(cid, secret),
            data={"grant_type": "client_credentials"},
            timeout=30,
        )
        token = r.json().get("access_token", "")
        if not token:
            print(f"❌ 换 token 失败：{r.status_code} {r.text[:200]}")
            return 1

    try:
        with PayPalMcpClient(
            tools=[t for t in args.tools.split(",") if t], access_token=token
        ) as mcp:
            if args.list or not args.call:
                _print_tools(mcp)
            if args.call:
                try:
                    call_args = json.loads(args.args)
                except json.JSONDecodeError as exc:
                    print(f"❌ --args 不是合法 JSON：{exc}")
                    return 1
                result = mcp.call_tool(args.call, call_args)
                print("\n=== 调用结果 ===")
                print(json.dumps(result, ensure_ascii=False, indent=2)[:4000])
    except McpError as exc:
        print(f"❌ MCP 错误：{exc}")
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"❌ {type(exc).__name__}: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
