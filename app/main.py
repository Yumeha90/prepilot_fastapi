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
from app.observability.logging import configure as configure_logging
from app.observability.middleware import ObservabilityMiddleware
from app.observability.metrics import set_app_info
from app.observability.tracing import flush as flush_traces
from app.routers import (
    admin,
    auth,
    board,
    candidates,
    evaluations,
    health,
    jd,
    lifecycle,
    match,
    me,
    positions,
    resume,
    sessions,
    users,
    workbench,
)

settings = get_settings()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # JSON 行日志：容器 stdout → Alloy → Loki。必须在任何业务日志之前装好，
    # 否则启动阶段的日志还是纯文本，进 Loki 后没法按 level / trace_id 过滤。
    configure_logging(logging.DEBUG if settings.DEBUG else logging.INFO)
    logger.info("启动 %s（env=%s）", settings.APP_NAME, settings.ENV)
    set_app_info(settings.ENV, settings.APP_VERSION)
    # infra 未启动时建表会失败，这里只告警，保证应用仍能起来
    await init_db()
    yield
    await close_db()
    # 进程退出前冲刷未发送的 trace（否则最后几秒的 AI 调用在 Langfuse 里查不到）
    flush_traces()
    logger.info("已关闭数据库连接")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version="0.1.0",
        description="面试官备面 AI 助手 · 后端服务",
        debug=settings.DEBUG,
        lifespan=lifespan,
    )

    # 埋点中间件必须是**最外层**（后 add 的更靠外），这样它包住 CORS 与路由，
    # 才能拿到最终状态码，并让后续所有 span 挂在 HTTP 根 span 下
    app.add_middleware(ObservabilityMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # 旧的纯文本访问日志中间件已由 ObservabilityMiddleware 取代（它输出 JSON 行，
    # 带 trace_id，可被 Loki 按字段过滤），不再叠加，避免每条请求记两遍。
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
    # 3.3 看板 P08（列可见性按 BR-15 由后端下发）
    app.include_router(board.router, prefix=settings.API_PREFIX)
    # 3.3 人岗匹配 P18（BR-18：面试官没有 match:view，路由级 403）
    app.include_router(match.router, prefix=settings.API_PREFIX)
    # 3.5 数据生命周期 P15（BR-10 90 天粉碎：策略 + 手动粉碎 + 定时扫描）
    app.include_router(lifecycle.router, prefix=settings.API_PREFIX)
    # 3.4 AI 备面工作台（Step1 矩阵 P09；后续 Step 随各自阶段加）
    app.include_router(workbench.router, prefix=settings.API_PREFIX)
    # P17 面评列表与详情（BR-17：面试官只能看本人撰写的面评）
    app.include_router(evaluations.router, prefix=settings.API_PREFIX)
    return app


app = create_app()


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=settings.APP_PORT,
        reload=settings.DEBUG,
    )
