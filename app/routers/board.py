"""候选人看板 P08 与阶段流转（PRD 3.3.3 / §5.8）。

权限：
- 看板读取 `candidate:view_all` / `view_assigned`（与候选人列表一致）
- 阶段流转 `candidate:dispose`（只有 HR / HR 主管 / 超管有）

面试官只拿到 r1 / r2 两列（BR-15），且列是后端下发的 —— 前端不参与判定。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, require_any_perm, require_perm
from app.models.user import User
from app.schemas.board import BoardOut, TransitionIn, TransitionOut
from app.services import board as svc

router = APIRouter(prefix="/board", tags=["board"])

VIEW_PERMS = ("candidate:view_all", "candidate:view_assigned")


@router.get("", response_model=BoardOut)
async def get_board(
    position_id: int | None = None,
    keyword: str = Query(default=""),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_any_perm(*VIEW_PERMS)),
) -> BoardOut:
    """看板：按阶段分列返回卡片。面试官只可见 r1 / r2 且只看被指派的（BR-14 / BR-15）。"""
    return await svc.board_data(db, user, position_id=position_id, keyword=keyword)


@router.post("/applications/{application_id}/transition", response_model=TransitionOut)
async def transition(
    application_id: int,
    payload: TransitionIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_perm("candidate:dispose")),
) -> TransitionOut:
    """阶段流转：推进 / 退回 / 录用 / 淘汰 / 人才库 / 作废。

    推进按流程配置自动找下一轮并派单；派单失败则整体失败（stage 不变）。
    """
    application, notice = await svc.transition(db, user, application_id, payload.action)
    from app.models.candidate import Candidate

    candidate = await db.get(Candidate, application.candidate_id)
    return TransitionOut(
        application_id=application.id,
        candidate_id=application.candidate_id,
        candidate_name=candidate.name if candidate else "",
        stage=application.stage,
        notice=notice,
    )
