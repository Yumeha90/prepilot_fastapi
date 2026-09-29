"""RBAC：角色 / 权限码 / 角色-权限关联 / 数据可见范围。

设计要点：
- permissions 只表达「能不能做」（决定菜单、按钮、接口访问）；
- role_data_scopes 表达「能看到多少」（决定查询过滤：all / assigned / own），
  用于支撑 PRD §2.3 中「仅被指派」「仅本人面评」「仅 r1/r2 阶段列」这类行级约束。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

# 角色 ←多对多→ 权限码
role_permissions = Table(
    "role_permissions",
    Base.metadata,
    Column("role_id", ForeignKey("roles.id"), primary_key=True),
    Column("permission_id", ForeignKey("permissions.id"), primary_key=True),
)


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(
        String(32), unique=True, index=True, nullable=False
    )
    # 名称走前端 i18n（role.admin / role.hr_lead ...），库里只存 key
    name_key: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    description: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    is_system: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    permissions: Mapped[list["Permission"]] = relationship(
        "Permission", secondary=role_permissions, lazy="selectin"
    )


class Permission(Base):
    __tablename__ = "permissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 形如 module:action，例如 position:view_all
    code: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    module: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    description: Mapped[str] = mapped_column(String(255), default="", nullable=False)


class RoleDataScope(Base):
    """角色对某类资源的数据可见范围。

    resource: position / candidate / evaluation / stage_column
    scope:    all（全部） / assigned（仅被指派） / own（仅本人）
    """

    __tablename__ = "role_data_scopes"
    __table_args__ = (UniqueConstraint("role_id", "resource", name="uq_role_resource"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    role_id: Mapped[int] = mapped_column(
        ForeignKey("roles.id"), index=True, nullable=False
    )
    resource: Mapped[str] = mapped_column(String(32), nullable=False)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
