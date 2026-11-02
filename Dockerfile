# MandatePay 部署镜像
#
# 用途：Zeabur / Hugging Face Spaces / Render(Docker 模式) 等容器平台。
# Render 的 Python 原生运行时不需要此文件（用 render.yaml 的 buildCommand 即可），
# 但保留它是为了让"一个 Dockerfile 走多个平台"，也方便本地复现线上环境。
#
# 关键点：
# 1. 端口用 $PORT，兼容各平台的端口注入（Render / Zeabur / HF Spaces 都注入 PORT）
# 2. AG Studio 前端运行时（static/vendor/）体积大且是 npm 商业包（不入 git），
#    这里在构建阶段装 —— 但它需要 npm，基础镜像没有。折中方案：
#    不装则由 budget.html 自动回退 jsdelivr CDN（功能完整，仅首屏依赖外网）。
#    如需本地 ESM，见 README「本地 vendor」说明。
# 3. 用 slim 而非完整镜像：体积小、拉取快（影响冷启动）

FROM python:3.11-slim

WORKDIR /app

# 先装依赖（利用 Docker 层缓存：代码改动不会触发依赖重装）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 再拷代码
COPY . .

# 端口：各平台注入 PORT，兜底 8000（本地 docker run 友好）
ENV PORT=8000
EXPOSE 8000

# 启动：绑定 0.0.0.0 让平台的反向代理能访问
# 用 exec 形式保证 uvicorn 成为 PID 1，正确接收平台的 SIGTERM（否则强杀会丢数据）
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
