"""集中配置：所有连接串 / 密钥统一从 .env 读取（明文管理，不做 12-factor）。

约定：
- .env 只放本地开发用明文，已加入 .gitignore 与 .dockerignore，绝不进镜像与仓库。
- .env.example 只放占位键，供团队复制。
- 端口沿用「避开 infra 已占用端口」的原则（见 .env.example 注释）。
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根 = prepilot_fastapi/
BASE_DIR = Path(__file__).resolve().parents[2]
ENV_FILE = BASE_DIR / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---------- 应用 ----------
    APP_NAME: str = "PrepPilot API"
    ENV: str = "dev"
    DEBUG: bool = True
    # 本地开发默认 8010（避开 8000/8001/8080 等 infra 已占用端口）
    # 容器 / k3d / 云端由环境注入 APP_PORT=8000，与 k8s app.yaml、云端 compose 对齐
    APP_PORT: int = 8010
    API_PREFIX: str = "/api"

    # ---------- 数据库（云端自建 Compose PostgreSQL，asyncpg 直连）----------
    # 本地联调指向 docker-compose 的 postgres（宿主端口 55432，避开 langfuse 的 5432）
    DATABASE_URL: str = "postgresql+asyncpg://prepilot:prepilot@localhost:55432/prepilot"

    # ---------- Redis（缓存 + Celery result backend）----------
    REDIS_URL: str = "redis://localhost:56379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:56379/1"

    # ---------- RabbitMQ（Celery broker）----------
    CELERY_BROKER_URL: str = "amqp://guest:guest@localhost:55672//"

    # ---------- JWT（短期 access + 可吊销 refresh）----------
    JWT_SECRET: str = "change-me-please"
    JWT_ALGORITHM: str = "HS256"
    # 兼容旧字段名：access token 有效期（分钟）
    JWT_EXPIRE_MINUTES: int = 30
    # refresh token 有效期（天），落库可吊销
    JWT_REFRESH_EXPIRE_DAYS: int = 7

    # ---------- 邮件（忘记密码验证码）----------
    # 未配置 SMTP_HOST 时降级为「只写日志」，不真实发信
    SMTP_HOST: str = ""
    SMTP_PORT: int = 465  # 云服务器 25 端口通常被封，一律走 465 SSL
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_SENDER: str = ""
    # 开发环境回显验证码（便于无 SMTP 时走通演示流程），生产必须为 false
    DEV_ECHO_CODE: bool = False

    # ---------- 种子数据（云端部署时是否写入演示数据）----------
    SEED_DEMO_DATA: bool = True

    # ---------- CORS（本地前端 dev server 端口 5180）----------

    # ---------- Milvus（本地 k3d + Helm 部署）----------
    MILVUS_HOST: str = "localhost"
    MILVUS_PORT: int = 19530
    MILVUS_USER: str = ""
    MILVUS_PASSWORD: str = ""
    MILVUS_DB: str = "default"

    # ---------- Langfuse（v2 服务端，本地 docker compose，宿主 3030）----------
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = ""
    LANGFUSE_HOST: str = "http://localhost:3030"

    # ---------- 腾讯云 COS（简历原件暂存）----------
    COS_REGION: str = ""
    COS_BUCKET: str = ""
    COS_SECRET_ID: str = ""
    COS_SECRET_KEY: str = ""
    COS_DOMAIN: str = ""

    # ---------- 云服务器（Ansible 部署目标）----------
    CLOUD_PUBLIC_IP: str = ""
    CLOUD_INTERNAL_IP: str = ""

    # ---------- LLM（阿里云百炼 DashScope 兼容 OpenAI 协议）----------
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = ""
    LLM_MODEL: str = "qwen3.8-flash"
    LLM_TEMPERATURE: float = 0.3
    LLM_TIMEOUT: int = 120

    # ---------- Embedding（向量模型，供 Milvus 检索用）----------
    # 未单独配置时复用 LLM 的 key / base_url
    EMBEDDING_MODEL: str = "qwen3.7-text-embedding-flash"
    EMBEDDING_API_KEY: str = ""
    EMBEDDING_BASE_URL: str = ""
    EMBEDDING_DIM: int = 1024

    # ---------- CORS（本地前端 dev server 端口 5180）----------
    CORS_ORIGINS: str = "http://localhost:5180,http://127.0.0.1:5180"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    """缓存单例，避免每次请求重复解析 .env。"""
    return Settings()
