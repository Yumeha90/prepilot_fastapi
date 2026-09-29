"""Redis 客户端（验证码、限流、缓存）。

本地：docker-compose 的 56379；云端：compose 内 redis 服务。
连接失败时不影响应用启动，调用方需自行 try/except。
"""
from __future__ import annotations

import logging

import redis.asyncio as redis

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

redis_client = redis.from_url(
    settings.REDIS_URL, encoding="utf-8", decode_responses=True
)


async def ping_redis() -> bool:
    try:
        return bool(await redis_client.ping())
    except Exception as exc:  # noqa: BLE001
        logger.warning("Redis 不可用：%s", exc)
        return False
