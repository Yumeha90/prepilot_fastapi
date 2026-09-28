"""任务定义。

示例任务 add 由原 main.py 迁移而来，用于验证 Celery + RabbitMQ 链路是否打通：
    celery -A app.tasks.celery_app worker -l info
"""
from __future__ import annotations

from app.tasks.celery_app import celery_app


@celery_app.task(name="prepilot.add")
def add(x: int, y: int) -> int:
    return x + y
