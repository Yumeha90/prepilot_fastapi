"""密码重置验证码（Redis，不落库）。

规则：6 位数字、5 分钟有效、60 秒重发冷却、单邮箱每日 10 次上限。
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

from app.core.errors import AppError, ErrorCode
from app.core.redis import redis_client

CODE_TTL = 300  # 5 分钟
RESEND_TTL = 60  # 60 秒冷却
DAILY_LIMIT = 10

_KEY_CODE = "pwd_code:{email}"
_KEY_RESEND = "pwd_resend:{email}"
_KEY_DAILY = "pwd_daily:{email}:{day}"


def _now_day() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y%m%d")


def new_code() -> str:
    """6 位数字验证码。"""
    return f"{secrets.randbelow(1_000_000):06d}"


async def issue_code(email: str) -> str:
    """签发验证码，返回明文。违反冷却 / 日限时抛错（带错误码）。"""
    key_email = email.strip().lower()

    if await redis_client.exists(_KEY_RESEND.format(email=key_email)):
        raise AppError(429, ErrorCode.RESEND_TOO_SOON, "发送过于频繁，请稍后再试")

    day = _now_day()
    daily_key = _KEY_DAILY.format(email=key_email, day=day)
    used = int(await redis_client.get(daily_key) or 0)
    if used >= DAILY_LIMIT:
        raise AppError(429, ErrorCode.DAILY_LIMIT, "今日验证码发送次数已达上限")
    pipe = redis_client.pipeline()
    pipe.incr(daily_key)
    pipe.expire(daily_key, 86_400)
    await pipe.execute()

    code = new_code()
    await redis_client.setex(
        _KEY_CODE.format(email=key_email), CODE_TTL, code
    )
    await redis_client.setex(_KEY_RESEND.format(email=key_email), RESEND_TTL, "1")
    return code


async def consume_code(email: str, code: str) -> bool:
    """校验并消费验证码（一次性）。"""
    key = _KEY_CODE.format(email=email.strip().lower())
    stored = await redis_client.get(key)
    if stored is None or stored != code.strip():
        return False
    await redis_client.delete(key)
    return True
