"""面试会话（PRD 3.4 工作台载体，S2 派单产物）。

权限：`workbench:enter_all`（HR 主管，全权）/ `workbench:enter_view`（HR，只读）/
`workbench:enter_assigned`（面试官，仅被指派的会话）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, require_any_perm
from app.core.errors import not_found
from app.models.session import InterviewSession
from app.models.user import User
from app.schemas.session import SessionOut
from app.services import dispatch as svc

router = APIRouter(prefix="/sessions", tags=["session"])

ENTER_PERMS = ("workbench:enter_all", "workbench:enter_assigned", "workbench:enter_view")


async def _out(db: AsyncSession, s: InterviewSession) -> SessionOut:
    from app.models.candidate import Candidate
    from app.models.position import Position
    from app.models.user import User as UserModel

    candidate = await db.get(Candidate, s.candidate_id)
    position = await db.get(Position, s.position_id)
    interviewer = await db.get(UserModel, s.interviewer_id)
    return SessionOut(
        id=s.id,
        application_id=s.application_id,
        candidate_id=s.candidate_id,
        candidate_name=candidate.name if candidate else "",
        position_id=s.position_id,
        position_name=position.name if position else "",
        round_id=s.round_id,
        round_type=s.round_type,
        round_name=s.round_name,
        interviewer_id=s.interviewer_id,
        interviewer_name=interviewer.full_name if interviewer else "",
        status=s.status,
        submitted_at=s.submitted_at,
        created_at=s.created_at,
        updated_at=s.updated_at,
    )


@router.get("", response_model=list[SessionOut])
async def list_sessions(
    position_id: int | None = None,
    candidate_id: int | None = None,
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_any_perm(*ENTER_PERMS)),
) -> list[SessionOut]:
    """会话列表。面试官只看到派给自己的（BR-16）。"""
    stmt = select(InterviewSession)

    allowed = await svc.visible_session_ids(db, user)
    if allowed is not None:
        if not allowed:
            return []
        stmt = stmt.where(InterviewSession.id.in_(allowed))
    if position_id is not None:
        stmt = stmt.where(InterviewSession.position_id == position_id)
    if candidate_id is not None:
        stmt = stmt.where(InterviewSession.candidate_id == candidate_id)
    if status:
        stmt = stmt.where(InterviewSession.status == status)

    rows = await db.scalars(stmt.order_by(InterviewSession.id.desc()).limit(limit))
    return [await _out(db, s) for s in rows]


@router.get("/{session_id}", response_model=SessionOut)
async def get_session(
    session_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_any_perm(*ENTER_PERMS)),
) -> SessionOut:
    session = await db.get(InterviewSession, session_id)
    if session is None:
        raise not_found("会话不存在")
    await svc.ensure_can_view_session(db, user, session)
    return await _out(db, session)
