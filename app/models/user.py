from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class User(Base):
    """系统用户。

    权限不直接挂在用户上：用户 → 角色（roles）→ 权限码（permissions），
    数据可见范围另由 role_data_scopes 表达（见 app/models/rbac.py）。
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(
        String(255), unique=True, index=True, nullable=False
    )
    full_name: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    role_id: Mapped[int | None] = mapped_column(
        ForeignKey("roles.id"), index=True, nullable=True
    )
    avatar_url: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    locale: Mapped[str] = mapped_column(String(16), default="zh-CN", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    role: Mapped["Role | None"] = relationship("Role", lazy="selectin")

    @property
    def role_code(self) -> str:
        """角色 code（无角色时退化为 interviewer，保证前端始终有可用菜单）。"""
        return self.role.code if self.role is not None else "interviewer"
