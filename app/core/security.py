"""密码哈希与 JWT。

不使用 passlib：其 1.7.4 与 bcrypt>=4.1 存在 __about__ 兼容缺陷。
直接使用 bcrypt 官方 API（hashpw / checkpw）。

Token 设计（PRD 3.1.1）：
- access token：短期 JWT（默认 30 分钟），只带 sub，权限每次查库，避免权限变更滞后；
- refresh token：随机串（非 JWT），只存哈希入库，退出登录即吊销，可审计。
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
from jose import JWTError, jwt

from app.core.config import get_settings

settings = get_settings()

# bcrypt 只处理前 72 字节，超长密码先截断，避免抛错
_BCRYPT_MAX_BYTES = 72


def _to_bytes(password: str) -> bytes:
    return password.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_to_bytes(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(_to_bytes(plain_password), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def _encode(payload: dict, expires_delta: timedelta) -> str:
    now = datetime.now(tz=timezone.utc)
    payload = {**payload, "iat": now, "exp": now + expires_delta}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_access_token(subject: str | int, expires_delta: timedelta | None = None) -> str:
    """短期 access token（默认 30 分钟）。"""
    delta = expires_delta or timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    return _encode({"sub": str(subject), "typ": "access"}, delta)


def decode_access_token(token: str) -> str | None:
    """校验 access token，成功返回 subject（user id），失败返回 None。"""
    try:
        payload = jwt.decode(
            token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM]
        )
    except JWTError:
        return None
    if payload.get("typ") != "access":
        return None
    return payload.get("sub")


def hash_token(raw_token: str) -> str:
    """refresh token 只存哈希入库，明文不落库。"""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def new_refresh_token() -> tuple[str, str, datetime]:
    """生成 refresh token，返回 (明文, 哈希, 过期时间)。明文只在签发响应中出现一次。"""
    raw = secrets.token_urlsafe(32)
    expires_at = datetime.now(tz=timezone.utc) + timedelta(
        days=settings.JWT_REFRESH_EXPIRE_DAYS
    )
    return raw, hash_token(raw), expires_at
