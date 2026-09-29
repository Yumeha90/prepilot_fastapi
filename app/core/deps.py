"""FastAPI 依赖注入：数据库会话、当前用户、权限校验。"""
from __future__ import annotations

from typing import AsyncGenerator

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import SessionLocal
from app.core.errors import ErrorCode, unauthorized
from app.core.security import decode_access_token
from app.models.user import User
from app.services.rbac import ensure_permission

# auto_error=False：由 get_current_user 自己返回 401，便于统一错误文案
bearer_scheme = HTTPBearer(auto_error=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """解析 access token 并查库取用户（含角色与权限）。

    权限不写进 token，每次请求查库，超管改角色后立即生效。
    """
    if credentials is None:
        raise unauthorized(ErrorCode.TOKEN_INVALID, "未提供认证凭证")

    subject = decode_access_token(credentials.credentials)
    if subject is None:
        raise unauthorized(ErrorCode.TOKEN_INVALID, "凭证无效或已过期")

    try:
        user_id = int(subject)
    except (TypeError, ValueError):
        raise unauthorized(ErrorCode.TOKEN_INVALID, "凭证无效或已过期")

    user = await db.get(User, user_id)
    if user is None:
        raise unauthorized(ErrorCode.USER_NOT_FOUND, "用户不存在")
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": ErrorCode.USER_DISABLED, "message": "账号已停用"},
        )
    return user


def require_perm(code: str):
    """权限校验依赖：`Depends(require_perm("evaluation:view_all"))`。"""

    async def _depend(user: User = Depends(get_current_user)) -> User:
        ensure_permission(user, code)
        return user

    return _depend


async def get_current_user_id(user: User = Depends(get_current_user)) -> int:
    """兼容旧签名：只取用户 id。"""
    return user.id
