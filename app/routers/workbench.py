"""AI 备面工作台接口（PRD 3.4 / §5.9 P09 · §5.10 P10）。

权限：`workbench:enter_all`（HR 主管）/ `workbench:enter_view`（HR）/
`workbench:enter_assigned`（面试官）三者任一可进；**写操作额外要求「是被指派的面试官」**，
由 service 的 `ensure_can_edit` 判（2026-10-01 拍板：HR 与 HR 主管一律只读）。

Step 2（问题链）走**异步**：`POST .../chain/generate` 只投递任务并返回，
前端轮询 `GET /workbench/sessions/{id}` 里的 `chain.status`
（idle / running / ready / failed）。带 RAG 检索后云端 15~30s，
同步等待既撞 nginx 120s 又让页面完全无反馈（BR-12）。

Step 3 及以后的接口随各自阶段加，前端把未实现的步骤标 disabled，
不会出现「点了报错」的半截状态。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, require_any_perm
from app.core.errors import ErrorCode, bad_request
from app.models.session import CHAIN_RUNNING
from app.models.user import User
from app.schemas.workbench import (
    ChainIn,
    ChainSaveOut,
    ChainTaskOut,
    DurationIn,
    MatrixIn,
    MatrixSaveOut,
    WorkbenchOut,
)
from app.services import workbench as svc
from app.services import question_chain as qc
from app.tasks.tasks import generate_question_chain

router = APIRouter(prefix="/workbench", tags=["workbench"])

ENTER_PERMS = ("workbench:enter_all", "workbench:enter_assigned", "workbench:enter_view")


@router.get("/sessions/{session_id}", response_model=WorkbenchOut)
async def get_workbench(
    session_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_any_perm(*ENTER_PERMS)),
) -> WorkbenchOut:
    """P09 / P10 一屏：顶部信息条 + 矩阵 + 问题链 + 步骤指示器。

    问题链的异步状态也在这一屏里，前端轮询这一个接口就够了。
    """
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


# ---------------------------------------------------------------- Step2 问题链


@router.post("/sessions/{session_id}/chain/generate", response_model=ChainTaskOut)
async def generate_chain(
    session_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_any_perm(*ENTER_PERMS)),
) -> ChainTaskOut:
    """投递「生成问题链」任务（异步）。

    前置校验在**投递前**做：放在 worker 里做的话，面试官会先看着进度条转 20 秒，
    最后才被告知「还没生成矩阵」—— 那 20 秒纯属浪费。
    """
    session = await svc.get_session_or_404(db, session_id)
    qc.ensure_can_edit(user, session)
    if session.chain_status == CHAIN_RUNNING:
        raise bad_request(
            ErrorCode.WORKBENCH_CHAIN_RUNNING, "上一次生成还在进行中，请稍候"
        )
    if not ((session.matrix_json or {}).get("rows") or []):
        raise bad_request(
            ErrorCode.WORKBENCH_CHAIN_NO_MATRIX, "请先生成能力-证据矩阵，再生成问题链"
        )

    task = generate_question_chain.delay(session_id, user.id)
    await qc.mark_running(db, session, str(task.id))
    return ChainTaskOut(session_id=session.id, task_id=str(task.id), status=CHAIN_RUNNING)


@router.put("/sessions/{session_id}/chain", response_model=ChainSaveOut)
async def save_chain(
    session_id: int,
    payload: ChainIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_any_perm(*ENTER_PERMS)),
) -> ChainSaveOut:
    """整块保存人工微调（改题 / 调顺序 / 调耗时 / 删节点）。"""
    session = await svc.get_session_or_404(db, session_id)
    return await qc.save_chain(db, user, session, payload)


@router.post(
    "/sessions/{session_id}/chain/nodes/{node_id}/regenerate",
    response_model=ChainSaveOut,
)
async def regenerate_node(
    session_id: int,
    node_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_any_perm(*ENTER_PERMS)),
) -> ChainSaveOut:
    """「换一换」：只重生成这一个节点。单节点一次模型调用，同步返回。"""
    session = await svc.get_session_or_404(db, session_id)
    return await qc.regenerate_node(db, user, session, node_id)
