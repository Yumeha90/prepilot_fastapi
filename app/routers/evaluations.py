"""P17 面评列表与详情（PRD §5.15 / BR-17）。

权限：`evaluation:view_all`（超管 / HR 主管 / HR：全部）或
`evaluation:view_own`（面试官：仅本人撰写的面评）。两者都没有 → 403。

路由层只做「有没有这个权限」，**具体哪几条能看到由 service 判** ——
面试官访问他人面评是 403，而不是返回一个空列表（BR-17 要求连 URL 直达都要挡）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, require_any_perm
from app.models.user import User
from app.schemas.evaluation import EvaluationDetail, EvaluationSummary
from app.services import evaluation_view as svc

router = APIRouter(prefix="/evaluations", tags=["evaluations"])

VIEW_PERMS = ("evaluation:view_all", "evaluation:view_own")


@router.get("", response_model=list[EvaluationSummary])
async def list_evaluations(
    position_id: int | None = Query(default=None),
    keyword: str | None = Query(default=None, max_length=50),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_any_perm(*VIEW_PERMS)),
) -> list[EvaluationSummary]:
    """面评列表：只看**已提交**的。草稿不是面评，不给 HR 看半截的东西。"""
    return await svc.list_evaluations(
        db, user, position_id=position_id, keyword=keyword, limit=limit
    )


@router.get("/{session_id}", response_model=EvaluationDetail)
async def get_evaluation(
    session_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_any_perm(*VIEW_PERMS)),
) -> EvaluationDetail:
    """P17 面评详情（只读）。面试官打开他人面评 → 403 + 前端跳回看板。"""
    return await svc.evaluation_detail(db, user, session_id)
