"""AI 备面工作台接口（PRD 3.4 / §5.9 P09）。

权限：`workbench:enter_all`（HR 主管）/ `workbench:enter_view`（HR）/
`workbench:enter_assigned`（面试官）三者任一可进；**写操作额外要求「是被指派的面试官」**，
由 service 的 `ensure_can_edit` 判（2026-10-01 拍板：HR 与 HR 主管一律只读）。

本期只开放 Step 1（矩阵）。后续 Step 的接口随各自阶段加，
前端把未实现的步骤标 disabled，不会出现「点了报错」的半截状态。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, require_any_perm
from app.models.user import User
from app.schemas.workbench import (
    DurationIn,
    MatrixIn,
    MatrixSaveOut,
    WorkbenchOut,
)
from app.services import workbench as svc

router = APIRouter(prefix="/workbench", tags=["workbench"])

ENTER_PERMS = ("workbench:enter_all", "workbench:enter_assigned", "workbench:enter_view")


@router.get("/sessions/{session_id}", response_model=WorkbenchOut)
async def get_workbench(
    session_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_any_perm(*ENTER_PERMS)),
) -> WorkbenchOut:
    """P09 一屏：顶部信息条 + 矩阵 + 步骤指示器。"""
    return await svc.workbench_view(db, user, session_id)


@router.post("/sessions/{session_id}/matrix/generate", response_model=MatrixSaveOut)
async def generate_matrix(
    session_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_any_perm(*ENTER_PERMS)),
) -> MatrixSaveOut:
    """C3 纯 LLM 生成能力-证据矩阵，覆盖旧矩阵（保留已勾选的本轮重点）。"""
    session = await svc.get_session_or_404(db, session_id)
    return await svc.generate_matrix(db, user, session)


@router.put("/sessions/{session_id}/matrix", response_model=MatrixSaveOut)
async def save_matrix(
    session_id: int,
    payload: MatrixIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_any_perm(*ENTER_PERMS)),
) -> MatrixSaveOut:
    """整块保存人工编辑（增删行 / 改证据与考察重点 / 勾本轮重点 / 调顺序）。"""
    session = await svc.get_session_or_404(db, session_id)
    return await svc.save_matrix(db, user, session, payload)


@router.put("/sessions/{session_id}/duration")
async def update_duration(
    session_id: int,
    payload: DurationIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_any_perm(*ENTER_PERMS)),
) -> dict:
    """改本轮时长（分钟）。会话级而非职位级：一面 45 分钟、二面 60 分钟是常态。"""
    session = await svc.get_session_or_404(db, session_id)
    minutes = await svc.update_duration(db, user, session, payload)
    return {"duration_minutes": minutes}
