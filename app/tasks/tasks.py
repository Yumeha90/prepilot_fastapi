"""任务定义。

示例任务 add 由原 main.py 迁移而来，用于验证 Celery + RabbitMQ 链路是否打通：
    celery -A app.tasks.celery_app worker -l info
"""
from __future__ import annotations

from app.tasks.celery_app import celery_app


@celery_app.task(name="prepilot.add")
def add(x: int, y: int) -> int:
    return x + y


@celery_app.task(name="prepilot.mail.send", bind=True, max_retries=2)
def send_mail(self, to: str, subject: str, body: str) -> None:
    """异步发信（忘记密码验证码）。失败重试 2 次，仍失败则记录日志。"""
    from app.services.email import get_email_sender

    try:
        get_email_sender().send(to, subject, body)
    except Exception as exc:  # noqa: BLE001
        raise self.retry(exc=exc, countdown=10)
