"""登录态相关 Schema：短期 access token + 长效 refresh token。"""
from pydantic import BaseModel, Field

from app.schemas.user import EMAIL_PATTERN


class LoginRequest(BaseModel):
    email: str = Field(min_length=5, max_length=255, pattern=EMAIL_PATTERN)
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    """登录响应。"""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # access token 有效期（秒）


class AccessTokenResponse(BaseModel):
    """刷新响应：只发新 access token（refresh 不轮换）。"""

    access_token: str
    token_type: str = "bearer"
    expires_in: int


class ForgotPasswordRequest(BaseModel):
    email: str = Field(min_length=5, max_length=255, pattern=EMAIL_PATTERN)


class ResetPasswordRequest(BaseModel):
    email: str = Field(min_length=5, max_length=255, pattern=EMAIL_PATTERN)
    code: str = Field(min_length=6, max_length=6)
    new_password: str = Field(min_length=8, max_length=128)


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str = Field(min_length=8, max_length=128)
