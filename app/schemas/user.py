"""用户相关 Schema。

说明：不用 pydantic.EmailStr —— 它需要额外依赖 email-validator，
这里用正则约束即可（云端/镜像依赖越少越稳）。
"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class UserCreate(BaseModel):
    """注册。邮箱必填（忘记密码靠邮箱验证码重置）；演示期角色可选。"""

    email: str = Field(min_length=5, max_length=255, pattern=EMAIL_PATTERN)
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(default="", max_length=100)
    role_code: str = Field(default="interviewer", max_length=32)


class UserUpdate(BaseModel):
    """修改本人资料（头像下拉里改昵称 / 头像 / 语言偏好）。"""

    full_name: str | None = Field(default=None, max_length=100)
    avatar_url: str | None = Field(default=None, max_length=500)
    locale: str | None = Field(default=None, max_length=16)


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    full_name: str
    role_code: str
    role_name_key: str = ""
    avatar_url: str = ""
    locale: str = "zh-CN"
    is_active: bool
    last_login_at: datetime | None = None


class MeResponse(BaseModel):
    """/auth/me：前端据此渲染侧边栏、按钮与数据范围。"""

    user: UserRead
    permissions: list[str]
    scopes: dict[str, str]
    unread_count: int


class UserOption(BaseModel):
    """下拉用的人员选项（职位负责人 / 轮次面试官）。"""

    id: int
    name: str = ""
    email: str = ""
    role_code: str = ""
