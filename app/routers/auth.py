"""认证与账号：注册 / 登录 / 刷新 / 退出 / 当前用户 / 密码（PRD 3.1）。

不做 SSO、不做第三方联动、不做注销账号、不做密码错误锁定。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import get_current_user, get_db
from app.core.errors import AppError, ErrorCode, unauthorized
from app.core.security import (
    create_access_token,
    hash_password,
    hash_token,
    new_refresh_token,
    verify_password,
)
from app.models.rbac import Role
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.schemas.token import (
    AccessTokenResponse,
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LogoutRequest,
    LoginRequest,
    RefreshRequest,
    ResetPasswordRequest,
    TokenResponse,
)
from app.schemas.user import MeResponse, UserCreate, UserRead, UserUpdate
from app.services.email import get_email_sender
from app.services.notification import (
    EVENT_TYPES,
    ensure_default_subscriptions,
    unread_count,
)
from app.services.rbac import user_data_scopes, user_permissions
from app.services.verification import issue_code, consume_code

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/auth", tags=["auth"])


def to_user_read(user: User) -> UserRead:
    return UserRead(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role_code=user.role_code,
        role_name_key=user.role.name_key if user.role else "",
        avatar_url=user.avatar_url,
        locale=user.locale,
        is_active=user.is_active,
        last_login_at=user.last_login_at,
    )


async def _issue_tokens(db: AsyncSession, user: User, user_agent: str) -> TokenResponse:
    """签发 access + refresh（refresh 只存哈希入库）。"""
    raw_refresh, token_hash, expires_at = new_refresh_token()
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=expires_at,
            user_agent=user_agent[:255],
        )
    )
    await db.commit()
    return TokenResponse(
        access_token=create_access_token(user.id),
        refresh_token=raw_refresh,
        expires_in=settings.JWT_EXPIRE_MINUTES * 60,
    )


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def register(payload: UserCreate, db: AsyncSession = Depends(get_db)) -> UserRead:
    email = payload.email.strip().lower()
    existing = await db.scalar(select(User).where(User.email == email))
    if existing is not None:
        raise AppError(
            status.HTTP_409_CONFLICT, ErrorCode.EMAIL_TAKEN, "该邮箱已注册"
        )

    role = await db.scalar(select(Role).where(Role.code == payload.role_code))
    if role is None:
        raise AppError(
            status.HTTP_400_BAD_REQUEST, ErrorCode.ROLE_NOT_FOUND, "角色不存在"
        )

    user = User(
        email=email,
        full_name=payload.full_name.strip(),
        password_hash=hash_password(payload.password),
        role_id=role.id,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    await ensure_default_subscriptions(db, user.id)
    return to_user_read(user)


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    email = payload.email.strip().lower()
    user = await db.scalar(select(User).where(User.email == email))
    # 账号不存在与密码错误统一文案，防枚举
    if user is None or not verify_password(payload.password, user.password_hash):
        raise AppError(
            status.HTTP_401_UNAUTHORIZED,
            ErrorCode.INVALID_CREDENTIALS,
            "邮箱或密码不正确",
        )
    if not user.is_active:
        raise AppError(
            status.HTTP_403_FORBIDDEN, ErrorCode.USER_DISABLED, "账号已停用"
        )

    user.last_login_at = datetime.now(tz=timezone.utc)
    await db.commit()
    return await _issue_tokens(db, user, request.headers.get("user-agent", ""))


@router.post("/refresh", response_model=AccessTokenResponse)
async def refresh(
    payload: RefreshRequest, db: AsyncSession = Depends(get_db)
) -> AccessTokenResponse:
    """用 refresh token 换新的 access token（本期不轮换 refresh）。"""
    token_hash = hash_token(payload.refresh_token)
    now = datetime.now(tz=timezone.utc)
    record = await db.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    if (
        record is None
        or record.revoked_at is not None
        or record.expires_at <= now
    ):
        raise AppError(
            status.HTTP_401_UNAUTHORIZED,
            ErrorCode.REFRESH_INVALID,
            "登录已失效，请重新登录",
        )

    user = await db.get(User, record.user_id)
    if user is None or not user.is_active:
        raise unauthorized(ErrorCode.USER_DISABLED, "账号已停用")

    return AccessTokenResponse(
        access_token=create_access_token(user.id),
        expires_in=settings.JWT_EXPIRE_MINUTES * 60,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    payload: LogoutRequest, db: AsyncSession = Depends(get_db)
) -> Response:
    """退出登录：吊销该 refresh token，其余设备不受影响。"""
    token_hash = hash_token(payload.refresh_token)
    record = await db.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    if record is not None and record.revoked_at is None:
        record.revoked_at = datetime.now(tz=timezone.utc)
        await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=MeResponse)
async def me(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> MeResponse:
    return MeResponse(
        user=to_user_read(user),
        permissions=sorted(user_permissions(user)),
        scopes=await user_data_scopes(db, user),
        unread_count=await unread_count(db, user.id),
    )


@router.patch("/me", response_model=UserRead)
async def update_me(
    payload: UserUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserRead:
    if payload.full_name is not None:
        user.full_name = payload.full_name.strip()
    if payload.avatar_url is not None:
        user.avatar_url = payload.avatar_url.strip()
    if payload.locale is not None:
        user.locale = payload.locale.strip()
    await db.commit()
    await db.refresh(user)
    return to_user_read(user)


@router.post("/password/change", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    payload: ChangePasswordRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    if not verify_password(payload.old_password, user.password_hash):
        raise AppError(
            status.HTTP_400_BAD_REQUEST, ErrorCode.PASSWORD_MISMATCH, "原密码不正确"
        )
    user.password_hash = hash_password(payload.new_password)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/password/forgot", status_code=status.HTTP_202_ACCEPTED)
async def forgot_password(
    payload: ForgotPasswordRequest, db: AsyncSession = Depends(get_db)
) -> dict:
    """申请验证码。邮箱不存在时也返回 202（防枚举），只是不发信。"""
    email = payload.email.strip().lower()
    result: dict = {"message": "如邮箱已注册，验证码将发送至该邮箱"}

    user = await db.scalar(select(User).where(User.email == email))
    if user is None:
        return result

    code = await issue_code(email)
    subject = "PrepPilot 密码重置验证码"
    body = (
        f"你正在重置 PrepPilot 账号密码。\n\n"
        f"验证码：{code}\n"
        f"有效期：5 分钟。若非本人操作请忽略此邮件。\n"
    )

    try:
        from app.tasks.tasks import send_mail

        send_mail.delay(email, subject, body)
    except Exception as exc:  # noqa: BLE001  Celery/RabbitMQ 不可用时降级为同步发送
        logger.warning("异步发信投递失败，改为同步发送：%s", exc)
        try:
            get_email_sender().send(email, subject, body)
        except Exception as send_exc:  # noqa: BLE001
            logger.error("发信失败（验证码已生成但无法送达）：%s", send_exc)

    # 开发环境回显，便于无 SMTP 时走通演示流程；生产必须为 false
    if settings.DEV_ECHO_CODE and settings.ENV != "prod":
        result["dev_code"] = code
    return result


@router.post("/password/reset", status_code=status.HTTP_204_NO_CONTENT)
async def reset_password(
    payload: ResetPasswordRequest, db: AsyncSession = Depends(get_db)
) -> Response:
    email = payload.email.strip().lower()
    ok = await consume_code(email, payload.code)
    if not ok:
        raise AppError(
            status.HTTP_400_BAD_REQUEST, ErrorCode.CODE_INVALID, "验证码错误或已过期"
        )

    user = await db.scalar(select(User).where(User.email == email))
    if user is None:
        raise AppError(
            status.HTTP_404_NOT_FOUND, ErrorCode.USER_NOT_FOUND, "用户不存在"
        )

    user.password_hash = hash_password(payload.new_password)
    # 重置密码后吊销该用户所有 refresh token，强制重新登录
    now = datetime.now(tz=timezone.utc)
    records = await db.scalars(
        select(RefreshToken).where(
            RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None)
        )
    )
    for record in records:
        record.revoked_at = now
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/notification-events", response_model=list[str])
async def list_notification_events() -> list[str]:
    """订阅事件类型枚举（前端渲染订阅设置用）。"""
    return EVENT_TYPES
