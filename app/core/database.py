"""异步数据库（SQLAlchemy 2.0 + asyncpg）。

注意：本项目不使用迁移框架。
- 开发期建表用 init_db()（Base.metadata.create_all，只建不存在的表，不 ALTER 已有表）
- 表结构变更走 migrations/schema.sql + `python migrate.py`
"""
from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)

SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    """SQLAlchemy 2.0 声明式基类。"""


async def init_db() -> None:
    """建表（幂等）。失败只告警不中断——infra 未启动时应用仍要能起来。"""
    from app import models  # noqa: F401  确保模型注册进 Base.metadata

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("数据库表初始化完成")
    except Exception as exc:  # noqa: BLE001
        logger.warning("数据库未就绪，跳过建表（infra 启动后重启应用即可）：%s", exc)


async def close_db() -> None:
    await engine.dispose()
