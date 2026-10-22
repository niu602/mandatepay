"""应用配置：从 .env 读取 PayPal 凭证与环境。"""
import os

from dotenv import load_dotenv

load_dotenv()

PAYPAL_MODE = os.getenv("PAYPAL_MODE", "sandbox").lower()
PAYPAL_CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID", "")
PAYPAL_CLIENT_SECRET = os.getenv("PAYPAL_CLIENT_SECRET", "")
PAYPAL_WEBHOOK_ID = os.getenv("PAYPAL_WEBHOOK_ID", "")

# 仅本地联调用：跳过签名校验，直接处理事件。
# 安全护栏：PAYPAL_MODE=live（生产）时该开关被强制忽略，防止误上线。
PAYPAL_WEBHOOK_SKIP_VERIFY = (
    os.getenv("PAYPAL_WEBHOOK_SKIP_VERIFY", "false").lower() in ("1", "true", "yes")
    and PAYPAL_MODE != "live"
)

# 沙箱与生产环境的 REST API 基地址
PAYPAL_API_BASE = (
    "https://api-m.sandbox.paypal.com"
    if PAYPAL_MODE == "sandbox"
    else "https://api-m.paypal.com"
)
