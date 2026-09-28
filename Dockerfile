# ============================================================
# PrepPilot 后端镜像
# - 基础镜像与本地 venv 严格一致：Python 3.12
# - 平台：由 `docker build --platform linux/amd64` 注入 TARGETPLATFORM。
#   云端为 amd64，本机 Mac 为 arm64，必须交叉构建；缺省回退 linux/amd64。
#   构建命令：docker build --platform linux/amd64 -t prepilot-backend:test-N .
# - 非 root 运行；密钥不 COPY（.dockerignore 已排除 .env），由运行时 env 注入
# ============================================================
ARG TARGETPLATFORM=linux/amd64
FROM --platform=${TARGETPLATFORM} python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_PORT=8000 \
    PYTHONPATH=/app

WORKDIR /app

# 依赖先行（利用构建缓存）
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 应用代码
COPY ./app ./app
COPY ./migrations ./migrations
COPY ./migrate.py ./migrate.py

# 非 root 用户
RUN useradd --create-home --shell /bin/bash appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# 健康检查：容器内 8000，云端 nginx 反代 /api/ 至此
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health').status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
