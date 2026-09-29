"""邮件发送（忘记密码验证码）。

两条实现：
- SMTPEmailSender：配置了 SMTP_HOST 时真实发信（云服务器 25 端口常封，默认 465 SSL）
- LogEmailSender：未配置 SMTP 时只写日志，保证无邮件环境下流程仍可走通（开发/演示）

未来接企业邮箱 / 短信只需再加一个实现，无需改调用方。
"""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from functools import lru_cache

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class EmailSender:
    """发送器接口。"""

    name = "base"

    def send(self, to: str, subject: str, body: str) -> None:
        raise NotImplementedError


class LogEmailSender(EmailSender):
    """未配置 SMTP 时的降级实现：只写日志，不真实发信。"""

    name = "log"

    def send(self, to: str, subject: str, body: str) -> None:
        logger.info("[email:log] to=%s subject=%s body=%s", to, subject, body)


class SMTPEmailSender(EmailSender):
    """SMTP SSL 发信（同步实现，由 Celery 任务调用，不阻塞事件循环）。"""

    name = "smtp"

    def send(self, to: str, subject: str, body: str) -> None:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = settings.SMTP_SENDER or settings.SMTP_USERNAME
        msg["To"] = to
        msg.set_content(body)

        with smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as server:
            if settings.SMTP_USERNAME:
                server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
            server.send_message(msg)
        logger.info("[email:smtp] 已发送 to=%s subject=%s", to, subject)


@lru_cache
def get_email_sender() -> EmailSender:
    """按配置选择实现：配了 SMTP_HOST 就真发，否则降级写日志。"""
    return SMTPEmailSender() if settings.SMTP_HOST else LogEmailSender()
