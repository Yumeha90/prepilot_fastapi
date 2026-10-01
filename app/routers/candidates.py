"""候选人（PRD 3.3 第一段）。

权限：`candidate:upload_resume`（上传 / 解析 / 确认）、
`candidate:view_all` / `view_assigned`（读取，含数据范围校验）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, require_any_perm, require_perm
from app.core.errors import ErrorCode, bad_request
from app.models.candidate import Candidate
from app.models.position import Position
from app.models.session import InterviewSession
from app.models.user import User
from app.schemas.candidate import (
    CandidateConfirmIn,
    CandidateCreateIn,
    CandidateOut,
)
from app.schemas.candidate import ApplicationOut
from app.schemas.session import SessionOut
from app.services import candidate as svc
from app.services import resume_ai

router = APIRouter(prefix="/candidates", tags=["candidate"])

VIEW_PERMS = ("candidate:view_all", "candidate:view_assigned")


async def _position_names(db: AsyncSession, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    rows = await db.execute(select(Position.id, Position.name).where(Position.id.in_(ids)))
    return {r[0]: r[1] for r in rows.all()}


async def _user_name(db: AsyncSession, user_id: int | None) -> str:
    if not user_id:
        return ""
    user = await db.get(User, user_id)
    return user.full_name if user else ""


async def _sessions_of(db: AsyncSession, candidate_id: int) -> list[SessionOut]:
    """该候选人的全部会话（含面试官 / 职位 / 轮次快照）。"""
    rows = await db.scalars(
        select(InterviewSession)
        .where(InterviewSession.candidate_id == candidate_id)
        .order_by(InterviewSession.id)
    )
    sessions = list(rows)
    if not sessions:
        return []

    names = await _position_names(db, {s.position_id for s in sessions})
    users = await _user_names(db, {s.interviewer_id for s in sessions})
    return [
        SessionOut(
            id=s.id,
            application_id=s.application_id,
            candidate_id=s.candidate_id,
            candidate_name=s.candidate.name if s.candidate else "",
            position_id=s.position_id,
            position_name=names.get(s.position_id, ""),
            round_id=s.round_id,
            round_type=s.round_type,
            round_name=s.round_name,
            interviewer_id=s.interviewer_id,
            interviewer_name=users.get(s.interviewer_id, ""),
            status=s.status,
            submitted_at=s.submitted_at,
            created_at=s.created_at,
            updated_at=s.updated_at,
        )
        for s in sessions
    ]


async def _user_names(db: AsyncSession, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    rows = await db.execute(select(User.id, User.full_name).where(User.id.in_(ids)))
    return {r[0]: r[1] for r in rows.all()}


async def _out(db: AsyncSession, c: Candidate, dispatch_notice: str = "") -> CandidateOut:
    apps = list(c.applications or [])
    names = await _position_names(db, {a.position_id for a in apps})
    sessions = await _sessions_of(db, c.id)

    # 每条应聘记录的「当前轮次 / 面试官」：优先取库里已派的单
    app_outs: list[ApplicationOut] = []
    for a in apps:
        mine = [s for s in sessions if s.application_id == a.id]
        current = mine[-1] if mine else None
        app_outs.append(
            ApplicationOut(
                id=a.id,
                candidate_id=a.candidate_id,
                position_id=a.position_id,
                position_name=names.get(a.position_id, ""),
                stage=a.stage,
                current_round_name=current.round_name or current.round_type if current else "",
                interviewer_name=current.interviewer_name if current else "",
                created_at=a.created_at,
            )
        )

    return CandidateOut(
        id=c.id,
        name=c.name,
        contact_email=c.contact_email,
        contact_phone=c.contact_phone,
        source=c.source,
        resume_raw_text=c.resume_raw_text,
        resume_file_name=c.resume_file_name,
        parsed_profile=c.parsed_profile,
        profile_status=c.profile_status,
        auth_tick=c.auth_tick,
        auth_at=c.auth_at,
        confirmed_at=c.confirmed_at,
        purged_at=c.purged_at,
        created_by=c.created_by,
        created_by_name=await _user_name(db, c.created_by),
        created_at=c.created_at,
        updated_at=c.updated_at,
        applications=app_outs,
        sessions=sessions,
        dispatch_notice=dispatch_notice,
    )


@router.post("", response_model=CandidateOut)
async def create_candidate(
    payload: CandidateCreateIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_perm("candidate:upload_resume")),
) -> CandidateOut:
    """上传简历并创建应聘记录（D1 必须选职位）。

    BR-09 授权门禁、D14 邮箱唯一、D3 一人一职位都在这里拦截。
    """
    created = await svc.create_candidate(db, user, payload)
    return await _out(db, created)


@router.get("/{candidate_id}", response_model=CandidateOut)
async def get_candidate(
    candidate_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_any_perm(*VIEW_PERMS)),
) -> CandidateOut:
    candidate = await svc.get_candidate(db, candidate_id)
    await svc.ensure_can_view(db, user, candidate)
    return await _out(db, candidate)


@router.post("/{candidate_id}/confirm", response_model=CandidateOut)
async def confirm_candidate(
    candidate_id: int,
    payload: CandidateConfirmIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_perm("candidate:upload_resume")),
) -> CandidateOut:
    """确认解析结果：确认后自动派单到首轮并建会话（D5）。

    派单失败不会让确认失败（确认已经生效），只在 `dispatch_notice` 里回原因，
    前端提示 HR 去补齐流程配置。
    """
    candidate = await svc.get_candidate(db, candidate_id)
    await svc.ensure_can_view(db, user, candidate)
    if not candidate.resume_raw_text:
        raise bad_request(ErrorCode.CANDIDATE_NOT_CONFIRMED, "简历内容为空，无法确认")
    updated, notice = await svc.confirm_candidate(db, user, candidate, payload.profile)
    return await _out(db, updated, notice)


@router.post("/{candidate_id}/parse", response_model=dict)
async def reparse_candidate(
    candidate_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_perm("candidate:upload_resume")),
) -> dict:
    """对已上传的候选人重新解析，并把结果存成草稿（不推进流程）。

    落草稿是为了不重复烧 token：云端一次解析 17~23s，若像早期那样「结果不落库」，
    每进一次解析页就得重跑一遍。草稿由确认（confirm）转正，见 svc.save_parsed_profile。
    """
    candidate = await svc.get_candidate(db, candidate_id)
    await svc.ensure_can_view(db, user, candidate)
    # 先校验再调模型：一次解析云端 17~23s，跑完才发现不能写等于白烧一次 token
    svc.ensure_can_parse(candidate)
    result = await resume_ai.parse_resume(candidate.resume_raw_text)
    await svc.save_parsed_profile(db, candidate, result["profile"])
    return result
