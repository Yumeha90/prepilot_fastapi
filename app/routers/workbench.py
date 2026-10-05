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
    EvaluationIn,
    EvaluationOut,
    EvaluationSaveOut,
    FairnessAcceptIn,
    FairnessOut,
    FairnessRewriteIn,
    MatrixIn,
    MatrixSaveOut,
    SubmissionIn,
    SubmissionOut,
    WorkbenchOut,
)
from app.services import evaluation as eval_svc
from app.services import fairness as fairness_svc
from app.services import submission as submission_svc
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


# ---------------------------------------------------------------- Step3 公平性


@router.post("/sessions/{session_id}/fairness/scan", response_model=FairnessOut)
async def scan_fairness(
    session_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_any_perm(*ENTER_PERMS)),
) -> FairnessOut:
    """C5 公平性扫描（同步，P95 ≤ 10s）。

    问题链生成时已经自动预扫过一次（PRD §6.4 质量门禁），这里是**改完之后**的复扫：
    面试官手改过题目、或只是想再看一次结论，都走这个接口。
    """
    session = await svc.get_session_or_404(db, session_id)
    return await fairness_svc.scan(db, user, session)


@router.post(
    "/sessions/{session_id}/fairness/findings/{finding_id}/apply",
    response_model=FairnessOut,
)
async def apply_rewrite(
    session_id: int,
    finding_id: str,
    payload: FairnessRewriteIn | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_any_perm(*ENTER_PERMS)),
) -> FairnessOut:
    """采纳改写：替换问题文本后**重新扫描**，返回新的结论。"""
    session = await svc.get_session_or_404(db, session_id)
    return await fairness_svc.apply_rewrite(db, user, session, finding_id, payload)


@router.post(
    "/sessions/{session_id}/fairness/findings/{finding_id}/accept",
    response_model=FairnessOut,
)
async def accept_finding(
    session_id: int,
    finding_id: str,
    payload: FairnessAcceptIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_any_perm(*ENTER_PERMS)),
) -> FairnessOut:
    """警告级「保留并记录原因」：不改写但必须写明为什么这么问是合理的。"""
    session = await svc.get_session_or_404(db, session_id)
    return await fairness_svc.accept_finding(db, user, session, finding_id, payload)


# ---------------------------------------------------------------- Step4 评分与面评


@router.put("/sessions/{session_id}/evaluation", response_model=EvaluationSaveOut)
async def save_evaluation(
    session_id: int,
    payload: EvaluationIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_any_perm(*ENTER_PERMS)),
) -> EvaluationSaveOut:
    """整块保存评分草稿：手动保存与 30 秒自动保存（BR-13）共用这一个接口。

    **刻意不做完整性校验**：写一半就被拦在门外，等于逼面试官先编一条证据出来。
    缺哪些能力项由返回体里的 `incomplete_count` / `missing` 提示，提交时（Step5）才拦。
    """
    session = await svc.get_session_or_404(db, session_id)
    return await eval_svc.save_draft(db, user, session, payload)


@router.post("/sessions/{session_id}/evaluation/polish", response_model=EvaluationOut)
async def polish_evaluation(
    session_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_any_perm(*ENTER_PERMS)),
) -> EvaluationOut:
    """C6 面评润色（同步）。润色稿**并存不覆盖**原文，要显式采纳才生效（BR-08）。

    生成后立刻跑一次二次合规扫描，命中落进 `polished_flags` 常驻警示。
    """
    session = await svc.get_session_or_404(db, session_id)
    return await eval_svc.polish(db, user, session)


@router.post("/sessions/{session_id}/evaluation/adopt", response_model=EvaluationOut)
async def adopt_polished(
    session_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_any_perm(*ENTER_PERMS)),
) -> EvaluationOut:
    """采纳润色稿：覆写综合评价正文，原文留进 `summary_original`（P17 双栏对比）。"""
    session = await svc.get_session_or_404(db, session_id)
    return await eval_svc.adopt_polished(db, user, session)


# ---------------------------------------------------------------- Step5 校准与提交


@router.post("/sessions/{session_id}/submission/scan", response_model=SubmissionOut)
async def scan_submission(
    session_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_any_perm(*ENTER_PERMS)),
) -> SubmissionOut:
    """提交前的合规扫描（只看不改）。

    单独给一个口子而不是只在提交时扫：点了提交才知道被拦，面试官得回 Step4 改完
    再走一遍流程 —— 先扫一次，被拦的原因在提交之前就摆在他面前。
    """
    session = await svc.get_session_or_404(db, session_id)
    return await submission_svc.scan(db, user, session)


@router.post("/sessions/{session_id}/submission", response_model=SubmissionOut)
async def submit_evaluation(
    session_id: int,
    payload: SubmissionIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_any_perm(*ENTER_PERMS)),
) -> SubmissionOut:
    """提交面评（终态）：校验完整性 → 过合规扫描 → 落结论 → 会话置 `submitted`。

    `confirm=true` 是后端这道闸（与粉碎同口径）。提交后**不变更候选人阶段**：
    面试官只交结论与面评，下一轮由 HR 在 P08 处置（BR-06）。
    """
    session = await svc.get_session_or_404(db, session_id)
    return await submission_svc.submit(db, user, session, payload)
