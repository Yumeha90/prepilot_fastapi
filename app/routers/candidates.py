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
from app.models.user import User
from app.schemas.candidate import (
    CandidateConfirmIn,
    CandidateCreateIn,
    CandidateOut,
    CandidatePaged,
)
from app.schemas.candidate import ApplicationOut, CandidateListItem
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


async def _out(db: AsyncSession, c: Candidate) -> CandidateOut:
    apps = list(c.applications or [])
    names = await _position_names(db, {a.position_id for a in apps})
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
        applications=[
            ApplicationOut(
                id=a.id,
                candidate_id=a.candidate_id,
                position_id=a.position_id,
                position_name=names.get(a.position_id, ""),
                stage=a.stage,
                created_at=a.created_at,
            )
            for a in apps
        ],
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


@router.get("", response_model=CandidatePaged)
async def list_candidates(
    keyword: str = "",
    position_id: int | None = None,
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_any_perm(*VIEW_PERMS)),
) -> CandidatePaged:
    rows, total = await svc.list_candidates(
        db,
        user,
        keyword=keyword,
        position_id=position_id,
        status=status,
        page=page,
        page_size=page_size,
    )
    items: list[CandidateListItem] = []
    for c in rows:
        app = (c.applications or [None])[0]
        pos_name = ""
        if app is not None:
            pos = await db.get(Position, app.position_id)
            pos_name = pos.name if pos else ""
        items.append(
            CandidateListItem(
                id=c.id,
                name=c.name,
                contact_email=c.contact_email,
                profile_status=c.profile_status,
                position_id=app.position_id if app else None,
                position_name=pos_name,
                stage=app.stage if app else "",
                created_by_name=await _user_name(db, c.created_by),
                created_at=c.created_at,
                updated_at=c.updated_at,
            )
        )
    return CandidatePaged(items=items, total=total, page=page, page_size=page_size)


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
    """确认解析结果（BR-04 / BR-11）。

    本阶段只写档案，确认后停在 `pending`；S2 接派单、S4 接首次算分。
    """
    candidate = await svc.get_candidate(db, candidate_id)
    await svc.ensure_can_view(db, user, candidate)
    if not candidate.resume_raw_text:
        raise bad_request(ErrorCode.CANDIDATE_NOT_CONFIRMED, "简历内容为空，无法确认")
    updated = await svc.confirm_candidate(db, user, candidate, payload.profile)
    return await _out(db, updated)


@router.post("/{candidate_id}/parse", response_model=dict)
async def reparse_candidate(
    candidate_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_perm("candidate:upload_resume")),
) -> dict:
    """对已上传的候选人重新解析（不落库，供 P05 刷新右栏）。"""
    candidate = await svc.get_candidate(db, candidate_id)
    await svc.ensure_can_view(db, user, candidate)
    return await resume_ai.parse_resume(candidate.resume_raw_text)
