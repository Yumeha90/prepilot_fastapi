"""可选人员列表（下拉用）。

口径（2026-09-30 定）：
- `purpose=interview`：轮次面试官候选。筛选「有 `evaluation:submit` **或** `candidate:dispose`」
  —— 因为 `evaluation:submit` 只授予了面试官角色，而 HR 面 / Offer 轮的责任人通常是
  HR 或 HR 主管；若只按 submit 筛，这两轮在下拉里将选不到任何 HR 用户。
  这里放宽筛选条件而**不动权限矩阵**，避免污染 `evaluation:submit` 的权限语义。
- `purpose=owner`：职位 HR 负责人候选，即有 `position:edit` 的用户。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.user import UserOption
from app.services.rbac import user_permissions

router = APIRouter(prefix="/users", tags=["user"])

INTERVIEW_PERMS = {"evaluation:submit", "candidate:dispose"}
OWNER_PERMS = {"position:edit"}


@router.get("/options", response_model=list[UserOption])
async def user_options(
    purpose: str = Query(default="interview", pattern="^(interview|owner)$"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[UserOption]:
    required = INTERVIEW_PERMS if purpose == "interview" else OWNER_PERMS
    rows = list(
        await db.scalars(
            select(User).where(User.is_active.is_(True)).order_by(User.full_name, User.id)
        )
    )
    out: list[UserOption] = []
    for u in rows:
        perms = user_permissions(u)
        if not (perms & required):
            continue
        out.append(
            UserOption(
                id=u.id,
                name=u.full_name or u.email,
                email=u.email,
                role_code=u.role_code,
            )
        )
    return out
