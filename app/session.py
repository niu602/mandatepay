"""会话 cookie 的签发与校验（W-08 鉴权）。

为什么用 cookie session 而不是 header 凭据
------------------------------------------
五个前端页面的 fetch **全部无凭据**（实测 11 处，见 W-08 工单），
而页面路由本身不提供数据。若给 `/api/*` 加 header 凭据，
页面数据区会全空白、"我已付款·捕获"按钮会 401 —— 直接砸掉演示。

cookie 方案下：页面路由访问时下发一个签名 cookie，页面内fetch 同源自动带上，
**前端代码一行不用改**。

安全目标（明确定位，不做过头）
----------------------------
工单要求："不要做登录表单 —— 评审期没人愿意注册账号。页面打开即自动签发即可，
安全目标是'挡住裸脚本批量拉数据'，不是'做账号体系'。"

所以本模块做的是：
  - cookie **带HMAC-SHA256 签名** → 篡改即失效（不是明文 base64）
  - 短有效期（默认 8 小时）→ 泄露的 cookie 不会长期可用
  - **绑定 User-Agent 摘要** → 复制 cookie 到别的客户端也用不了
  - `HttpOnly` → JS 读不到（XSS 也偷不走）；`SameSite=Lax` → 防 CSRF
  - 密钥**从环境变量派生**，不新增必填环境变量（见下）

不做什么：不做账号体系、不做登录表单、不做速率限制、不防定向扫描。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time

# 会话 cookie 名。用 __Host- 前缀有硬性要求（必须 Secure + Path=/ +
# 无 Domain），本地 http 环境下浏览器会拒收——所以线上用 __Host- 前缀，
# 本地开发用普通名字。切换逻辑见 _cookie_name()。
_COOKIE_NAME_HTTPS = "__Host-mandatepay_session"
_COOKIE_NAME_HTTP = "mandatepay_session"

# 会话有效期（秒）。默认 8 小时：
# 够评委逛一整天，不用反复刷新；泄露风险窗口可控。
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", "28800"))

# 签名密钥的用途隔离标签 —— 避免与其他用途的 HMAC 串味
_KDF_INFO = b"mandatepay/session-key/v1"


def _derive_secret() -> bytes:
    """派生签名密钥。

    **不新增必填环境变量**：优先用 `SESSION_SECRET`；没配就从已有的
    `PAYPAL_CLIENT_SECRET` 派生（它已经是部署必填项，且是稳定的随机值）。
    两者都没有时退回到一个**每次启动都变**的进程级随机密钥 ——
    这种情况下重启会让旧 cookie 失效（安全但体验略差），
    且单实例部署下完全够用。

    为什么可以复用 PAYPAL_CLIENT_SECRET：它只在服务端使用、不出公网、
    长度足够、且不会轮换（轮换 PayPal 凭据不常见）。派生时加了
    `HKDF-like` 的 info 标签，密钥与 PayPal 用途在密码学上隔离。
    """
    explicit = os.getenv("SESSION_SECRET", "").strip()
    if explicit:
        return explicit.encode("utf-8")

    paypal_secret = os.getenv("PAYPAL_CLIENT_SECRET", "").strip()
    if paypal_secret:
        base = paypal_secret.encode("utf-8")
    else:
        # 无任何配置：进程级随机密钥（重启即失效）
        base = os.urandom(32)
        # 标记这是临时密钥 —— 校验时不能跨重启接受
        return hashlib.sha256(_KDF_INFO + b"/ephemeral" + base).digest()

    # HKDF-ish 派生：sha256(info || base)
    return hashlib.sha256(_KDF_INFO + b"/" + base).digest()


# 进程启动时派生一次（不是每次请求都算）
_SECRET = _derive_secret()
# 标记是否用了临时密钥（无任何配置时）
_EPHEMERAL = not (
    os.getenv("SESSION_SECRET", "").strip() or os.getenv("PAYPAL_CLIENT_SECRET", "").strip()
)


def _b64e(raw: bytes) -> str:
    """URL-safe base64，无 padding。"""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    """URL-safe base64 解码（补回 padding）。"""
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _sign(payload: str) -> str:
    """对 payload 做 HMAC-SHA256 签名。"""
    return _b64e(hmac.new(_SECRET, payload.encode("ascii"), hashlib.sha256).digest())


def _ua_digest(ua: str | None) -> str:
    """User-Agent 摘要。

    绑定 UA 是为了让"复制 cookie 到别的客户端"失效。
    **不是**主要防线（UA 可伪造），是纵深防御的一层。
    """
    return _b64e(hashlib.sha256((ua or "").encode("utf-8")).digest()[:8])


def issue_cookie_parts(user_agent: str | None = None, secure: bool = False) -> tuple[str, str]:
    """签发会话 cookie，返回 (name, value)。

    payload 格式：`<issued_at>.<ua_digest>`
    签名：`hmac_sha256(secret, payload)`
    cookie 值：`<payload>.<signature>`
    """
    issued_at = int(time.time())
    payload = f"{issued_at}.{_ua_digest(user_agent)}"
    sig = _sign(payload)
    value = f"{payload}.{sig}"
    name = _COOKIE_NAME_HTTPS if secure else _COOKIE_NAME_HTTP
    return name, value


def issue_cookie(user_agent: str | None = None, secure: bool = False) -> str:
    """签发一个会话 cookie，返回完整的 Set-Cookie 头值。

    形如：`name=value; Path=/; Max-Age=28800; HttpOnly; SameSite=Lax[; Secure]`
    """
    name, value = issue_cookie_parts(user_agent=user_agent, secure=secure)
    parts = [
        f"{name}={value}",
        "Path=/",
        f"Max-Age={SESSION_TTL_SECONDS}",
        # HttpOnly：JS 读不到 —— 前端 fetch 靠浏览器自动带，不依赖 JS 读
        "HttpOnly",
        # Lax：正常导航（同源页面内 fetch）会带，跨站顶层导航不带 → 防 CSRF
        "SameSite=Lax",
    ]
    if secure:
        # __Host- 前缀要求 Secure
        parts.append("Secure")
    return "; ".join(parts)


def verify_cookie(cookie_header: str | None, user_agent: str | None = None) -> bool:
    """校验 cookie 是否有效。

    四道检查：
      1. 格式正确（三个段）
      2. HMAC 签名匹配（防篡改）
      3. 未过期（防长期重放）
      4. UA 摘要匹配（纵深防御）
    """
    if not cookie_header:
        return False

    # 从 Cookie 头里取出我们的那一个（可能有多个 cookie）
    token = None
    for part in cookie_header.split(";"):
        part = part.strip()
        if part.startswith(f"{_COOKIE_NAME_HTTPS}="):
            token = part.split("=", 1)[1]
            break
        if part.startswith(f"{_COOKIE_NAME_HTTP}="):
            token = part.split("=", 1)[1]
            break
    if not token:
        return False

    segments = token.split(".")
    if len(segments) != 3:
        return False
    issued_at_s, ua_digest, sig = segments

    # 1) 签名（用 compare_digest 防时序攻击）
    payload = f"{issued_at_s}.{ua_digest}"
    expected = _sign(payload)
    if not hmac.compare_digest(sig, expected):
        return False

    # 2) 过期
    try:
        issued_at = int(issued_at_s)
    except ValueError:
        return False
    age = time.time() - issued_at
    # 负数 = 签发时间在未来（时钟偏移或伪造）→ 拒绝，但要给一点容差
    if age < -300:
        return False
    if age > SESSION_TTL_SECONDS:
        return False

    # 3) UA 绑定
    if not hmac.compare_digest(ua_digest, _ua_digest(user_agent)):
        return False

    return True


def cookie_name() -> str:
    """当前环境下的 cookie 名（供测试与文档引用）。"""
    return _COOKIE_NAME_HTTPS if _is_https() else _COOKIE_NAME_HTTP


def _is_https() -> bool:
    """判断是否应使用 Secure / __Host- 前缀。

    依据 `SESSION_COOKIE_SECURE` 环境变量；未设时按"有 HTTPS 就用 Secure"处理，
    由调用方（路由）传入实际请求 scheme 更准 —— 见 issue_cookie 的 secure 参数。
    """
    return os.getenv("SESSION_COOKIE_SECURE", "").lower() in ("1", "true", "yes")


def is_ephemeral_secret() -> bool:
    """是否使用了临时密钥（无 SESSION_SECRET 且无 PAYPAL_CLIENT_SECRET）。

    临时密钥下重启会让旧 cookie 全部失效 —— 部署时应该能看到这个提示。
    """
    return _EPHEMERAL
