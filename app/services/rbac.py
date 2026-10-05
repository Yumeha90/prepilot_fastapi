"""RBAC 运行时：解析用户权限码与数据可见范围。

权限两层（见 app/models/rbac.py 注释）：
- permissions：module:action，决定菜单 / 按钮 / 接口能否访问
- data_scopes：all / assigned / own，决定查询时如何过滤数据

权限不写进 JWT：每次请求随用户查库取出，超管改角色后立即生效。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode, forbidden
from app.models.rbac import RoleDataScope
from app.models.user import User

# 权限码全集（seed 会按此写入 permissions 表，保持与 PRD §2.3 角色权限矩阵一致）
PERMISSION_CATALOG: list[tuple[str, str, str]] = [
    # (code, module, 描述)
    ("dashboard:view", "dashboard", "查看工作台首页"),
    ("position:view_all", "position", "查看全部职位与 JD"),
    ("position:view_assigned", "position", "查看被指派职位"),
    ("position:edit", "position", "创建/编辑职位与 JD"),
    ("candidate:view_all", "candidate", "查看全部候选人"),
    ("candidate:view_assigned", "candidate", "查看被指派候选人"),
    ("candidate:upload_resume", "candidate", "上传简历"),
    ("candidate:dispose", "candidate", "候选人终结处置（推进/Offer/淘汰/人才库）"),
    ("workbench:enter_all", "workbench", "进入任意 AI 备面工作台"),
    ("workbench:enter_view", "workbench", "只读查看 AI 备面工作台"),
    ("workbench:enter_assigned", "workbench", "进入自己被指派的工作台会话"),
    ("evaluation:submit", "evaluation", "提交面评"),
    ("evaluation:view_all", "evaluation", "查看全部面评"),
    ("evaluation:view_own", "evaluation", "查看本人撰写的面评"),
    ("match:view", "match", "查看人岗匹配分与反馈"),
    ("match:feedback", "match", "提交匹配分反馈"),
    ("system:purge", "system", "数据粉碎"),
    ("system:role_view", "system", "查看角色权限矩阵"),
    ("system:role_manage", "system", "维护角色与权限"),
    ("system:lifecycle", "system", "数据生命周期管理"),
    ("notification:manage", "notification", "管理通知订阅"),
]

# 角色 → 权限码（PRD §2.3 角色与权限矩阵）
ROLE_PERMISSIONS: dict[str, list[str]] = {
    "admin": [
        "dashboard:view",
        "position:view_all",
        "position:edit",
        "candidate:view_all",
        "candidate:upload_resume",
        "candidate:dispose",
        "workbench:enter_all",
        "evaluation:view_all",
        "match:view",
        "match:feedback",
        "system:purge",
        "system:role_view",
        "system:role_manage",
        "system:lifecycle",
        "notification:manage",
    ],
    "hr_lead": [
        "dashboard:view",
        "position:view_all",
        "position:edit",
        "candidate:view_all",
        "candidate:upload_resume",
        "candidate:dispose",
        "workbench:enter_all",
        "evaluation:view_all",
        "match:view",
        "match:feedback",
        "system:role_view",
        "notification:manage",
    ],
    "hr": [
        "dashboard:view",
        "position:view_all",
        "position:edit",
        "candidate:view_all",
        "candidate:upload_resume",
        "candidate:dispose",
        "workbench:enter_view",
        "evaluation:view_all",
        "match:view",
        "match:feedback",
        "notification:manage",
    ],
    "interviewer": [
        "dashboard:view",
        "position:view_assigned",
        "candidate:view_assigned",
        # 不持有 candidate:upload_resume（BR-15 方案①，2026-10-04 拍板）：
        # 建档（上传/解析/确认）统一由 HR 承担。面试官上传后候选人停在 pending 列，
        # 而面试官看板只有 r1/r2 两列——他自己刚传的人自己看不见，规则自相矛盾。
        "workbench:enter_assigned",
        "evaluation:submit",
        "evaluation:view_own",
        "notification:manage",
    ],
}

# 角色 → 数据可见范围（resource → scope）
ROLE_DATA_SCOPES: dict[str, dict[str, str]] = {
    "admin": {
        "position": "all",
        "candidate": "all",
        "evaluation": "all",
        "stage_column": "all",
    },
    "hr_lead": {
        "position": "all",
        "candidate": "all",
        "evaluation": "all",
        "stage_column": "all",
    },
    "hr": {
        "position": "all",
        "candidate": "all",
        "evaluation": "all",
        "stage_column": "all",
    },
    "interviewer": {
        "position": "assigned",
        "candidate": "assigned",
        "evaluation": "own",
        "stage_column": "r1r2",
    },
}


def user_permissions(user: User) -> set[str]:
    """取出用户的权限码集合（角色未配置时退化为空集合）。"""
    if user.role is None:
        return set()
    return {p.code for p in user.role.permissions}


async def user_data_scopes(db: AsyncSession, user: User) -> dict[str, str]:
    """取出用户的数据可见范围。"""
    if user.role is None:
        return {}
    rows = await db.scalars(
        select(RoleDataScope).where(RoleDataScope.role_id == user.role.id)
    )
    return {row.resource: row.scope for row in rows}


def ensure_permission(user: User, code: str) -> None:
    """权限不足时抛 403（带错误码，供前端三语映射）。"""
    if code not in user_permissions(user):
        raise AppError(403, ErrorCode.FORBIDDEN, "无权限执行该操作")


__all__ = [
    "PERMISSION_CATALOG",
    "ROLE_PERMISSIONS",
    "ROLE_DATA_SCOPES",
    "user_permissions",
    "user_data_scopes",
    "ensure_permission",
    "forbidden",
]
