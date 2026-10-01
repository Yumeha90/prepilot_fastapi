"""人岗匹配 P18（PRD §5.16 / §6.7 / §6.8）。

权限：`match:view`（HR / HR 主管 / 超管）。**面试官没有这个权限码**，
路由级直接 403，前端再拦一道跳回看板（BR-18）。

前置：简历未确认 / JD 未确认都不能算分 —— 前者没有可信档案，
后者没有权重来源（BR-20 定 JD 权重是加权唯一来源）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, require_perm
from app.core.errors import ErrorCode, bad_request, not_found
from app.models.candidate import Application
from app.models.user import User
from app.schemas.match import MatchFeedbackIn, MatchFeedbackItem, MatchOut
from app.services import candidate as candidate_svc
from app.services import match as svc

router = APIRouter(prefix="/match", tags=["match"])


async def _load(
    db: AsyncSession, user: User, application_id: int
) -> tuple[Application, str]:
    """取应聘记录 + 可见性校验，并给出算分前置的阻断原因（空串 = 可算）。"""
    application = await db.get(Application, application_id)
    if application is None:
        raise not_found("应聘记录不存在")
    from app.models.candidate import CONFIRMED, Candidate
    from app.models.position import Position

    candidate = await db.get(Candidate, application.candidate_id)
    if candidate is None:
        raise not_found("候选人不存在")
    await candidate_svc.ensure_can_view(db, user, candidate)

    blocked = ""
    if candidate.profile_status != CONFIRMED:
        blocked = "profile_not_confirmed"
    else:
        position = await db.get(Position, application.position_id)
        if position is None or position.jd_status != "confirmed":
            blocked = "jd_not_confirmed"
    return application, blocked


@router.get("/applications/{application_id}", response_model=MatchOut)
async def get_match(
    application_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_perm("match:view")),
) -> MatchOut:
    """P18 一屏数据。没有分时只回信息条 + `has_score=False`，不自动算。"""
    application, blocked = await _load(db, user, application_id)
    payload = await svc.detail_payload(db, application)
    return MatchOut(**payload, blocked_reason=blocked)


@router.post("/applications/{application_id}/recompute", response_model=MatchOut)
async def recompute_match(
    application_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_perm("match:view")),
) -> MatchOut:
    """重算（JD 变更后置 STALE 的分数由这里补）。

    判定与加权**同步**落定，AI 总结**异步**补写 —— 接口返回时总结可能还是 pending，
    前端轮询或下次进入即可看到。
    """
    application, blocked = await _load(db, user, application_id)
    if blocked == "profile_not_confirmed":
        raise bad_request(
            ErrorCode.CANDIDATE_NOT_CONFIRMED, "该候选人简历尚未确认，无法计算人岗匹配分"
        )
    if blocked == "jd_not_confirmed":
        raise bad_request(
            ErrorCode.CANDIDATE_JD_NOT_CONFIRMED,
            "该职位 JD 尚未确认，没有权重可用于计算匹配分",
        )
    await svc.compute(db, application)
    payload = await svc.detail_payload(db, application)
    return MatchOut(**payload, blocked_reason="")


@router.post(
    "/applications/{application_id}/feedback",
    response_model=MatchFeedbackItem,
)
async def add_feedback(
    application_id: int,
    payload: MatchFeedbackIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_perm("match:view")),
) -> MatchFeedbackItem:
    """BR-22：提交评分异议。只落库 + 存快照，**不改原分数**。"""
    application, _ = await _load(db, user, application_id)
    if payload.expected_low is not None and payload.expected_high is not None:
        if payload.expected_low > payload.expected_high:
            raise bad_request(ErrorCode.MATCH_INVALID_RANGE, "期望分数区间的下限不能大于上限")
    item = await svc.add_feedback(
        db,
        user,
        application,
        kind=payload.kind,
        comment=payload.comment.strip(),
        expected_low=payload.expected_low,
        expected_high=payload.expected_high,
    )
    return MatchFeedbackItem(**item)
