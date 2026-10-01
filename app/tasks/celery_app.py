"""Celery 应用配置：broker = RabbitMQ(amqp)，result backend = Redis。"""
from __future__ import annotations

from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "prepilot",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["app.tasks.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_track_started=True,
    # 简历解析等任务较重，避免 worker 预取过多导致长任务堆积
    worker_prefetch_multiplier=1,
    # 定时作业（PRD §7.7）：每小时扫一次到期简历。
    # 由独立的 celery beat 进程投递（compose 里的 celery-beat 服务），
    # worker 自己不会发任务 —— 少了 beat，这条 schedule 就是一纸空文。
    beat_schedule={
        "purge-expired-resumes-every-hour": {
            "task": "prepilot.lifecycle.purge_expired",
            "schedule": 3600.0,
        },
    },
)
