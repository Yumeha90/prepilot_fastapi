"""PrepPilot 后端入口。

本地开发：
    uvicorn app.main:app --reload --port 8010
或：
    python -m app.main   （端口取 .env 的 APP_PORT）
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.database import close_db, init_db
from app.middleware.logging import RequestLogMiddleware
from app.routers import (
    admin,
    auth,
    candidates,
    health,
    jd,
    me,
    positions,
    resume,
    sessions,
    users,
)

settings = get_settings()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=logging.DEBUG if settings.DEBUG else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    logger.info("启动 %s（env=%s）", settings.APP_NAME, settings.ENV)
    # infra 未启动时建表会失败，这里只告警，保证应用仍能起来
    await init_db()
    yield
    await close_db()
    logger.info("已关闭数据库连接")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version="0.1.0",
        description="面试官备面 AI 助手 · 后端服务",
        debug=settings.DEBUG,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestLogMiddleware)

    # 统一挂到 /api 前缀：/api/health、/api/auth/*、/api/me/*、/api/admin/*、/api/positions/*
    app.include_router(health.router, prefix=settings.API_PREFIX)
    app.include_router(auth.router, prefix=settings.API_PREFIX)
    app.include_router(me.router, prefix=settings.API_PREFIX)
    app.include_router(admin.router, prefix=settings.API_PREFIX)
    app.include_router(positions.router, prefix=settings.API_PREFIX)
    app.include_router(users.router, prefix=settings.API_PREFIX)
    # JD 抽取与 AI 拆解：独立前缀，新建职位（尚无 id）时也要能用
    app.include_router(jd.router, prefix=settings.API_PREFIX)
    # 3.3 候选人：简历抽取 / 解析（/api/resume）与候选人 CRUD（/api/candidates）
    app.include_router(resume.router, prefix=settings.API_PREFIX)
    app.include_router(candidates.router, prefix=settings.API_PREFIX)
    app.include_router(sessions.router, prefix=settings.API_PREFIX)
    return app


app = create_app()


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=settings.APP_PORT,
        reload=settings.DEBUG,
    )
