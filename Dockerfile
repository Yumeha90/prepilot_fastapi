# ============================================================
# PrepPilot 后端镜像
# - 基础镜像与本地 venv 严格一致：Python 3.12
# - 目标平台由构建命令 `docker build --platform linux/amd64` 决定：
#   云端为 amd64，本机 Mac 为 arm64，必须交叉构建。此处不再写死
#   FROM --platform=常量（会触发 FromPlatformFlagConstDisallowed 警告）。
# - pip 默认走国内镜像：官方 pypi.org 实测约 80KB/s，大包（pandas/grpcio）
#   极易断流导致构建失败；清华镜像实测 20MB/s 以上。可用
#   --build-arg PIP_INDEX_URL=... 覆盖。
# - 依赖层使用 BuildKit cache mount 持久化 pip 缓存：Dockerfile 变更导致层
#   缓存失效时，无需重新下载全部 wheel（缓存不进镜像层，不增大体积）。
# - 非 root 运行；密钥不 COPY（.dockerignore 已排除 .env），由运行时 env 注入
# ============================================================
FROM python:3.12-slim

ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_PORT=8000 \
    PYTHONPATH=/app

WORKDIR /app

# 依赖先行（利用构建缓存 + pip 缓存持久化）
# 注意：此处刻意不设 PIP_NO_CACHE_DIR，否则 pip 不写 /root/.cache/pip，
# cache mount 就失去意义；该目录由 cache mount 挂载，不会进入镜像层。
COPY requirements.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install \
        -i ${PIP_INDEX_URL} \
        --retries 10 \
        --timeout 120 \
        -r requirements.txt

# 应用代码
COPY ./app ./app
COPY ./migrations ./migrations
COPY ./migrate.py ./migrate.py
# seed.py：云端部署后写入角色/权限与演示账号（幂等，由 playbook 调用）
COPY ./scripts ./scripts

# 非 root 用户
RUN useradd --create-home --shell /bin/bash appuser \
    && mkdir -p /var/lib/prepilot/metrics \
    && chown -R appuser:appuser /app /var/lib/prepilot
USER appuser

EXPOSE 8000

# 健康检查：容器内 8000，云端 nginx 反代 /api/ 至此
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health').status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
